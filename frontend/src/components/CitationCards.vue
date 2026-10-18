<script setup lang="ts">
import type { Citation } from '../types/chat'
defineProps<{ citations: Citation[]; pending: boolean }>()
</script>

<template>
  <aside class="panel citations-panel" aria-label="引用来源">
    <h2>引用来源 <span class="count">{{ citations.length }}</span></h2>
    <p class="muted">{{ pending ? '回答生成中，以下是本次检索到的资料。' : '来自当前知识库的实际文本分片，点击查看原文。' }}</p>
    <p v-if="!citations.length" class="muted">这条回答暂无可引用的来源。</p>
    <details v-for="citation in citations" :key="citation.id" class="citation-card">
      <summary><strong>[{{ citation.id }}] {{ citation.document_name }}</strong><span>{{ citation.page === null ? '无固定页码' : `第 ${citation.page} 页` }}</span></summary>
      <p class="citation-content">{{ citation.content }}</p>
    </details>
  </aside>
</template>
