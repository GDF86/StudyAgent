import { test, expect } from '@playwright/test'
const api = process.env.E2E_API_BASE_URL || 'http://127.0.0.1:8000'
async function createKb(request: any, name: string) {
  const response = await request.post(`${api}/api/knowledge-bases`, { data: { name: `${name}-${Date.now()}` } })
  expect(response.status()).toBe(201); return (await response.json()).id as number
}
async function createConversation(request: any, kb: number, title: string, mode = 'chat') {
  const response = await request.post(`${api}/api/conversations`, { data: { knowledge_base_id: kb, title, mode } })
  expect(response.status()).toBe(201); return (await response.json()).id as string
}

test('Phase 6：真实历史列表分页和超过一百条消息恢复', async ({ page, request }) => {
  test.setTimeout(90000)
  const kb = await createKb(request, '分页')
  try {
    const ids: string[] = []
    for (let i = 0; i < 21; i++) ids.push(await createConversation(request, kb, `分页-${i}`, i === 20 ? 'agent' : 'chat'))
    await page.goto(`/history?knowledge_base_id=${kb}`)
    await expect(page.locator('.history-item')).toHaveCount(20)
    await page.getByRole('button', { name: '下一页' }).click()
    await expect(page.locator('.history-item')).toHaveCount(1)
    await expect(page.locator('.history-item')).toContainText('分页-0')
    await page.getByRole('button', { name: '上一页' }).click()
    await expect(page.locator('.history-item')).toHaveCount(20)
    await page.getByLabel('筛选模式').selectOption('agent')
    await expect(page.locator('.history-item')).toHaveCount(1)
    await expect(page.locator('.history-item')).toContainText('分页-20')
    // Empty-KB replies exercise the real API/DB cheaply without fake message endpoints.
    for (let i = 0; i < 51; i++) {
      const result = await request.post(`${api}/api/chat`, { data: { knowledge_base_id: kb, conversation_id: ids[0], request_id: crypto.randomUUID(), question: `问题-${i}` } })
      expect(result.status()).toBe(200)
      expect((await result.json()).status).toBe('empty_knowledge_base')
    }
    await page.goto(`/conversations/${ids[0]}`)
    await expect(page.locator('[data-role]')).toHaveCount(102)
    await expect(page.locator('[data-role="user"]').first()).toContainText('问题-0')
    await expect(page.locator('[data-role="user"]').last()).toContainText('问题-50')
    await page.reload()
    await expect(page.locator('[data-role]')).toHaveCount(102)
  } finally { await request.delete(`${api}/api/knowledge-bases/${kb}`) }
})

test('Phase 6：切换会话取消旧历史请求且旧失败不污染新页面', async ({ page, request }) => {
  const kb = await createKb(request, '旧响应')
  const oldId = await createConversation(request, kb, '旧会话')
  const newId = await createConversation(request, kb, '新会话')
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  const oldReads: import('@playwright/test').Request[] = []
  const aborted: string[] = []
  page.on('requestfailed', req => { if (req.url().includes(oldId)) aborted.push(req.url()) })
  try {
    await page.goto(`/conversations/${oldId}`)
    await expect(page.getByRole('heading', { name: '旧会话', exact: true })).toBeVisible()
    await page.route(`${api}/api/conversations/${oldId}`, async route => {
      if (route.request().method() !== 'GET') return route.continue()
      oldReads.push(route.request()); await gate
      await route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ message: 'OLD_REQUEST_FAILURE' }) }).catch(() => undefined)
    })
    await page.getByRole('button', { name: '刷新历史' }).click()
    await expect.poll(() => oldReads.length).toBe(1)
    // Use the real SPA router to reuse ConversationView, preserving the stale request.
    await page.evaluate(async id => {
      const path = '/src/router/index.ts'; const { default: router } = await import(path)
      await router.push(`/conversations/${id}`)
    }, newId)
    await expect(page.getByRole('heading', { name: '新会话', exact: true })).toBeVisible()
    release()
    await expect.poll(() => aborted.length).toBe(1)
    await expect(page.getByRole('alert')).toHaveCount(0)
    await expect(page.getByRole('heading', { name: '新会话', exact: true })).toBeVisible()
  } finally { release(); await request.delete(`${api}/api/knowledge-bases/${kb}`) }
})

test('Phase 6：偏好部分保存失败和恢复默认失败展示实际状态', async ({ page, request }) => {
  const keys = ['answer_style', 'learning_goal', 'preferred_language']
  for (const key of keys) await request.delete(`${api}/api/memory/${key}`)
  try {
    await page.goto('/preferences')
    await page.getByLabel('回答风格').selectOption('detailed')
    await page.getByLabel('学习目标').selectOption('exam')
    await page.getByLabel('回答语言').selectOption('en')
    await page.route(`${api}/api/memory/learning_goal`, async route => {
      if (route.request().method() === 'PUT') await route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ message: '测试：偏好保存失败' }) })
      else await route.continue()
    })
    await page.getByRole('button', { name: '保存偏好' }).click()
    await expect(page.getByRole('alert')).toContainText('偏好保存失败')
    await expect(page.getByLabel('回答风格')).toHaveValue('detailed')
    await expect(page.getByLabel('学习目标')).toHaveValue('general')
    await expect(page.getByLabel('回答语言')).toHaveValue('zh-CN')
    expect((await (await request.get(`${api}/api/memory`)).json()).overrides).toEqual({ answer_style: 'detailed' })
    await expect(page.getByText('偏好已保存', { exact: false })).toHaveCount(0)
    await page.route(`${api}/api/memory/answer_style`, async route => {
      if (route.request().method() === 'DELETE') await route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ message: '测试：恢复默认失败' }) })
      else await route.continue()
    })
    await page.getByRole('button', { name: '恢复默认风格' }).click()
    await expect(page.getByRole('alert')).toContainText('恢复默认失败')
    await expect(page.getByLabel('回答风格')).toHaveValue('detailed')
    expect((await (await request.get(`${api}/api/memory`)).json()).overrides).toEqual({ answer_style: 'detailed' })
  } finally { for (const key of keys) await request.delete(`${api}/api/memory/${key}`) }
})

test('Phase 6：服务端已保存但 done 丢失，刷新和相同请求重试不重复保存', async ({ page, request }) => {
  const kb = await createKb(request, '丢失done')
  const cid = await createConversation(request, kb, '恢复已保存回答')
  let sent: any
  try {
    expect((await request.post(`${api}/api/knowledge-bases/${kb}/documents`, { multipart: { file: { name: '来源.txt', mimeType: 'text/plain', buffer: Buffer.from('ALPHA phase6 source') } } })).status()).toBe(201)
    await page.route(`${api}/api/chat/stream`, async route => {
      sent = route.request().postDataJSON()
      const response = await route.fetch()
      const frames = (await response.text()).split(/\r?\n\r?\n/)
      const retained = frames.filter(frame => !frame.includes('"type": "done"') && !frame.includes('"type":"done"')).join('\n\n')
      await route.fulfill({ status: 200, contentType: 'text/event-stream', body: retained })
    })
    await page.goto(`/conversations/${cid}`)
    await page.getByLabel('学习问题或任务').fill('ALPHA')
    await page.getByRole('button', { name: '发送并保存' }).click()
    await expect(page.getByRole('alert')).toContainText('连接提前中断')
    await expect(page.locator('[data-role="assistant"][data-status="completed"]')).toHaveCount(1)
    const replay = await request.post(`${api}/api/chat`, { data: sent })
    expect(replay.status()).toBe(200)
    expect((await (await request.get(`${api}/api/conversations/${cid}/messages`)).json()).total).toBe(2)
    await page.reload()
    await expect(page.locator('[data-role="assistant"][data-status="completed"]')).toHaveCount(1)
  } finally { await request.delete(`${api}/api/knowledge-bases/${kb}`) }
})

test('Phase 6：持久化 Chat 部分流式内容取消后不保存为完整答案', async ({ page, request }) => {
  const kb = await createKb(request, '断流')
  const cid = await createConversation(request, kb, '部分流式取消')
  try {
    expect((await request.post(`${api}/api/knowledge-bases/${kb}/documents`, { multipart: { file: { name: '来源.txt', mimeType: 'text/plain', buffer: Buffer.from('ALPHA source') } } })).status()).toBe(201)
    await page.goto(`/conversations/${cid}`)
    await page.getByLabel('学习问题或任务').fill('ALPHA PHASE6:SLOW')
    await page.getByRole('button', { name: '发送并保存' }).click()
    await expect(page.locator('section[role="status"] .markdown-answer')).toBeVisible()
    await page.getByRole('button', { name: '停止生成' }).click()
    await expect.poll(async () => (await (await request.get(`${api}/api/conversations/${cid}/messages`)).json()).items[1]?.status).toBe('cancelled')
    const rows = (await (await request.get(`${api}/api/conversations/${cid}/messages`)).json()).items
    expect(rows).toHaveLength(2); expect(rows[1].content).toBe(''); expect(rows[1].citations).toEqual([])
    await page.reload()
    await expect(page.locator('[data-status="cancelled"]')).toHaveCount(1)
    await page.getByLabel('学习问题或任务').fill('ALPHA 重试')
    await page.getByRole('button', { name: '发送并保存' }).click()
    await expect(page.locator('[data-role="assistant"][data-status="completed"]')).toHaveCount(1)
  } finally { await request.delete(`${api}/api/knowledge-bases/${kb}`) }
})
