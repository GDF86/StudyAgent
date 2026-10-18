import axios from 'axios'
import type { DocumentDetail, StudyDocument } from '../types/document'

const http = axios.create({ baseURL: import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000', timeout: 15000 })

export const documentsApi = {
  list: async (kbId: number) => (await http.get<StudyDocument[]>(`/api/knowledge-bases/${kbId}/documents`)).data,
  detail: async (id: string) => (await http.get<DocumentDetail>(`/api/documents/${id}`)).data,
  upload: async (kbId: number, file: File, progress: (value: number) => void) => {
    const body = new FormData()
    body.append('file', file)
    // Processing can span multiple embedding batches. The server bounds each call.
    return (await http.post<StudyDocument>(`/api/knowledge-bases/${kbId}/documents`, body, {
      timeout: 0,
      onUploadProgress: event => progress(event.total ? Math.round(event.loaded / event.total * 100) : 0),
    })).data
  },
  remove: async (id: string) => { await http.delete(`/api/documents/${id}`) },
}
