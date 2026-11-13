<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { historyApi, describeError, type Conversation, type HistoryMessage } from '../api/history'
import MarkdownAnswer from '../components/MarkdownAnswer.vue'
import CitationCards from '../components/CitationCards.vue'
import { answerQuestion, streamQuestion } from '../api/chat'
import { runAgent, type AgentStatus } from '../api/agent'

const route = useRoute()
const conversation = ref<Conversation>()
const messages = ref<HistoryMessage[]>([])
const selected = ref<HistoryMessage>()
const error = ref('')
const loading = ref(false)
const cancelling = ref(false)
const input = ref(''), streaming = ref(true), busy = ref(false), preview = ref('')
const statuses = ref<AgentStatus[]>([])
let generationController: AbortController | undefined
let activeRequest: { id: string; requestId: string } | undefined
const lastUser = computed(() => messages.value.filter(message => message.role === 'user').at(-1))
const pending = computed(() => messages.value.some(message => message.status === 'pending'))
let version = 0
let readController: AbortController | undefined

async function stop() {
  generationController?.abort()
  const request = activeRequest
  if (request) {
    try { await historyApi.cancel(request.id, request.requestId) }
    catch (cause) { if (request === activeRequest) error.value = describeError(cause) }
  }
}

async function generate(regenerate = false) {
  const item = conversation.value
  const question = regenerate ? lastUser.value?.content : input.value.trim()
  if (!item || !question || busy.value || pending.value) return
  const current = version
  const request = { id: item.id, requestId: crypto.randomUUID() }
  activeRequest = request
  const controller = new AbortController()
  generationController = controller
  busy.value = true; error.value = ''; preview.value = ''; statuses.value = []
  const session = { conversation_id: item.id, request_id: request.requestId,
    ...(regenerate ? { regenerate_turn: lastUser.value!.turn_no } : {}) }
  try {
    if (item.mode === 'agent') {
      await runAgent(item.knowledge_base_id, question, controller.signal, event => {
        if (current === version) statuses.value.push(event)
      }, session)
    } else {
      const body = { knowledge_base_id: item.knowledge_base_id, question, ...session }
      if (streaming.value) {
        await streamQuestion(body, controller.signal, event => {
          if (current === version && event.type === 'token') preview.value += event.content
        })
      } else await answerQuestion(body, controller.signal)
    }
    if (current === version && !regenerate) input.value = ''
  } catch (cause) {
    if (current === version) error.value = controller.signal.aborted ? '已停止生成，历史记录已保留。' : describeError(cause)
  } finally {
    if (current === version) {
      preview.value = ''; busy.value = false; generationController = undefined; activeRequest = undefined
      try { await refresh(current) }
      catch (cause) { if (current === version) error.value = describeError(cause) }
    }
  }
}

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
  void stop()
  readController?.abort()
  version++
  conversation.value = undefined
  messages.value = []
  selected.value = undefined
  cancelling.value = false
  busy.value = false; preview.value = ''; input.value = ''; statuses.value = []; activeRequest = undefined
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

onBeforeUnmount(() => { void stop(); readController?.abort(); version++ })
function retryShort(){ input.value=(lastUser.value?.content || '')+'\n请用不超过300字的核心要点简短回答，避免扩展。';void generate() }
const labels = { pending: '正在执行', completed: '已完成', failed: '执行失败', cancelled: '已停止', interrupted: '执行中断' }
</script>

<template>
  <RouterLink to="/history">← 历史会话</RouterLink>
  <header class="page-header"><span class="eyebrow">CONVERSATION</span><h1>{{ conversation?.title || '历史会话' }}</h1><p>连续提问，自动保存回答与引用。长历史摘要会调用模型并计入累计预算；资料结论以当前原文为依据。</p></header>
  <p v-if="loading" role="status">正在加载会话…</p>
  <p v-if="error" role="alert" class="error">{{ error }}</p>
  <template v-if="conversation">
    <div class="chat-toolbar">
      <span>{{ conversation.mode === 'chat' ? '资料问答记录' : 'Agent 任务记录' }}</span>
      <RouterLink :to="`/knowledge-bases/${conversation.knowledge_base_id}`">管理当前知识库</RouterLink>
      <button class="secondary" :disabled="loading || cancelling || busy" @click="reload">刷新历史</button>
      <button v-if="pending && !busy" class="secondary" :disabled="loading || cancelling" @click="cancelPending">{{ cancelling ? '正在停止…' : '停止未完成请求' }}</button>
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
        <section v-if="busy" class="panel history-panel" role="status" aria-live="polite">
          <p>正在{{ conversation.mode === 'agent' ? '执行学习任务' : '检索并生成回答' }}，完成后自动保存…</p>
          <MarkdownAnswer v-if="preview" :text="preview" />
          <ol v-if="statuses.length" class="history-progress"><li v-for="(status, index) in statuses" :key="index">{{ status.message }}</li></ol>
        </section>
        <form class="panel chat-composer" @submit.prevent="generate()">
          <label for="conversation-input">学习问题或任务</label>
          <textarea id="conversation-input" v-model="input" rows="3" maxlength="4000" :disabled="busy || pending || loading" required placeholder="继续当前知识库的学习…"></textarea>
          <div class="actions">
            <label v-if="conversation.mode === 'chat'" class="stream-option"><input v-model="streaming" type="checkbox" :disabled="busy" />流式回答</label>
            <button v-if="busy" type="button" class="secondary" @click="stop">停止生成</button>
            <button type="submit" :disabled="busy || pending || loading || !input.trim()">发送并保存</button>
            <button v-if="messages.some(m=>m.error_code==='LLM_OUTPUT_LIMIT')" type="button" :disabled="busy || pending" @click="retryShort">重新提问（简短回答）</button>
            <button v-if="lastUser" type="button" class="secondary" :disabled="busy || pending || loading" @click="generate(true)">重新生成最后一轮</button>
          </div>
        </form>
      </section>
      <CitationCards :citations="selected?.citations || []" :pending="false" />
    </div>
  </template>
</template>
