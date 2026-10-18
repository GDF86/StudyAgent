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
  remove: async (id: number) => { await http.delete(`${path}/${id}`) },
}

export function errorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    return error.response?.data?.message || '无法连接服务，请确认后端已启动后重试。'
  }
  return '操作未完成，请重试。'
}
