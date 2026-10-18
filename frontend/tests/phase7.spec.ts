import { test, expect } from '@playwright/test'
const api = process.env.E2E_API_BASE_URL || 'http://127.0.0.1:8000'

test('Phase 7：真实基础与重排对比、来源原文与移动布局', async ({ page, request }) => {
  const created = await request.post(`${api}/api/knowledge-bases`, { data: { name: `Phase7-${Date.now()}` } })
  expect(created.status()).toBe(201); const kb = (await created.json()).id
  try {
    for (const [name, content] of [['target.txt', 'ALPHA telescope lens clean soft cloth'], ['other.txt', 'ALPHA battery storage room']]) {
      const result = await request.post(`${api}/api/knowledge-bases/${kb}/documents`, { multipart: { file: { name, mimeType: 'text/plain', buffer: Buffer.from(content) } } })
      expect(result.status()).toBe(201)
    }
    await page.goto('/comparison')
    await page.getByLabel('知识库', { exact: true }).selectOption(String(kb))
    await page.getByLabel('问题', { exact: true }).fill('ALPHA telescope lens')
    await page.getByLabel('返回条数').fill('1')
    await page.getByRole('button', { name: '开始对比' }).click()
    await expect(page.locator('.comparison-result')).toHaveCount(2)
    const ranked = page.locator('[data-mode="reranked"]')
    await expect(ranked).toContainText('target.txt')
    await expect(ranked.locator('[role="alert"]')).toHaveCount(0)
    await ranked.getByText('查看来源原文').click()
    await expect(ranked).toContainText('ALPHA telescope lens clean soft cloth')
    await expect(ranked).toContainText('不是重排分数')
    await page.setViewportSize({ width: 390, height: 844 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await page.screenshot({ path: '../docs/screenshots/phase7-comparison-mobile.png', fullPage: true })
    await page.setViewportSize({ width: 1440, height: 1000 })
    await page.screenshot({ path: '../docs/screenshots/phase7-comparison.png', fullPage: true })
  } finally { expect((await request.delete(`${api}/api/knowledge-bases/${kb}`)).status()).toBe(204) }
})

test('Phase 7：空库提示与对比请求失败恢复', async ({ page, request }) => {
  const created = await request.post(`${api}/api/knowledge-bases`, { data: { name: `Phase7-empty-${Date.now()}` } })
  expect(created.status()).toBe(201); const kb = (await created.json()).id
  try {
    await page.goto('/comparison')
    await page.getByLabel('知识库', { exact: true }).selectOption(String(kb))
    await page.getByLabel('问题', { exact: true }).fill('ALPHA')
    await page.route(`${api}/api/rag/compare`, route => route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ message: 'Injected comparison failure' }) }))
    await page.getByRole('button', { name: '开始对比' }).click()
    await expect(page.getByRole('alert')).toContainText('Injected comparison failure')
    await expect(page.getByRole('button', { name: '开始对比' })).toBeEnabled()
    await page.unroute(`${api}/api/rag/compare`)
    await page.getByRole('button', { name: '开始对比' }).click()
    await expect(page.locator('.comparison-result')).toHaveCount(2)
    await expect(page.getByText('没有符合阈值的已就绪资料。')).toHaveCount(2)
    await expect(page.getByRole('alert')).toHaveCount(0)
  } finally { await request.delete(`${api}/api/knowledge-bases/${kb}`) }
})
