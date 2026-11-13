import type { Citation } from '../types/chat'
import { apiErrorMessage } from './knowledgeBases'

export interface AgentResult { knowledge_base_id: number; answer: string; citations: Citation[]; tool_calls: number }
export interface AgentStatus { type: 'status'; phase: 'model' | 'tool_started' | 'tool_succeeded' | 'tool_failed'; tool?: string; call?: number; round?: number; message: string }
type AgentEvent = AgentStatus | ({ type: 'done' } & AgentResult) | { type: 'error'; message: string }
const base = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')

export async function runAgent(knowledge_base_id: number, task: string, signal: AbortSignal, onStatus: (event: AgentStatus) => void, session?: { conversation_id: string; request_id: string; regenerate_turn?: number }): Promise<AgentResult> {
  const response = await fetch(`${base}/api/agent/stream`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ knowledge_base_id, task, ...session }), signal })
  if (!response.ok) { const error = await response.json().catch(() => null); throw new Error(apiErrorMessage(error,'Agent 请求失败')) }
  if (!response.body || !response.headers.get('content-type')?.includes('text/event-stream')) throw new Error('服务器未返回流式响应')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      const next = await reader.read()
      buffer += decoder.decode(next.value, { stream: !next.done })
      if (buffer.length > 1024 * 1024) throw new Error('Agent 响应超过限制')
      let match = /\r?\n\r?\n/.exec(buffer)
      while (match) {
        const frame = buffer.slice(0, match.index)
        buffer = buffer.slice(match.index + match[0].length)
        const raw = frame.split(/\r?\n/).filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n')
        if (raw) {
          const event = JSON.parse(raw) as AgentEvent
          if (event.type === 'error') throw new Error(event.message)
          if (event.type === 'done') {
            if (typeof event.answer !== 'string' || !Number.isInteger(event.tool_calls) || event.tool_calls < 0) throw new Error('Agent 完成事件格式无效')
            if (event.knowledge_base_id !== knowledge_base_id || !Array.isArray(event.citations) || event.citations.some(c => c.knowledge_base_id !== knowledge_base_id)) throw new Error('结果知识库不匹配')
            return event
          }
          if (event.type !== 'status') throw new Error('Agent 响应格式无效')
          onStatus(event)
        }
        match = /\r?\n\r?\n/.exec(buffer)
      }
      if (next.done) throw new Error('连接提前中断，任务尚未完成')
    }
  } finally { await reader.cancel().catch(() => undefined); reader.releaseLock() }
}
