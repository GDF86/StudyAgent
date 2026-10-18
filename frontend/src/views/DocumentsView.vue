<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { documentsApi } from '../api/documents'
import { errorMessage, knowledgeBasesApi } from '../api/knowledgeBases'
import type { KnowledgeBase } from '../types/knowledgeBase'
import type { DocumentDetail, StudyDocument } from '../types/document'

const route = useRoute()
const kb = ref<KnowledgeBase>()
const items = ref<StudyDocument[]>([])
const selected = ref<File>()
const input = ref<HTMLInputElement>()
const detail = ref<DocumentDetail>()
const deleting = ref<StudyDocument>()
const loading = ref(false)
const uploading = ref(false)
const removing = ref(false)
const progress = ref(0)
const error = ref('')
const listError = ref('')
const notice = ref('')
let version = 0
let timer: ReturnType<typeof setInterval> | undefined
const statusNames: Record<string, string> = { processing: '正在解析', embedding: '正在生成向量', ready: '已就绪', failed: '处理失败', deleting: '正在删除' }

async function refresh() {
  const current = version
  try {
    const docs = await documentsApi.list(Number(route.params.id))
    if (current === version) { items.value = docs; listError.value = '' }
  } catch (cause) { if (current === version) listError.value = errorMessage(cause) }
}

watch(() => route.params.id, async () => {
  const current = ++version
  kb.value = undefined
  detail.value = undefined
  deleting.value = undefined
  items.value = []
  error.value = ''
  listError.value = ''
  loading.value = true
  try {
    const result = await knowledgeBasesApi.get(Number(route.params.id))
    if (current !== version) return
    kb.value = result
    await refresh()
  } catch (cause) { if (current === version) error.value = errorMessage(cause) }
  finally { if (current === version) loading.value = false }
}, { immediate: true })

function chooseFile(event: Event) {
  selected.value = (event.target as HTMLInputElement).files?.[0]
  error.value = ''
}

async function upload() {
  if (!selected.value || !kb.value || uploading.value) return
  if (!/\.(pdf|docx|txt|md)$/i.test(selected.value.name)) { error.value = '仅支持 PDF、DOCX、TXT 和 MD 文件'; return }
  if (!selected.value.size) { error.value = '不能上传空文件'; return }
  const current = version
  uploading.value = true
  progress.value = 0
  error.value = ''
  notice.value = ''
  timer = setInterval(() => { void refresh() }, 2000)
  try {
    await documentsApi.upload(kb.value.id, selected.value, value => { progress.value = value })
    if (current === version) {
      notice.value = '文档已解析，文本分片和向量已保存'
      selected.value = undefined
      if (input.value) input.value.value = ''
    }
  } catch (cause) { if (current === version) error.value = errorMessage(cause) }
  finally {
    clearInterval(timer)
    uploading.value = false
    if (current === version) await refresh()
  }
}

async function inspect(item: StudyDocument) {
  const current = version
  error.value = ''
  try { const result = await documentsApi.detail(item.id); if (current === version) detail.value = result }
  catch (cause) { if (current === version) error.value = errorMessage(cause) }
}

async function remove() {
  if (!deleting.value || removing.value) return
  removing.value = true
  error.value = ''
  try {
    await documentsApi.remove(deleting.value.id)
    if (detail.value?.id === deleting.value.id) detail.value = undefined
    deleting.value = undefined
    notice.value = '文档、文本分片和向量已删除'
    await refresh()
  } catch (cause) { error.value = errorMessage(cause) }
  finally { removing.value = false }
}

onBeforeUnmount(() => { version++; clearInterval(timer) })
</script>

<template>
  <RouterLink to="/knowledge-bases">← 返回知识库</RouterLink>
  <header class="page-header document-header"><span class="eyebrow">文档知识库</span><h1>{{ kb?.name || '知识库文档' }}</h1><p>{{ kb?.description || '上传学习资料，保存可追溯的文本分片。' }}</p></header>
  <p v-if="loading" role="status">正在加载…</p>
  <p v-if="error" class="error" role="alert">{{ error }}</p>
  <p v-if="notice" class="notice" role="status">{{ notice }}</p>
  <template v-if="kb">
    <form class="panel upload-panel" @submit.prevent="upload" :aria-busy="uploading">
      <h2>添加学习资料</h2><p class="muted">支持 PDF、DOCX、TXT、MD。默认单文件上限 10 MB，以后端配置为准。扫描版 PDF 暂不支持。</p>
      <label for="document-file">选择文件</label>
      <input id="document-file" ref="input" type="file" accept=".pdf,.docx,.txt,.md" :disabled="uploading || removing" @change="chooseFile" />
      <div class="actions"><span v-if="uploading" role="status">{{ progress < 100 ? `上传中 ${progress}%` : '正在解析并生成向量，请稍候…' }}</span><button :disabled="!selected || uploading || removing">{{ uploading ? '处理中…' : '上传并处理' }}</button></div>
    </form>
    <section v-if="deleting" class="panel delete-confirm"><h2>删除“{{ deleting.filename }}”？</h2><p>原文件、文本分片和向量索引将一并删除，无法恢复。</p><div class="actions"><button class="secondary" :disabled="removing" @click="deleting = undefined">取消删除</button><button class="danger" :disabled="removing" @click="remove">确认删除文档</button></div></section>
    <div class="section-heading"><h2>文件列表 <span class="count">{{ items.length }}</span></h2><button class="text-button" :disabled="uploading || removing" @click="refresh">刷新文件</button></div>
    <div v-if="listError" class="error" role="alert">{{ listError }} <button class="secondary" @click="refresh">重试</button></div>
    <div v-else-if="!items.length" class="panel empty"><h3>还没有学习资料</h3><p>选择一份文件，开始构建这个知识库。</p></div>
    <div v-else class="document-list">
      <article v-for="item in items" :key="item.id" class="panel document-row">
        <div class="document-info"><h3>{{ item.filename }}</h3><p class="muted">{{ item.file_type.slice(1).toUpperCase() }} · {{ (item.file_size / 1024).toFixed(1) }} KB · {{ item.chunk_count }} 个分片 · {{ new Date(item.created_at).toLocaleString('zh-CN') }}</p><span class="document-status" :class="item.status">{{ statusNames[item.status] }}</span><p v-if="item.error_message" class="error">{{ item.error_message }}。删除此记录后可重新上传。</p></div>
        <div class="card-actions"><button class="text-button" :disabled="uploading || removing" @click="inspect(item)" :aria-label="`查看 ${item.filename}`">查看分片</button><button class="text-button danger-text" :disabled="uploading || removing" @click="deleting = item" :aria-label="`删除 ${item.filename}`">删除</button></div>
      </article>
    </div>
    <section v-if="detail" class="panel chunk-panel"><div class="section-heading"><h2>{{ detail.filename }} · 文本分片</h2><button class="secondary" @click="detail = undefined">关闭详情</button></div><p class="muted">共 {{ detail.chunk_count }} 个分片。PDF 保留原页码；其他格式不编造页码。</p><p v-if="!detail.chunks.length">暂无分片。</p><details v-for="chunk in detail.chunks" :key="chunk.id"><summary>分片 {{ chunk.ordinal + 1 }} · {{ chunk.page ? `第 ${chunk.page} 页` : '无固定页码' }} · {{ chunk.content.length }} 字符</summary><pre>{{ chunk.content }}</pre></details></section>
  </template>
</template>
