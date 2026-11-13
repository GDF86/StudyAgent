<script setup lang="ts">
import type { KnowledgeBase } from '../types/knowledgeBase'
defineProps<{ item: KnowledgeBase; readonly?: boolean; disabled?: boolean }>()
defineEmits<{ edit: [item: KnowledgeBase]; remove: [item: KnowledgeBase] }>()
function formatDate(value: string) {
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium' }).format(new Date(value))
}
</script>

<template>
  <article class="panel kb-card">
    <div class="folder-icon" aria-hidden="true">▤</div>
    <h3>{{ item.name }}</h3>
    <p class="description">{{ item.description || '还没有描述，可以补充你的学习目标。' }}</p>
    <RouterLink :to="`/knowledge-bases/${item.id}`">打开文档 →</RouterLink>
    <RouterLink class="chat-entry" :to="`/knowledge-bases/${item.id}/chat`">开始问答 →</RouterLink>
    <div class="card-footer"><time :datetime="item.updated_at">更新于 {{ formatDate(item.updated_at) }}</time>
      <div v-if="!readonly" class="card-actions"><button class="text-button" :disabled="disabled" @click="$emit('edit', item)" :aria-label="`编辑 ${item.name}`">编辑</button><button class="text-button danger-text" :disabled="disabled" @click="$emit('remove', item)" :aria-label="`删除 ${item.name}`">删除</button></div>
    </div>
  </article>
</template>
