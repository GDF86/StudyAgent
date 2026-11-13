import axios from 'axios'
import type { DocumentDetail, StudyDocument } from '../types/document'

const http = axios.create({ baseURL: import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000', timeout: 15000 })
export interface ProcessingPlan { parsed_chars:number; planned_chunks:number; embedding_requests:number; max_attempts_with_retry:number; estimated_reserve_yuan:number|null; worst_reserve_with_retry_yuan:number|null; embedding_called:boolean }

export const documentsApi = {
  preview: async (kbId:number,file:File):Promise<ProcessingPlan> => { const body=new FormData();body.append('file',file);return (await http.post(`/api/knowledge-bases/${kbId}/documents/preview`,body,{timeout:120000})).data },
  plan: async (id:string):Promise<ProcessingPlan> => (await http.get(`/api/documents/${id}/processing-plan`,{timeout:120000})).data,
  retry: async(id:string) => (await http.post<StudyDocument>(`/api/documents/${id}/retry`,{}, {headers:{'X-Confirm-Retry':'true'},timeout:0})).data,
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
  capabilities: async () => (await http.get<{local_real_mode:boolean;document_writes:boolean;document_api_version?:number;max_upload_bytes:number;max_chunks:number;embedding_batch_size:number}>('/api/runtime-capabilities')).data,
  remove: async (id: string) => { await http.delete(`/api/documents/${id}`, { headers: { 'X-Confirm-Delete': 'true' } }) },
}
