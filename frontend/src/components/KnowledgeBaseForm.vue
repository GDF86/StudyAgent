<script setup lang="ts">
import { onMounted, ref } from 'vue'
import type { KnowledgeBase, KnowledgeBaseInput } from '../types/knowledgeBase'

const props = defineProps<{ item?: KnowledgeBase; busy: boolean; error: string }>()
const emit = defineEmits<{ save: [body: KnowledgeBaseInput]; cancel: [] }>()
const name = ref(props.item?.name ?? '')
const description = ref(props.item?.description ?? '')
const nameInput = ref<HTMLInputElement>()
onMounted(() => nameInput.value?.focus())

function submit() {
  if (name.value.trim() && !props.busy) {
    emit('save', { name: name.value.trim(), description: description.value.trim() })
  }
}
</script>

<template>
  <form class="panel editor" @submit.prevent="submit" aria-labelledby="form-title" :aria-busy="busy">
    <div class="section-heading"><h2 id="form-title">{{ item ? '编辑知识库' : '创建知识库' }}</h2><span class="muted">用一个主题开始</span></div>
    <label for="kb-name">知识库名称 <span class="required">*</span></label>
    <input id="kb-name" ref="nameInput" v-model="name" required maxlength="100" :disabled="busy" placeholder="例如：机器学习课程" />
    <label for="kb-description">描述 <span class="muted">（选填）</span></label>
    <textarea id="kb-description" v-model="description" maxlength="2000" rows="3" :disabled="busy" placeholder="记录这个知识库的主题和学习目标" />
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <div class="actions"><button type="button" class="secondary" :disabled="busy" @click="emit('cancel')">取消</button><button type="submit" :disabled="busy || !name.trim()">{{ busy ? '保存中…' : '保存知识库' }}</button></div>
  </form>
</template>
