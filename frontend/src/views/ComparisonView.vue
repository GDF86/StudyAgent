<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { knowledgeBasesApi } from '../api/knowledgeBases'
import { describeError } from '../api/history'
import type { KnowledgeBase } from '../types/knowledgeBase'
import type { Citation } from '../types/chat'
interface Run { mode: string; citations: Citation[]; elapsed_ms: number; error_code: string | null; error_message: string | null }
const bases = ref<KnowledgeBase[]>([]), kb = ref(''), question = ref(''), topK = ref(5)
const runs = ref<Run[]>([]), busy = ref(false), error = ref(''), candidateK = ref(20)
let controller: AbortController | undefined
const base = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')
function clear() { controller?.abort(); controller = undefined; busy.value = false; runs.value = []; error.value = '' }
async function compare() {
  clear(); const current = new AbortController(); controller = current; busy.value = true
  try {
    const response = await fetch(`${base}/api/rag/compare`, { method: 'POST', signal: current.signal,
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ knowledge_base_id: Number(kb.value), question: question.value.trim(), top_k: topK.value }) })
    const data = await response.json()
    if (!response.ok) throw new Error(data.message || '对比失败')
    if (controller === current) { runs.value = data.runs; candidateK.value = data.candidate_k }
  } catch (cause) { if (controller === current && !current.signal.aborted) error.value = describeError(cause) }
  finally { if (controller === current) { busy.value = false; controller = undefined } }
}
onMounted(async () => { try { bases.value = await knowledgeBasesApi.list(); kb.value = String(bases.value[0]?.id || '') } catch (cause) { error.value = describeError(cause) } })
onBeforeUnmount(() => controller?.abort())
</script>
<template>
  <header class="page-header"><span class="eyebrow">RETRIEVAL COMPARISON</span><h1>检索对比</h1><p>查看同一问题在基础检索与重排后的来源顺序。</p></header>
  <p class="panel comparison-note">本页不生成答案、不保存会话。两种方式分别执行；耗时是本次测量，不代表模型性能。Mock 服务只能验证流程，不能证明效果提升。</p>
  <form class="panel history-panel" @submit.prevent="compare">
    <div class="history-controls"><label>知识库<select aria-label="知识库" v-model="kb" required @change="clear"><option v-for="item in bases" :key="item.id" :value="String(item.id)">{{ item.name }}</option></select></label>
    <label>返回条数<input v-model.number="topK" type="number" min="1" max="20" required @change="clear" /></label></div>
    <label>问题<textarea v-model="question" required maxlength="4000" rows="3" @input="clear" /></label>
    <button class="primary" :disabled="busy || !kb || !question.trim()">{{ busy ? '正在对比…' : '开始对比' }}</button>
    <button v-if="busy" type="button" @click="clear">停止等待</button>
    <p v-if="!bases.length">请先创建知识库并上传资料。</p>
  </form>
  <p v-if="error" role="alert" class="error">{{ error }}</p>
  <div class="comparison-grid" aria-live="polite" :aria-busy="busy">
    <section v-for="run in runs" :key="run.mode" class="panel comparison-result" :data-mode="run.mode">
      <h2>{{ run.mode === 'basic' ? 'Basic RAG' : 'RAG + Reranker' }}</h2>
      <p>{{ run.elapsed_ms.toFixed(1) }} ms · {{ run.citations.length }} 条来源<span v-if="run.mode === 'reranked'"> · 候选上限 {{ candidateK }}</span></p>
      <p v-if="run.error_code" role="alert" class="error">{{ run.error_message }}（{{ run.error_code }}）</p>
      <p v-else-if="!run.citations.length">没有符合阈值的已就绪资料。</p>
      <ol><li v-for="source in run.citations" :key="source.chunk_id"><strong>{{ source.document_name }}</strong><span v-if="source.page"> · 第 {{ source.page }} 页</span>
        <p>向量相似度 {{ source.score.toFixed(4) }}（不是重排分数）</p><details><summary>查看来源原文</summary><p class="source-content">{{ source.content }}</p></details></li></ol>
    </section>
  </div>
</template>
<style scoped>
.comparison-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; margin-top: 24px; }
.comparison-note { padding: 16px 20px; color: #58685f; font-size: 14px; }
.comparison-result { padding: 24px; min-width: 0; overflow-wrap: anywhere; }
.comparison-result li { margin-bottom: 20px; }
textarea { border: 1px solid #d6e0d7; border-radius: 8px; padding: 12px; color: #243c35; resize: vertical; width: 100%; box-sizing: border-box; margin: 8px 0 16px; }
.source-content { white-space: pre-wrap; }
@media (max-width: 800px) { .comparison-grid { grid-template-columns: 1fr; } }
</style>
