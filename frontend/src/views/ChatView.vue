<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { answerQuestion, streamQuestion } from '../api/chat'
import { errorMessage, knowledgeBasesApi } from '../api/knowledgeBases'
import MarkdownAnswer from '../components/MarkdownAnswer.vue'
import CitationCards from '../components/CitationCards.vue'
import type { KnowledgeBase } from '../types/knowledgeBase'
import type { Citation, ChatResponse } from '../types/chat'

interface Exchange {
  id: number
  question: string
  answer: string
  citations: Citation[]
  state: 'pending' | 'complete' | 'error' | 'stopped'
  error: string
}

const route = useRoute()
const router = useRouter()
const bases = ref<KnowledgeBase[]>([])
const kb = ref<KnowledgeBase>()
const input = ref('')
const streaming = ref(true)
const exchanges = ref<Exchange[]>([])
const selected = ref<number>()
const busy = ref(false)
const loading = ref(false)
const pageError = ref('')
let serial = 0
let version = 0
let controller: AbortController | undefined
const active = computed(() => exchanges.value.find(item => item.id === selected.value))

function stop() { controller?.abort() }

watch(() => route.params.id, async () => {
  const current = ++version
  stop()
  busy.value = false
  exchanges.value = []
  selected.value = undefined
  input.value = ''
  kb.value = undefined
  pageError.value = ''
  loading.value = true
  try {
    const [items, currentKb] = await Promise.all([knowledgeBasesApi.list(), knowledgeBasesApi.get(Number(route.params.id))])
    if (current !== version) return
    bases.value = items
    kb.value = currentKb
  } catch (cause) { if (current === version) pageError.value = errorMessage(cause) }
  finally { if (current === version) loading.value = false }
}, { immediate: true })

function switchKnowledgeBase(event: Event) {
  void router.push(`/knowledge-bases/${(event.target as HTMLSelectElement).value}/chat`)
}

function clear() {
  stop()
  version++
  busy.value = false
  exchanges.value = []
  selected.value = undefined
}

async function generate(target: Exchange) {
  if (!kb.value || busy.value) return
  const current = version
  const kbId = kb.value.id
  target.answer = ''
  target.citations = []
  target.error = ''
  target.state = 'pending'
  selected.value = target.id
  busy.value = true
  controller = new AbortController()
  const signal = controller.signal
  function finish(response: ChatResponse) {
    if (response.knowledge_base_id !== kbId) throw new Error('回答知识库不匹配，已停止显示')
    target.answer = response.answer
    target.citations = response.citations
    target.state = 'complete'
  }
  try {
    const body = { knowledge_base_id: kbId, question: target.question }
    if (streaming.value) {
      await streamQuestion(body, signal, event => {
        if (current !== version) return
        if (event.type === 'token') target.answer += event.content
        if (event.type === 'citation') {
          if (event.data.knowledge_base_id !== kbId) throw new Error('引用知识库不匹配')
          target.citations.push(event.data)
        }
        if (event.type === 'done') finish(event)
      })
    } else {
      const result = await answerQuestion(body, signal)
      if (current === version) finish(result)
    }
  } catch (cause) {
    if (current !== version) return
    target.state = signal.aborted ? 'stopped' : 'error'
    target.error = signal.aborted ? '已停止生成，当前片段不是完整回答。' : cause instanceof Error ? cause.message : '问答失败，请重试'
    if (!signal.aborted) { target.answer = ''; target.citations = [] }
  } finally {
    if (current === version) { busy.value = false; controller = undefined }
  }
}

function submit() {
  const question = input.value.trim()
  if (!question || !kb.value || busy.value) return
  const item: Exchange = { id: ++serial, question, answer: '', citations: [], state: 'pending', error: '' }
  exchanges.value.push(item)
  input.value = ''
  // Use the reactive object so streamed deltas update the UI.
  void generate(exchanges.value[exchanges.value.length - 1]!)
}

onBeforeUnmount(() => { version++; stop() })
</script>

<template>
  <RouterLink to="/knowledge-bases">← 返回知识库</RouterLink>
  <header class="page-header chat-header"><span class="eyebrow">基于资料的问答</span><h1>{{ kb?.name || '知识库问答' }}</h1><p>回答依据当前知识库，关键结论可追溯到原文。</p></header>
  <p v-if="loading" role="status">正在加载知识库…</p>
  <p v-if="pageError" class="error" role="alert">{{ pageError }}</p>
  <template v-if="kb">
    <div class="chat-toolbar"><RouterLink :to="`/history?knowledge_base_id=${kb.id}&mode=chat`">打开持久化会话</RouterLink><label for="chat-kb">当前知识库</label><select id="chat-kb" :value="kb.id" @change="switchKnowledgeBase"><option v-for="base in bases" :key="base.id" :value="base.id">{{ base.name }}</option></select><RouterLink :to="`/knowledge-bases/${kb.id}`">管理文档</RouterLink><button class="text-button" @click="clear" :disabled="!exchanges.length">清空对话</button></div>
    <div class="chat-layout">
      <section aria-label="问答内容" class="chat-main">
        <div v-if="!exchanges.length" class="panel empty"><h2>向你的资料提问</h2><p>例如：这份资料的主要结论是什么？<br />请在每次提问中包含完整问题。</p></div>
        <article v-for="item in exchanges" :key="item.id" class="panel exchange" :aria-busy="item.state === 'pending'">
          <div class="question"><span class="eyebrow">你的问题</span><p>{{ item.question }}</p></div>
          <div class="answer"><span class="eyebrow">STUDYAGENT</span><MarkdownAnswer v-if="item.answer" :text="item.answer" /><p v-if="item.state === 'pending'" role="status" class="muted">{{ item.answer ? '正在生成，完成后将校验引用…' : '正在检索资料并生成回答…' }}</p><p v-if="item.error" class="error" role="alert">{{ item.error }}</p></div>
          <div class="exchange-actions"><button class="text-button" :disabled="busy" @click="generate(item)">重新生成</button><button class="text-button" @click="selected = item.id">查看本条引用（{{ item.citations.length }}）</button><span v-if="item.state === 'complete'" class="muted">回答完成</span></div>
        </article>
        <form class="panel chat-composer" @submit.prevent="submit"><label for="chat-question">输入问题</label><textarea id="chat-question" v-model="input" maxlength="4000" rows="3" :disabled="busy" placeholder="只针对当前知识库提问…" required></textarea><div class="actions"><label class="stream-option"><input v-model="streaming" type="checkbox" :disabled="busy" />使用流式回答</label><button v-if="busy" type="button" class="secondary" @click="stop">停止生成</button><button type="submit" :disabled="busy || !input.trim()">{{ busy ? '生成中…' : '发送问题' }}</button></div></form>
      </section>
      <CitationCards :citations="active?.citations || []" :pending="active?.state === 'pending'" />
    </div>
  </template>
</template>
