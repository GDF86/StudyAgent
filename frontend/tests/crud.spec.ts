import { test, expect } from '@playwright/test'

test('真实 UI 创建、重载、编辑、取消删除和删除', async ({ page, request }) => {
  const pageErrors: string[] = []
  page.on('pageerror', error => pageErrors.push(error.message))
  const name = `浏览器测试-${Date.now()}`
  const renamed = `${name}-已编辑`
  const api = process.env.E2E_API_BASE_URL || 'http://127.0.0.1:8000'
  let id: number | undefined
  try {
    await page.goto('/')
    await expect(page.getByRole('heading', { name: '让知识，有处安放。' })).toBeVisible()
    await expect(page.getByRole('status')).toHaveCount(0)
    await page.screenshot({ path: '../docs/screenshots/home.png', fullPage: true })
    await page.getByRole('link', { name: '＋ 创建知识库' }).click()
    await page.getByLabel('知识库名称').fill(name)
    await page.getByLabel('描述').fill('真实浏览器调用 FastAPI，并存入 SQLite')
    const created = page.waitForResponse(response => response.url().endsWith('/api/knowledge-bases') && response.request().method() === 'POST')
    await page.getByRole('button', { name: '保存知识库' }).click()
    const response = await created
    expect(response.status()).toBe(201)
    id = (await response.json()).id
    await expect(page.getByRole('heading', { name, exact: true })).toBeVisible()
    await page.screenshot({ path: '../docs/screenshots/knowledge-bases.png', fullPage: true })
    await page.getByRole('button', { name: '＋ 创建知识库' }).click()
    await expect(page.getByRole('button', { name: '保存知识库' })).toBeDisabled()
    await page.getByLabel('知识库名称').fill(name)
    await page.getByRole('button', { name: '保存知识库' }).click()
    await expect(page.getByRole('alert')).toContainText('知识库名称已存在')
    await page.getByRole('button', { name: '取消', exact: true }).click()
    await page.reload()
    await expect(page.getByRole('heading', { name, exact: true })).toBeVisible()
    await page.getByRole('button', { name: `编辑 ${name}`, exact: true }).click()
    await page.getByLabel('知识库名称').fill(renamed)
    await page.screenshot({ path: '../docs/screenshots/edit.png', fullPage: true })
    await page.getByRole('button', { name: '保存知识库' }).click()
    await expect(page.getByRole('heading', { name: renamed, exact: true })).toBeVisible()
    const detail = await request.get(`${api}/api/knowledge-bases/${id}`)
    expect(detail.status()).toBe(200)
    expect((await detail.json()).name).toBe(renamed)
    await page.getByRole('button', { name: `删除 ${renamed}`, exact: true }).click()
    await page.screenshot({ path: '../docs/screenshots/delete-confirm.png', fullPage: true })
    await page.getByRole('button', { name: '取消删除', exact: true }).click()
    await expect(page.getByRole('heading', { name: renamed, exact: true })).toBeVisible()
    await page.setViewportSize({ width: 390, height: 844 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
    await page.screenshot({ path: '../docs/screenshots/mobile.png', fullPage: true })
    await page.getByRole('button', { name: `删除 ${renamed}`, exact: true }).click()
    await page.getByRole('button', { name: '确认删除', exact: true }).click()
    await expect(page.getByRole('heading', { name: renamed, exact: true })).toHaveCount(0)
    expect((await request.get(`${api}/api/knowledge-bases/${id}`)).status()).toBe(404)
    expect(pageErrors).toEqual([])
  } finally {
    if (id !== undefined) await request.delete(`${api}/api/knowledge-bases/${id}`)
  }
})
