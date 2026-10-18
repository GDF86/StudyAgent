<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { historyApi, describeError, type Conversation, type HistoryMessage } from '../api/history'
import MarkdownAnswer from '../components/MarkdownAnswer.vue'
import CitationCards from '../components/CitationCards.vue'

const route = useRoute()
const conversation = ref<Conversation>()
const messages = ref<HistoryMessage[]>([])
const selected = ref<HistoryMessage>()
const error = ref('')
const loading = ref(false)
const cancelling = ref(false)
const pending = computed(() => messages.value.some(message => message.status === 'pending'))
let version = 0
let readController: AbortController | undefined

async function refresh(current = version) {
  readController?.abort()
  const reads = new AbortController()
  readController = reads
  const id = String(route.params.id)
  try {
    const item = await historyApi.get(id, reads.signal)
    const all: HistoryMessage[] = []
    let offset = 0
    do {
      const page = await historyApi.messages(id, offset, 100, reads.signal)
      all.push(...page.items)
      offset += page.items.length
      if (offset >= page.total || !page.items.length) break
    } while (current === version && !reads.signal.aborted)
    if (current === version && !reads.signal.aborted) {
      conversation.value = item
      messages.value = all
      selected.value = all.filter(message => message.role === 'assistant' && message.status === 'completed').at(-1)
    }
  } finally {
    if (readController === reads) readController = undefined
  }
}

async function reload() {
  const current = version
  loading.value = true
  error.value = ''
  try { await refresh(current) }
  catch (cause) {
    if (current === version && !(cause instanceof Error && cause.name === 'AbortError')) error.value = describeError(cause)
  } finally {
    if (current === version) loading.value = false
  }
}

watch(() => route.params.id, () => {
  readController?.abort()
  version++
  conversation.value = undefined
  messages.value = []
  selected.value = undefined
  cancelling.value = false
  void reload()
}, { immediate: true })

async function cancelPending() {
  const row = messages.value.find(message => message.status === 'pending')
  if (!row?.request_id || cancelling.value) return
  const current = version
  const id = String(route.params.id)
  cancelling.value = true
  error.value = ''
  try {
    await historyApi.cancel(id, row.request_id)
    if (current === version) await reload()
  } catch (cause) {
    if (current === version) error.value = describeError(cause)
  } finally {
    if (current === version) cancelling.value = false
  }
}

onBeforeUnmount(() => { readController?.abort(); version++ })
const labels = { pending: '正在执行', completed: '已完成', failed: '执行失败', cancelled: '已停止', interrupted: '执行中断' }
</script>

<template>
  <RouterLink to="/history">← 历史会话</RouterLink>
  <header class="page-header"><span class="eyebrow">CONVERSATION</span><h1>{{ conversation?.title || '历史会话' }}</h1><p>仅查看已保存的记录。已有记录可能包含测试回复，请以文档原文为准。</p></header>
  <p v-if="loading" role="status">正在加载会话…</p>
  <p v-if="error" role="alert" class="error">{{ error }}</p>
  <template v-if="conversation">
    <div class="chat-toolbar">
      <span>{{ conversation.mode === 'chat' ? '资料问答记录' : 'Agent 任务记录' }}</span>
      <RouterLink :to="`/knowledge-bases/${conversation.knowledge_base_id}`">管理当前知识库</RouterLink>
      <button class="secondary" :disabled="loading || cancelling" @click="reload">刷新历史</button>
      <button v-if="pending" class="secondary" :disabled="loading || cancelling" @click="cancelPending">{{ cancelling ? '正在停止…' : '停止未完成请求' }}</button>
    </div>
    <details v-if="conversation.summary" class="panel history-panel"><summary>已保存的历史摘要（截至第 {{ conversation.summary_through_turn }} 轮）</summary><p>{{ conversation.summary }}</p></details>
    <div class="chat-layout">
      <section class="chat-main" aria-label="会话消息">
        <p v-if="!messages.length" class="panel history-panel">此会话暂无已保存的消息。</p>
        <article v-for="message in messages" :key="message.id" class="panel history-panel" :data-role="message.role" :data-status="message.status">
          <p class="muted">{{ message.role === 'user' ? '你' : 'StudyAgent' }} · 第 {{ message.turn_no }} 轮 · 尝试 {{ message.attempt_no }} · {{ labels[message.status] }}</p>
          <MarkdownAnswer v-if="message.content" :text="message.content" />
          <p v-if="message.error_code" class="error">{{ message.error_code }}</p>
          <button v-if="message.citations.length" class="text-button" @click="selected = message">查看本条引用（{{ message.citations.length }}）</button>
        </article>
      </section>
      <CitationCards :citations="selected?.citations || []" :pending="false" />
    </div>
  </template>
</template>
