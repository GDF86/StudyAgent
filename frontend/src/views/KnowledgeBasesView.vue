<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import KnowledgeBaseCard from '../components/KnowledgeBaseCard.vue'
import KnowledgeBaseForm from '../components/KnowledgeBaseForm.vue'
import { errorMessage } from '../api/knowledgeBases'
import { useKnowledgeBases } from '../stores/knowledgeBases'
import type { KnowledgeBase, KnowledgeBaseInput } from '../types/knowledgeBase'

const store = useKnowledgeBases()
const route = useRoute()
const router = useRouter()
const showForm = ref(route.query.create === '1')
const editing = ref<KnowledgeBase>()
const deleting = ref<KnowledgeBase>()
const busy = ref(false)
const formError = ref('')
const deleteError = ref('')
const notice = ref('')
onMounted(() => store.load())

function openForm(item?: KnowledgeBase) {
  editing.value = item
  deleting.value = undefined
  formError.value = ''
  notice.value = ''
  showForm.value = true
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function closeForm() {
  showForm.value = false
  editing.value = undefined
  if (route.query.create) void router.replace('/knowledge-bases')
}

async function save(body: KnowledgeBaseInput) {
  busy.value = true
  formError.value = ''
  try {
    await store.save(body, editing.value?.id)
    notice.value = editing.value ? '知识库已更新' : '知识库已创建'
    closeForm()
  } catch (error) { formError.value = errorMessage(error) }
  finally { busy.value = false }
}

function confirmDelete(item: KnowledgeBase) {
  closeForm()
  deleting.value = item
  deleteError.value = ''
  notice.value = ''
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

async function remove() {
  if (!deleting.value || busy.value) return
  busy.value = true
  deleteError.value = ''
  try {
    await store.remove(deleting.value.id)
    deleting.value = undefined
    notice.value = '知识库已删除'
  } catch (error) { deleteError.value = errorMessage(error) }
  finally { busy.value = false }
}
</script>

<template>
  <header class="page-header title-row"><div><span class="eyebrow">学习空间</span><h1>我的知识库</h1><p>以主题为线索，让每一份知识各得其所。</p></div><button :disabled="busy || store.loading" @click="openForm()">＋ 创建知识库</button></header>
  <p v-if="notice" class="notice" role="status">{{ notice }}</p>
  <KnowledgeBaseForm v-if="showForm" :key="editing?.id ?? 'new'" :item="editing" :busy="busy" :error="formError" @save="save" @cancel="closeForm" />
  <section v-if="deleting" class="panel delete-confirm" aria-labelledby="delete-title">
    <h2 id="delete-title">删除“{{ deleting.name }}”？</h2><p>将同时删除其中的文档、文本分片和向量，删除后无法恢复。</p><p v-if="deleteError" class="error" role="alert">{{ deleteError }}</p>
    <div class="actions"><button class="secondary" :disabled="busy" @click="deleting = undefined">取消删除</button><button class="danger" :disabled="busy" @click="remove">{{ busy ? '删除中…' : '确认删除' }}</button></div>
  </section>
  <div class="section-heading"><h2>全部知识库 <span class="count">{{ store.items.length }}</span></h2><button class="text-button" :disabled="busy || store.loading" @click="store.load">刷新列表</button></div>
  <p v-if="store.loading" role="status" class="muted">正在读取知识库…</p>
  <div v-else-if="store.error" class="panel error" role="alert">{{ store.error }} <button class="secondary" @click="store.load">重试</button></div>
  <div v-else-if="!store.items.length" class="panel empty"><div class="folder-icon" aria-hidden="true">▤</div><h3>从第一个主题开始</h3><p>课程笔记、研究课题或个人兴趣，都可以拥有自己的知识库。</p><button :disabled="busy" @click="openForm()">创建知识库</button></div>
  <div v-else class="card-grid"><KnowledgeBaseCard v-for="item in store.items" :key="item.id" :item="item" :disabled="busy" @edit="openForm" @remove="confirmDelete" /></div>
</template>
