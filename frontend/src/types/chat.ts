export interface Citation {
  id: number
  knowledge_base_id: number
  document_id: string
  document_name: string
  page: number | null
  chunk_id: string
  content: string
  score: number
}

export interface ChatResponse {
  knowledge_base_id: number
  answer: string
  citations: Citation[]
  status: 'answered' | 'empty_knowledge_base' | 'no_relevant_content' | 'insufficient_evidence'
}

export interface ChatRequest {
  knowledge_base_id: number
  question: string
  conversation_id?: string
  request_id?: string
  regenerate_turn?: number
}

export type ChatEvent = { type: 'token'; content: string }
  | { type: 'citation'; data: Citation }
  | ({ type: 'done' } & ChatResponse)
  | { type: 'error'; code: string; message: string }
