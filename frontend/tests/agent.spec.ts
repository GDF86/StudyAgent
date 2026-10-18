import { test, expect } from '@playwright/test'
const api = process.env.E2E_API_BASE_URL || 'http://127.0.0.1:8000'

test('Agent 实际五工具、连续调用、引用、状态和移动端', async ({ page, request }) => {
  const name = `Agent 测试-${Date.now()}`
  const created = await request.post(`${api}/api/knowledge-bases`, { data: { name } })
  const kb = (await created.json()).id
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  try {
    for (const [filename, content] of [['课程一.txt', 'ALPHA first document: real tool fixture.'], ['课程二.txt', 'ALPHA second document: comparison fixture.']]) {
      const upload = await request.post(`${api}/api/knowledge-bases/${kb}/documents`, { multipart: { file: { name: filename!, mimeType: 'text/plain', buffer: Buffer.from(content!) } } })
      expect(upload.status()).toBe(201)
    }
    await page.goto(`/knowledge-bases/${kb}`)
    await page.getByRole('link', { name: '打开 Agent 学习助手' }).click()
    await expect(page.getByRole('button', { name: '执行任务', exact: true })).toBeDisabled()
    await page.getByLabel('学习任务').fill('MOCK:all')
    await page.getByRole('button', { name: '执行任务', exact: true }).click()
    await expect(page.getByRole('heading', { name: '任务完成' })).toBeVisible()
    await expect(page.locator('[data-phase="tool_succeeded"]')).toHaveCount(5)
    for (const name of ['检索资料','计算','总结文档','比较文档','生成练习题']) {
      await expect(page.locator('[data-phase="tool_succeeded"]').filter({ hasText: name })).toBeVisible()
    }
    await expect(page.locator('.markdown-answer')).toContainText('42')
    await expect(page.locator('.markdown-answer')).toContainText('练习题')
    await expect(page.locator('.citation-card')).toHaveCount(2)
    await page.locator('.citation-card summary').first().click()
    await expect(page.locator('.citation-content').first()).toContainText('ALPHA')
    await expect(page.locator('body')).not.toContainText('PRIVATE_TEST_REASONING')
    await page.screenshot({ path: '../docs/screenshots/phase4-agent.png', fullPage: true })
    await page.setViewportSize({ width: 390, height: 844 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
    await page.screenshot({ path: '../docs/screenshots/phase4-mobile.png', fullPage: true })
    expect(errors).toEqual([])
  } finally { await request.delete(`${api}/api/knowledge-bases/${kb}`) }
})

test('Agent 工具失败恢复、调用上限、上游错误、停止及切库隔离', async ({ page, request }) => {
  const ids: number[] = []
  for (let i = 0; i < 2; i++) ids.push((await (await request.post(`${api}/api/knowledge-bases`, { data: { name: `Agent 异常-${Date.now()}-${i}` } })).json()).id)
  try {
    await page.goto(`/knowledge-bases/${ids[0]}/agent`)
    await page.getByLabel('学习任务').fill('MOCK:recover')
    await page.getByRole('button', { name: '执行任务', exact: true }).click()
    await expect(page.getByRole('heading', { name: '任务完成' })).toBeVisible()
    await expect(page.locator('[data-phase="tool_failed"]')).toHaveCount(1)
    await expect(page.locator('[data-phase="tool_succeeded"]')).toHaveCount(1)
    for (const [task, error] of [['MOCK:limit', '次数限制'], ['MOCK:error', '模型服务请求失败'], ['MOCK:timeout', '模型服务超时']]) {
      await page.getByLabel('学习任务').fill(task!)
      await page.getByRole('button', { name: '执行任务', exact: true }).click()
      await expect(page.getByRole('alert')).toContainText(error!)
      await expect(page.getByRole('heading', { name: '任务完成' })).toHaveCount(0)
    }
    await page.getByLabel('学习任务').fill('MOCK:timeout')
    await page.getByRole('button', { name: '执行任务', exact: true }).click()
    await page.getByRole('button', { name: '停止任务' }).click()
    await expect(page.getByRole('alert')).toContainText('已停止任务')
    await page.getByLabel('学习任务').fill('MOCK:timeout')
    await page.getByRole('button', { name: '执行任务', exact: true }).click()
    await page.getByLabel('当前知识库').selectOption(String(ids[1]))
    await expect(page.locator('.agent-status')).toHaveCount(0)
    await expect(page.getByRole('alert')).toHaveCount(0)
    await page.getByLabel('学习任务').fill('MOCK:calculator')
    await page.getByRole('button', { name: '执行任务', exact: true }).click()
    await expect(page.getByRole('heading', { name: '任务完成' })).toBeVisible()
    await expect(page.locator('.markdown-answer')).toContainText('42')
    await expect(page.locator('.citation-card')).toHaveCount(0)
  } finally { for (const id of ids) await request.delete(`${api}/api/knowledge-bases/${id}`) }
})
