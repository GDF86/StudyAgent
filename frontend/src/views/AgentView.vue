<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { runAgent, type AgentResult, type AgentStatus } from '../api/agent'
import { errorMessage, knowledgeBasesApi } from '../api/knowledgeBases'
import type { KnowledgeBase } from '../types/knowledgeBase'
import MarkdownAnswer from '../components/MarkdownAnswer.vue'
import CitationCards from '../components/CitationCards.vue'
const route = useRoute()
const router = useRouter()
const kb = ref<KnowledgeBase>()
const bases = ref<KnowledgeBase[]>([])
const task = ref('')
const statuses = ref<AgentStatus[]>([])
const result = ref<AgentResult>()
const error = ref('')
const busy = ref(false)
let version = 0
let controller: AbortController | undefined
const names: Record<string, string> = { search_documents: '检索资料', summarize_document: '总结文档', compare_documents: '比较文档', generate_quiz: '生成练习题', calculator: '计算', unknown_tool: '未知工具' }
function stop() { controller?.abort() }
watch(() => route.params.id, async () => {
  const current = ++version
  stop(); busy.value = false; kb.value = undefined; result.value = undefined; statuses.value = []; error.value = ''; task.value = ''
  try {
    const [selected, all] = await Promise.all([knowledgeBasesApi.get(Number(route.params.id)), knowledgeBasesApi.list()])
    if (current === version) { kb.value = selected; bases.value = all }
  } catch (cause) { if (current === version) error.value = errorMessage(cause) }
}, { immediate: true })
function switchBase(event: Event) { void router.push(`/knowledge-bases/${(event.target as HTMLSelectElement).value}/agent`) }
async function submit() {
  if (!kb.value || busy.value || !task.value.trim()) return
  const current = version
  controller = new AbortController()
  const signal = controller.signal
  busy.value = true; result.value = undefined; statuses.value = []; error.value = ''
  try {
    const answer = await runAgent(kb.value.id, task.value.trim(), signal, event => { if (current === version) statuses.value.push(event) })
    if (current === version) result.value = answer
  } catch (cause) {
    if (current === version) error.value = signal.aborted ? '已停止任务，未生成完整结果。' : cause instanceof Error ? cause.message : '任务失败'
  } finally { if (current === version) { busy.value = false; controller = undefined } }
}
onBeforeUnmount(() => { version++; stop() })
</script>
<template>
  <RouterLink to="/knowledge-bases">← 返回知识库</RouterLink>
  <header class="page-header"><span class="eyebrow">AGENT 学习助手</span><h1>{{ kb?.name || '学习任务' }}</h1><p>检索、总结、比较、出题与计算。每次任务独立执行，不保留历史记忆。</p></header>
  <p v-if="error" class="error" role="alert">{{ error }}</p>
  <template v-if="kb">
    <div class="chat-toolbar"><RouterLink :to="`/history?knowledge_base_id=${kb.id}&mode=agent`">打开持久化会话</RouterLink><label for="agent-kb">当前知识库</label><select id="agent-kb" :value="kb.id" @change="switchBase"><option v-for="base in bases" :key="base.id" :value="base.id">{{ base.name }}</option></select><RouterLink :to="`/knowledge-bases/${kb.id}`">管理文档</RouterLink><RouterLink :to="`/knowledge-bases/${kb.id}/chat`">资料问答</RouterLink></div>
    <div class="chat-layout">
      <section class="chat-main" aria-label="Agent 任务">
        <form class="panel chat-composer" @submit.prevent="submit"><label for="agent-task">学习任务</label><textarea id="agent-task" v-model="task" maxlength="4000" rows="4" :disabled="busy" placeholder="例如：比较两篇资料的主要结论，并生成三道简答题。" required></textarea><div class="actions"><button v-if="busy" type="button" class="secondary" @click="stop">停止任务</button><button type="submit" :disabled="busy || !task.trim()">{{ busy ? '执行中…' : '执行任务' }}</button></div></form>
        <section v-if="statuses.length" class="panel agent-panel" aria-label="执行状态" aria-live="polite"><h2>执行状态</h2><p class="muted">仅展示实际执行事件。</p><ol class="agent-status"><li v-for="(status, i) in statuses" :key="i" :data-phase="status.phase">{{ status.tool ? names[status.tool] || '工具' : `第 ${status.round} 轮` }}：{{ status.message }}</li></ol></section>
        <article v-if="result" class="panel agent-panel"><h2>任务完成</h2><p class="muted">执行了 {{ result.tool_calls }} 次工具调用</p><MarkdownAnswer :text="result.answer" /></article>
      </section>
      <CitationCards :citations="result?.citations || []" :pending="busy" />
    </div>
  </template>
</template>

<style scoped>
.agent-panel { padding: 24px; margin-top: 18px; overflow-wrap: anywhere; }
.agent-panel h2 { margin-top: 0; }
.agent-status { padding-left: 22px; line-height: 1.7; }
.agent-status li { margin: 6px 0; }
.agent-status [data-phase="tool_succeeded"] { color: #176d5b; }
.agent-status [data-phase="tool_failed"] { color: #a33d2b; }
@media (max-width: 600px) { .agent-panel { padding: 18px; } }
</style>
