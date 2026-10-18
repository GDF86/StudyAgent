<script setup lang="ts">
import { onMounted } from 'vue'
import KnowledgeBaseCard from '../components/KnowledgeBaseCard.vue'
import { useKnowledgeBases } from '../stores/knowledgeBases'
const store = useKnowledgeBases()
onMounted(() => store.load())
</script>

<template>
  <header class="page-header"><span class="eyebrow">你的学习，从这里开始</span><h1>让知识，有处安放。</h1><p>按课程、研究方向或兴趣整理知识库，建立属于你的学习空间。</p></header>
  <section class="welcome panel">
    <div><span class="eyebrow">STUDYAGENT</span><h2>把零散的想法，<br />整理成清晰的主题。</h2><p>创建你的第一个知识库，为下一次学习留下入口。</p><RouterLink class="button" to="/knowledge-bases?create=1">＋ 创建知识库</RouterLink></div>
    <div class="welcome-art" aria-hidden="true"><div>学习笔记<span>01</span></div><div>研究课题<span>02</span></div><div>新的灵感<span>03</span></div></div>
  </section>
  <section aria-labelledby="recent-title">
    <div class="section-heading"><h2 id="recent-title">最近知识库</h2><RouterLink to="/knowledge-bases">查看全部 →</RouterLink></div>
    <p v-if="store.loading" role="status" class="muted">正在读取知识库…</p>
    <div v-else-if="store.error" class="panel error" role="alert">{{ store.error }} <button class="secondary" @click="store.load">重试</button></div>
    <div v-else-if="!store.items.length" class="panel empty"><h3>你的学习空间还是空的</h3><p>从一个熟悉的主题开始，创建第一个知识库。</p><RouterLink to="/knowledge-bases?create=1">创建知识库 →</RouterLink></div>
    <div v-else class="card-grid"><KnowledgeBaseCard v-for="item in store.items.slice(0, 3)" :key="item.id" :item="item" readonly /></div>
  </section>
</template>
