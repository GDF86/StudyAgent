import { test, expect } from '@playwright/test'
import path from 'node:path'

test('文档知识库：四种文件上传、分片、重复、失败状态与删除', async ({ page, request }) => {
  const api = process.env.E2E_API_BASE_URL || 'http://127.0.0.1:8000'
  const name = `Phase2 文档测试 ${Date.now()}`
  const created = await request.post(`${api}/api/knowledge-bases`, { data: { name } })
  expect(created.status()).toBe(201)
  const kb = await created.json()
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  try {
    await page.goto(`/knowledge-bases/${kb.id}`)
    await expect(page.getByRole('heading', { name, exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: '还没有学习资料' })).toBeVisible()
    for (const filename of ['学习资料.pdf', '课程笔记.docx', '中文笔记.txt', '研究记录.md']) {
      await page.getByLabel('选择文件').setInputFiles(path.resolve('tests/fixtures', filename))
      const response = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith('/documents'))
      await page.getByRole('button', { name: '上传并处理' }).click()
      expect((await response).status()).toBe(201)
      const card = page.getByRole('article').filter({ has: page.getByRole('heading', { name: filename, exact: true }) })
      await expect(card.getByText('已就绪', { exact: true })).toBeVisible()
    }
    await page.reload()
    await expect(page.getByRole('article')).toHaveCount(4)
    await page.getByRole('button', { name: '查看 学习资料.pdf', exact: true }).click()
    await expect(page.getByText(/分片 1 · 第 1 页/)).toBeVisible()
    await page.getByText(/分片 1 · 第 1 页/).click()
    await expect(page.getByText('StudyAgent PDF ingestion: page one.', { exact: true })).toBeVisible()
    await page.screenshot({ path: '../docs/screenshots/phase2-documents.png', fullPage: true })
    await page.getByLabel('选择文件').setInputFiles(path.resolve('tests/fixtures', '学习资料.pdf'))
    await page.getByRole('button', { name: '上传并处理' }).click()
    await expect(page.getByRole('alert')).toContainText('该文件已上传')
    await page.getByLabel('选择文件').setInputFiles({ name: '损坏文件.pdf', mimeType: 'application/pdf', buffer: Buffer.from('invalid pdf') })
    await page.getByRole('button', { name: '上传并处理' }).click()
    await expect(page.getByRole('alert')).toContainText('文档解析失败')
    await expect(page.getByText('处理失败', { exact: true })).toBeVisible()
    await page.setViewportSize({ width: 390, height: 844 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
    await page.screenshot({ path: '../docs/screenshots/phase2-mobile.png', fullPage: true })
    await page.getByRole('button', { name: '删除 损坏文件.pdf', exact: true }).click()
    await page.getByRole('button', { name: '取消删除', exact: true }).click()
    await expect(page.getByRole('article')).toHaveCount(5)
    await page.getByRole('button', { name: '删除 损坏文件.pdf', exact: true }).click()
    await page.getByRole('button', { name: '确认删除文档' }).click()
    await expect(page.getByRole('article')).toHaveCount(4)
    await page.getByRole('button', { name: '删除 中文笔记.txt', exact: true }).click()
    await page.getByRole('button', { name: '确认删除文档' }).click()
    await expect(page.getByRole('article')).toHaveCount(3)
    const records = await (await request.get(`${api}/api/knowledge-bases/${kb.id}/documents`)).json()
    expect(records.every((item: { status: string; chunk_count: number }) => item.status === 'ready' && item.chunk_count > 0)).toBeTruthy()
    expect(errors).toEqual([])
  } finally {
    expect((await request.delete(`${api}/api/knowledge-bases/${kb.id}`)).status()).toBe(204)
  }
})
