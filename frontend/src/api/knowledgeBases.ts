import axios from 'axios'
import type { KnowledgeBase, KnowledgeBaseInput } from '../types/knowledgeBase'

const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000',
  timeout: 15000,
})
const path = '/api/knowledge-bases'

export const knowledgeBasesApi = {
  list: async () => (await http.get<KnowledgeBase[]>(path)).data,
  get: async (id: number) => (await http.get<KnowledgeBase>(`${path}/${id}`)).data,
  create: async (body: KnowledgeBaseInput) => (await http.post<KnowledgeBase>(path, body)).data,
  update: async (id: number, body: KnowledgeBaseInput) => (await http.put<KnowledgeBase>(`${path}/${id}`, body)).data,
  remove: async (id: number) => { await http.delete(`${path}/${id}`, { headers: { 'X-Confirm-Delete': 'true' } }) },
}

export function apiErrorMessage(data: {code?: string; message?: string} | null, fallback='请求失败，请检查服务状态'): string {
  const message=data?.message || fallback
    if(data?.code==='MODEL_AUTH_REQUIRED')return `模型授权状态：${message}`
    if(data?.code==='MODEL_REAUTHORIZATION_BLOCKED')return `模型重新授权被保护机制拒绝：${message}`
  if(data?.code==='WEB_DEMO_BLOCKED' || data?.code==='REFERENCE_KB_PROTECTED')return `本机权限限制：${message}`
  if(data?.code==='LIVE_TEST_BLOCKED')return `模型费用或会话保护：${message}`
  return message
}

export function errorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    return apiErrorMessage(error.response?.data,'无法连接服务，请确认后端已启动后重试。')
  }
  return '操作未完成，请重试。'
}
