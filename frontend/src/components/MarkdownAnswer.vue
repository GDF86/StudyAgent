<script setup lang="ts">
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

const props = defineProps<{ text: string }>()
const markdown = new MarkdownIt({ html: false, linkify: false, breaks: true })
// Do not load model-supplied remote images or tracking URLs automatically.
markdown.renderer.rules.image = (tokens, index) => markdown.utils.escapeHtml(tokens[index]?.content || '')
const html = computed(() => DOMPurify.sanitize(markdown.render(props.text), {
  USE_PROFILES: { html: true }, FORBID_TAGS: ['img', 'iframe', 'form', 'input', 'style'],
}))
</script>

<template><div class="markdown-answer" v-html="html"></div></template>
