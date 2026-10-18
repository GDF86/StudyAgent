import type { Citation } from '../types/chat'
export type ConversationMode = 'chat' | 'agent'
export interface Conversation { id: string; knowledge_base_id: number; title: string; mode: ConversationMode; created_at: string; updated_at: string; summary: string; summary_through_turn: number }
export interface HistoryMessage { id: string; conversation_id: string; role: 'user' | 'assistant'; content: string; turn_no: number; attempt_no: number; request_id: string | null; status: 'pending' | 'completed' | 'failed' | 'cancelled' | 'interrupted'; citations: Citation[]; error_code: string | null }
export interface Page<T> { items: T[]; total: number; limit: number; offset: number }
export interface Preferences { answer_style: 'concise' | 'detailed'; learning_goal: 'general' | 'exam' | 'research'; preferred_language: 'zh-CN' | 'en' }
export interface PreferenceState { preferences: Preferences; defaults: Preferences; overrides: Partial<Preferences> }
const base = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')
async function request<T>(path: string, method = 'GET', data?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(base + path, { method, headers: { 'Content-Type': 'application/json' }, body: data === undefined ? undefined : JSON.stringify(data), signal })
  if (!response.ok) { const error = await response.json().catch(() => null); throw new Error(error?.message || `请求失败（${response.status}）`) }
  return response.status === 204 ? undefined as T : response.json()
}
export const historyApi = {
  list: (query: URLSearchParams) => request<Page<Conversation>>(`/api/conversations?${query}`),
  create: (knowledge_base_id: number, title: string, mode: ConversationMode) => request<Conversation>('/api/conversations', 'POST', { knowledge_base_id, title, mode }),
  get: (id: string, signal?: AbortSignal) => request<Conversation>(`/api/conversations/${id}`, 'GET', undefined, signal),
  messages: (id: string, offset = 0, limit = 100, signal?: AbortSignal) => request<Page<HistoryMessage>>(`/api/conversations/${id}/messages?offset=${offset}&limit=${limit}`, 'GET', undefined, signal),
  rename: (id: string, title: string) => request<Conversation>(`/api/conversations/${id}`, 'PUT', { title }),
  remove: (id: string) => request<void>(`/api/conversations/${id}`, 'DELETE'),
  cancel: (id: string, requestId: string) => request<void>(`/api/conversations/${id}/requests/${requestId}/cancel`, 'POST'),
}
export const preferencesApi = {
  get: () => request<PreferenceState>('/api/memory'),
  set: (key: keyof Preferences, value: string) => request<PreferenceState>(`/api/memory/${key}`, 'PUT', { value }),
  reset: (key: keyof Preferences) => request<void>(`/api/memory/${key}`, 'DELETE'),
}
export function describeError(error: unknown) { return error instanceof Error ? error.message : '操作失败，请重试' }
