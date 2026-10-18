export interface StudyDocument {
  id: string
  knowledge_base_id: number
  filename: string
  file_type: string
  file_size: number
  status: 'processing' | 'embedding' | 'ready' | 'failed' | 'deleting'
  error_message: string | null
  chunk_count: number
  embedding_model: string | null
  embedding_dimension: number | null
  created_at: string
}

export interface DocumentDetail extends StudyDocument {
  source: string
  chunks: { id: string; document_id: string; ordinal: number; page: number | null; content: string; vector_id: string }[]
}
