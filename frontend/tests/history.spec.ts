import { test, expect } from '@playwright/test'
const api = process.env.E2E_API_BASE_URL || 'http://127.0.0.1:8000'

test('Phase 5：会话创建、多轮摘要、刷新恢复、重生成、引用快照和删除', async ({ page, request }) => {
  const name = `历史测试-${Date.now()}`
  const kb = (await (await request.post(`${api}/api/knowledge-bases`, { data: { name } })).json()).id
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message))
  try {
    const doc = await request.post(`${api}/api/knowledge-bases/${kb}/documents`, { multipart: { file: { name: '历史资料.txt', mimeType: 'text/plain', buffer: Buffer.from('ALPHA historical source') } } })
    expect(doc.status()).toBe(201); const did = (await doc.json()).id
    await page.goto('/history')
    await page.getByLabel('会话标题', { exact: true }).fill(name)
    await page.getByLabel('会话知识库', { exact: true }).selectOption(String(kb))
    await page.getByRole('button', { name: '创建会话', exact: true }).click()
    await expect(page).toHaveURL(/conversations\//)
    const url = page.url(); const cid = url.split('/').at(-1)
    for (let i = 0; i < 4; i++) {
      if (i === 1) await page.getByLabel('流式回答', { exact: true }).uncheck()
      await page.getByLabel('学习问题或任务').fill(`ALPHA 第${i + 1}问`)
      await page.getByRole('button', { name: '发送并保存' }).click()
      await expect(page.locator('[data-role="assistant"][data-status="completed"]')).toHaveCount(i + 1)
    }
    await expect(page.getByText(/历史摘要（截至第/)).toBeVisible()
    await page.reload()
    await expect(page.locator('[data-role="assistant"][data-status="completed"]')).toHaveCount(4)
    await page.getByRole('button', { name: '重新生成最后一轮' }).click()
    await expect(page.locator('[data-role="assistant"][data-status="completed"]')).toHaveCount(5)
    expect((await (await request.get(`${api}/api/conversations/${cid}/messages`)).json()).total).toBe(10)
    expect((await request.delete(`${api}/api/documents/${did}`)).status()).toBe(204)
    await page.reload()
    await page.locator('.citation-card summary').click()
    await expect(page.locator('.citation-content')).toContainText('ALPHA historical source')
    await page.setViewportSize({ width: 390, height: 844 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
    await page.screenshot({ path: '../docs/screenshots/phase5-history.png', fullPage: true })
    await page.goto('/history')
    await page.getByLabel('筛选知识库').selectOption(String(kb))
    await page.getByRole('button', { name: '重命名', exact: true }).click()
    await page.getByLabel('新的标题').fill(`${name}-修改`)
    await page.getByRole('button', { name: '保存标题' }).click()
    await expect(page.getByRole('heading', { name: `${name}-修改` })).toBeVisible()
    await page.getByRole('button', { name: '删除会话', exact: true }).click()
    await page.getByRole('button', { name: '确认删除', exact: true }).click()
    await expect(page.getByRole('heading', { name: `${name}-修改` })).toHaveCount(0)
    expect((await request.get(`${api}/api/conversations/${cid}`)).status()).toBe(404)
    expect(errors).toEqual([])
  } finally { await request.delete(`${api}/api/knowledge-bases/${kb}`) }
})

test('Phase 5：Agent 持久化、失败保留、停止和继续会话', async ({ page, request }) => {
  const kb = (await (await request.post(`${api}/api/knowledge-bases`, { data: { name: `Agent历史-${Date.now()}` } })).json()).id
  const cid = (await (await request.post(`${api}/api/conversations`, { data: { knowledge_base_id: kb, mode: 'agent', title: 'Agent 持久化' } })).json()).id
  try {
    await page.goto(`/conversations/${cid}`)
    await page.getByLabel('学习问题或任务').fill('计算 6*7')
    await page.getByRole('button', { name: '发送并保存' }).click()
    await expect(page.locator('[data-role="assistant"][data-status="completed"]')).toContainText('42')
    await page.reload()
    await expect(page.locator('[data-role="assistant"][data-status="completed"]')).toContainText('42')
    await page.getByLabel('学习问题或任务').fill('MOCK:error')
    await page.getByRole('button', { name: '发送并保存' }).click()
    await expect(page.locator('[data-status="failed"]')).toHaveCount(1)
    await page.getByLabel('学习问题或任务').fill('MOCK:timeout')
    await page.getByRole('button', { name: '发送并保存' }).click()
    await expect.poll(async () => (await (await request.get(`${api}/api/conversations/${cid}/messages`)).json()).items.some((m: {status: string}) => m.status === 'pending')).toBeTruthy()
    await page.getByRole('button', { name: '停止生成' }).click()
    await expect.poll(async () => (await (await request.get(`${api}/api/conversations/${cid}/messages`)).json()).items.some((m: {status: string}) => m.status === 'cancelled')).toBeTruthy()
    await page.getByRole('button', { name: '刷新历史' }).click()
    await expect(page.locator('[data-status="cancelled"]')).toHaveCount(1)
    await page.getByLabel('学习问题或任务').fill('再次计算')
    await page.getByRole('button', { name: '发送并保存' }).click()
    await expect(page.locator('[data-role="assistant"][data-status="completed"]')).toHaveCount(2)
  } finally { await request.delete(`${api}/api/knowledge-bases/${kb}`) }
})

test('Phase 5：偏好保存、刷新与逐项恢复默认', async ({ page, request }) => {
  const keys = ['answer_style', 'learning_goal', 'preferred_language']
  try {
    await page.goto('/preferences')
    await page.getByLabel('回答风格').selectOption('detailed')
    await page.getByLabel('学习目标').selectOption('exam')
    await page.getByLabel('回答语言').selectOption('en')
    await page.getByRole('button', { name: '保存偏好' }).click()
    await expect(page.getByRole('status')).toContainText('偏好已保存')
    await page.reload()
    await expect(page.getByLabel('回答风格')).toHaveValue('detailed')
    await expect(page.getByLabel('学习目标')).toHaveValue('exam')
    await expect(page.getByLabel('回答语言')).toHaveValue('en')
    for (const label of ['恢复默认风格', '恢复默认目标', '恢复默认语言']) {
      await page.getByRole('button', { name: label }).click()
      await expect(page.getByRole('button', { name: '保存偏好' })).toBeEnabled()
    }
    await expect(page.getByLabel('回答风格')).toHaveValue('concise')
    await expect(page.getByLabel('学习目标')).toHaveValue('general')
    await expect(page.getByLabel('回答语言')).toHaveValue('zh-CN')
    expect((await (await request.get(`${api}/api/memory`)).json()).overrides).toEqual({})
  } finally { for (const key of keys) await request.delete(`${api}/api/memory/${key}`) }
})
