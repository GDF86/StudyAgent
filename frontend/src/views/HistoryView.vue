<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { historyApi, describeError, type Conversation } from '../api/history'
import { knowledgeBasesApi } from '../api/knowledgeBases'
import type { KnowledgeBase } from '../types/knowledgeBase'
const route = useRoute()
const bases = ref<KnowledgeBase[]>([]), items = ref<Conversation[]>([])
const filterKb = ref(String(route.query.knowledge_base_id || '')), filterMode = ref(String(route.query.mode || ''))
const error = ref(''), loading = ref(false), saving = ref(false), total = ref(0), offset = ref(0)
const editing = ref(''), editedTitle = ref(''), deleting = ref<Conversation>()
let version = 0
async function load(reset = false) {
  if (reset) offset.value = 0
  const current = ++version
  loading.value = true; error.value = ''
  const params = new URLSearchParams({ limit: '20', offset: String(offset.value) })
  if (filterKb.value) params.set('knowledge_base_id', filterKb.value)
  if (filterMode.value) params.set('mode', filterMode.value)
  try { const page = await historyApi.list(params); if (current === version) { items.value = page.items; total.value = page.total } }
  catch (cause) { if (current === version) error.value = describeError(cause) }
  finally { if (current === version) loading.value = false }
}
async function rename(item: Conversation) {
  saving.value = true
  try { await historyApi.rename(item.id, editedTitle.value.trim()); editing.value = ''; await load() }
  catch (cause) { error.value = describeError(cause) }
  finally { saving.value = false }
}
async function remove() {
  if (!deleting.value) return
  saving.value = true
  try { await historyApi.remove(deleting.value.id); deleting.value = undefined; await load(true) }
  catch (cause) { error.value = describeError(cause) }
  finally { saving.value = false }
}
async function page(delta: number) { offset.value += delta; await load() }
onMounted(async () => {
  try { bases.value = await knowledgeBasesApi.list(); await load() }
  catch (cause) { error.value = describeError(cause) }
})
</script>
<template>
  <header class="page-header"><span class="eyebrow">HISTORY</span><h1>历史会话</h1><p>查看和管理已有会话记录，当前不提供新建会话或继续提问。</p></header>
  <p v-if="error" role="alert" class="error">{{ error }}</p>
  <div class="history-controls history-filters"><label>筛选知识库<select aria-label="筛选知识库" v-model="filterKb" @change="load(true)"><option value="">全部知识库</option><option v-for="base in bases" :key="base.id" :value="String(base.id)">{{ base.name }}</option></select></label><label>筛选模式<select aria-label="筛选模式" v-model="filterMode" @change="load(true)"><option value="">全部模式</option><option value="chat">资料问答</option><option value="agent">Agent 学习任务</option></select></label><button class="secondary" @click="load()">刷新列表</button></div>
  <p v-if="loading" role="status">正在加载历史…</p>
  <p v-else-if="!items.length" class="panel history-panel">暂无已有会话记录。你可以前往知识库上传文档、查看提取的文字和分片。</p>
  <article v-for="item in items" :key="item.id" class="panel history-panel history-item">
    <RouterLink :to="`/conversations/${item.id}`"><h2>{{ item.title }}</h2></RouterLink><p class="muted">{{ item.mode === 'chat' ? '资料问答' : 'Agent 学习任务' }} · {{ bases.find(b => b.id === item.knowledge_base_id)?.name || '知识库' }} · {{ new Date(item.updated_at).toLocaleString() }}</p>
    <form v-if="editing === item.id" class="history-controls" @submit.prevent="rename(item)"><label>新的标题<input v-model="editedTitle" maxlength="100" required /></label><button :disabled="saving || !editedTitle.trim()">保存标题</button><button type="button" class="secondary" @click="editing = ''">取消</button></form>
    <div v-else class="actions"><button class="secondary" @click="editing = item.id; editedTitle = item.title">重命名</button><button class="secondary" @click="deleting = item">删除会话</button></div>
  </article>
  <div class="actions"><button class="secondary" :disabled="offset === 0 || loading" @click="page(-20)">上一页</button><span>共 {{ total }} 个会话</span><button class="secondary" :disabled="offset + 20 >= total || loading" @click="page(20)">下一页</button></div>
  <section v-if="deleting" class="panel history-panel" role="dialog" aria-label="删除会话确认"><p>永久删除“{{ deleting.title }}”及其所有消息？</p><button :disabled="saving" @click="remove">确认删除</button><button class="secondary" @click="deleting = undefined">取消删除</button></section>
</template>
