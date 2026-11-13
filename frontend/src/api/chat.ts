import type { ChatEvent, ChatRequest, ChatResponse } from '../types/chat'
import { apiErrorMessage } from './knowledgeBases'

const base = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')

async function request(body: ChatRequest, streaming: boolean, signal: AbortSignal) {
  const response = await fetch(`${base}/api/chat${streaming ? '/stream' : ''}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal,
  })
  if (!response.ok) {
    const error = await response.json().catch(() => null)
    throw new Error(apiErrorMessage(error,`请求失败（${response.status}），请重试`))
  }
  return response
}

export async function answerQuestion(body: ChatRequest, signal: AbortSignal): Promise<ChatResponse> {
  return (await request(body, false, signal)).json()
}

/** Parse UTF-8 SSE across arbitrary network boundaries, including CRLF/multiline data. */
export async function readChatEvents(response: Response, onEvent: (event: ChatEvent) => void): Promise<void> {
  if (!response.body || !response.headers.get('content-type')?.includes('text/event-stream')) throw new Error('服务器未返回流式响应')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let done = false

  function dispatch(frame: string) {
    const data = frame.split(/\r?\n/).filter(line => line.startsWith('data:')).map(line => line.slice(5).replace(/^ /, '')).join('\n')
    if (!data) return
    const event = JSON.parse(data) as ChatEvent
    if (event.type === 'error') throw new Error(event.message || '回答生成失败，请重试')
    if (!['token', 'citation', 'done'].includes(event.type)) throw new Error('流式响应格式无效')
    onEvent(event)
    if (event.type === 'done') done = true
  }

  try {
    while (!done) {
      const next = await reader.read()
      buffer += decoder.decode(next.value, { stream: !next.done })
      if (buffer.length > 1024 * 1024) throw new Error('流式数据超过限制')
      let match = /\r?\n\r?\n/.exec(buffer)
      while (match && !done) {
        dispatch(buffer.slice(0, match.index))
        buffer = buffer.slice(match.index + match[0].length)
        match = /\r?\n\r?\n/.exec(buffer)
      }
      if (next.done) break
    }
    if (!done) throw new Error('连接提前中断，回答尚未完成，请重新生成')
  } finally {
    await reader.cancel().catch(() => undefined)
    reader.releaseLock()
  }
}

export async function streamQuestion(body: ChatRequest, signal: AbortSignal, onEvent: (event: ChatEvent) => void) {
  await readChatEvents(await request(body, true, signal), onEvent)
}
