import { test, expect } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const api = process.env.E2E_API_BASE_URL || 'http://127.0.0.1:8000'

test('RAG 页面：SSE、引用页码、普通回答、重新生成、知识库隔离与清空', async ({ page, request }) => {
  const kbIds: number[] = []
  const baseName = `问答测试-${Date.now()}`
  for (const name of [baseName, `${baseName}-空库`]) {
    const response = await request.post(`${api}/api/knowledge-bases`, { data: { name } })
    expect(response.status()).toBe(201)
    kbIds.push((await response.json()).id)
  }
  const [kb, emptyKb] = kbIds
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  try {
    const uploaded = await request.post(`${api}/api/knowledge-bases/${kb}/documents`, {
      multipart: { file: { name: '问答课程.pdf', mimeType: 'application/pdf', buffer: fs.readFileSync(path.resolve('tests/fixtures/问答课程.pdf')) } },
    })
    expect(uploaded.status()).toBe(201)
    await page.goto(`/knowledge-bases/${kb}/chat`)
    await expect(page.getByRole('heading', { name: baseName, exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: '发送问题' })).toBeDisabled()
    await page.getByLabel('输入问题').fill('解释 ALPHA')
    await page.getByRole('button', { name: '发送问题' }).click()
    await expect(page.getByText('正在检索资料并生成回答…')).toBeVisible()
    await expect(page.getByText('回答完成', { exact: true })).toBeVisible()
    await expect(page.locator('.markdown-answer pre code')).toContainText("print('资料示例')")
    await page.locator('.citation-card summary').click()
    await expect(page.locator('.citation-content')).toContainText('ALPHA source on page one')
    await expect(page.locator('.citation-card summary')).toContainText('第 1 页')
    await page.screenshot({ path: '../docs/screenshots/phase3-chat.png', fullPage: true })
    await page.getByRole('button', { name: '重新生成', exact: true }).click()
    await expect(page.getByText('回答完成', { exact: true })).toBeVisible()
    await expect(page.locator('.exchange')).toHaveCount(1)
    await page.getByLabel('使用流式回答').uncheck()
    await page.getByLabel('输入问题').fill('解释 BETA')
    await page.getByRole('button', { name: '发送问题' }).click()
    await expect(page.locator('.exchange').last()).toContainText('BETA source on page two')
    await expect(page.locator('.citation-card summary')).toContainText('第 2 页')
    await page.setViewportSize({ width: 390, height: 844 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
    await page.screenshot({ path: '../docs/screenshots/phase3-mobile.png', fullPage: true })
    await page.getByLabel('当前知识库').selectOption(String(emptyKb))
    await expect(page.locator('.exchange')).toHaveCount(0)
    await page.getByLabel('输入问题').fill('解释 ALPHA')
    await page.getByRole('button', { name: '发送问题' }).click()
    await expect(page.locator('.markdown-answer')).toContainText('没有已就绪的文档')
    await expect(page.locator('.citation-card')).toHaveCount(0)
    await page.getByRole('button', { name: '清空对话', exact: true }).click()
    await expect(page.locator('.exchange')).toHaveCount(0)
    await page.getByLabel('当前知识库').selectOption(String(kb))
    await page.getByLabel('输入问题').fill('NO_MATCH')
    await page.getByRole('button', { name: '发送问题' }).click()
    await expect(page.locator('.markdown-answer')).toContainText('未检索到足够相关')
    await expect(page.locator('.citation-card')).toHaveCount(0)
    expect(errors).toEqual([])
  } finally {
    for (const id of kbIds) await request.delete(`${api}/api/knowledge-bases/${id}`)
  }
})

test('RAG 错误提示、无效引用拒绝、Markdown 安全和切库取消', async ({ page, request }) => {
  const response = await request.post(`${api}/api/knowledge-bases`, { data: { name: `异常问答-${Date.now()}` } })
  const kb = (await response.json()).id
  try {
    expect((await request.post(`${api}/api/knowledge-bases/${kb}/documents`, { multipart: {
      file: { name: '测试原文.txt', mimeType: 'text/plain', buffer: Buffer.from('ALPHA source for browser error tests') },
    } })).status()).toBe(201)
    await page.goto(`/knowledge-bases/${kb}/chat`)
    for (const [question, error] of [['ALPHA 超时测试', '模型服务超时'], ['ALPHA 异常测试', '模型服务请求失败'], ['ALPHA 无效引用测试', '缺少有效引用']]) {
      await page.getByLabel('输入问题').fill(question!)
      await page.getByRole('button', { name: '发送问题' }).click()
      await expect(page.locator('.exchange').last().getByRole('alert')).toContainText(error!, { timeout: 10000 })
      await expect(page.locator('.exchange').last().locator('.markdown-answer')).toHaveCount(0)
    }
    await page.getByLabel('输入问题').fill('ALPHA 恶意内容测试')
    await page.getByRole('button', { name: '发送问题' }).click()
    await expect(page.locator('.exchange').last()).toContainText('回答完成')
    await expect(page.locator('.markdown-answer script, .markdown-answer img, .markdown-answer a[href^="javascript:"]')).toHaveCount(0)
    expect(await page.evaluate(() => '__xss' in window)).toBeFalsy()
    await page.getByLabel('输入问题').fill('ALPHA 超时测试')
    await page.getByRole('button', { name: '发送问题' }).click()
    await page.getByRole('button', { name: '停止生成' }).click()
    await expect(page.locator('.exchange').last()).toContainText('已停止生成')
    await page.getByRole('button', { name: '清空对话', exact: true }).click()
    await expect(page.locator('.exchange')).toHaveCount(0)
  } finally {
    await request.delete(`${api}/api/knowledge-bases/${kb}`)
  }
})

test('SSE 客户端按字节切分仍保留中文、CRLF 与结束事件，缺少 done 会报错', async ({ page }) => {
  await page.goto('/')
  const result = await page.evaluate(async () => {
    const modulePath = '/src/api/chat.ts'
    const { readChatEvents } = await import(modulePath)
    const text = ': heartbeat\r\n\r\ndata: {"type":"token","content":"中文片段"}\r\n\r\ndata: {"type":"done","answer":"中文片段"}\r\n\r\n'
    const bytes = new TextEncoder().encode(text)
    const received: { type: string; content?: string }[] = []
    const body = new ReadableStream({ start(controller) { for (const byte of bytes) controller.enqueue(Uint8Array.of(byte)); controller.close() } })
    await readChatEvents(new Response(body, { headers: { 'content-type': 'text/event-stream' } }), (event: { type: string; content?: string }) => received.push(event))
    let error = ''
    try {
      await readChatEvents(new Response('data: {"type":"token","content":"未完成"}\n\n', { headers: { 'content-type': 'text/event-stream' } }), () => undefined)
    } catch (cause) { error = (cause as Error).message }
    return { received, error }
  })
  expect(result.received[0]?.content).toBe('中文片段')
  expect(result.received[1]?.type).toBe('done')
  expect(result.error).toContain('连接提前中断')
})
