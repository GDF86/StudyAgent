import { defineStore } from 'pinia'
import { ref } from 'vue'
import { errorMessage, knowledgeBasesApi } from '../api/knowledgeBases'
import type { KnowledgeBase, KnowledgeBaseInput } from '../types/knowledgeBase'

export const useKnowledgeBases = defineStore('knowledgeBases', () => {
  const items = ref<KnowledgeBase[]>([])
  const loading = ref(false)
  const error = ref('')

  async function load() {
    loading.value = true
    error.value = ''
    try { items.value = await knowledgeBasesApi.list() }
    catch (cause) { error.value = errorMessage(cause) }
    finally { loading.value = false }
  }

  async function save(body: KnowledgeBaseInput, id?: number) {
    const saved = id === undefined
      ? await knowledgeBasesApi.create(body)
      : await knowledgeBasesApi.update(id, body)
    items.value = [saved, ...items.value.filter(item => item.id !== saved.id)]
    error.value = ''
  }

  async function remove(id: number) {
    await knowledgeBasesApi.remove(id)
    items.value = items.value.filter(item => item.id !== id)
    error.value = ''
  }

  return { items, loading, error, load, save, remove }
})
