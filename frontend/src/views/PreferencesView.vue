<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { preferencesApi, describeError, type Preferences, type PreferenceState } from '../api/history'
const state = ref<PreferenceState>(), form = ref<Preferences>({ answer_style: 'concise', learning_goal: 'general', preferred_language: 'zh-CN' })
const busy = ref(false), loading = ref(true), error = ref(''), notice = ref('')
async function load() { state.value = await preferencesApi.get(); form.value = { ...state.value.preferences } }
async function save() {
  busy.value = true; error.value = ''; notice.value = ''
  try { for (const key of ['answer_style', 'learning_goal', 'preferred_language'] as const) await preferencesApi.set(key, form.value[key]); await load(); notice.value = '偏好已保存，将用于后续历史会话请求。' }
  catch (cause) { error.value = describeError(cause); await load().catch(() => undefined) }
  finally { busy.value = false }
}
async function reset(key: keyof Preferences) {
  busy.value = true; error.value = ''; notice.value = ''
  try { await preferencesApi.reset(key); await load(); notice.value = '已恢复默认值。' }
  catch (cause) { error.value = describeError(cause) }
  finally { busy.value = false }
}
onMounted(async () => { try { await load() } catch (cause) { error.value = describeError(cause) } finally { loading.value = false } })
</script>
<template>
  <header class="page-header"><span class="eyebrow">PREFERENCES</span><h1>学习偏好</h1><p>只保存你主动设置的选项，不会从聊天或文档自动提取偏好。</p></header>
  <p v-if="error" role="alert" class="error">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p><p v-if="loading" role="status">正在加载偏好…</p>
  <form v-if="state" class="panel history-panel" @submit.prevent="save">
    <div class="preference-row"><label>回答风格<select v-model="form.answer_style" :disabled="busy"><option value="concise">简洁</option><option value="detailed">详细</option></select></label><span>{{ state.overrides.answer_style === undefined ? '使用默认值' : '已主动设置' }}</span><button type="button" class="secondary" :disabled="busy" @click="reset('answer_style')">恢复默认风格</button></div>
    <div class="preference-row"><label>学习目标<select v-model="form.learning_goal" :disabled="busy"><option value="general">通用学习</option><option value="exam">考试复习</option><option value="research">研究学习</option></select></label><span>{{ state.overrides.learning_goal === undefined ? '使用默认值' : '已主动设置' }}</span><button type="button" class="secondary" :disabled="busy" @click="reset('learning_goal')">恢复默认目标</button></div>
    <div class="preference-row"><label>回答语言<select v-model="form.preferred_language" :disabled="busy"><option value="zh-CN">中文</option><option value="en">English</option></select></label><span>{{ state.overrides.preferred_language === undefined ? '使用默认值' : '已主动设置' }}</span><button type="button" class="secondary" :disabled="busy" @click="reset('preferred_language')">恢复默认语言</button></div>
    <button :disabled="busy">保存偏好</button><p class="muted">偏好只影响表达方式，不能改变资料范围、工具权限或引用规则。临时问答保留原有行为。</p>
  </form>
</template>
