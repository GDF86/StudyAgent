export interface KnowledgeBase {
  id: number
  name: string
  description: string
  created_at: string
  updated_at: string
}

export type KnowledgeBaseInput = Pick<KnowledgeBase, 'name' | 'description'>
