<script setup lang="ts">
import {onMounted,onBeforeUnmount,ref} from 'vue'
import {apiErrorMessage} from '../api/knowledgeBases'
interface Status {session_valid:boolean;model_state:string;session_version:string;can_reauthorize:boolean;management_enabled:boolean;project_risk_yuan:number;budget_alert:boolean;fee_reminders?:number[];personal_mode?:boolean;price_confirmed_at?:number|null;price_source?:string;policy_version?:string;session_known_cost_yuan?:number;halt_reason?:string|null;llm_configured?:boolean;embedding_configured?:boolean;known_cost_yuan?:number;session_expires_at?:number;price_expires_at?:number}
const base=(import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/,'')
const real=ref(false),state=ref<Status>(),error=ref(''),dialog=ref(false),busy=ref(false)
const input=ref(''),output=ref(''),embedding=ref(''),confirmed=ref(false),version=ref('')
const labels:Record<string,string>={PRICE_CONFIGURATION_CHANGED:'模型或地域配置已变化，请重新核对价格',READY:'模型授权有效',SESSION_EXPIRED:'模型会话已过期',PRICE_CONFIRMATION_EXPIRED:'价格确认已过期',REAUTHORIZATION_REQUIRED:'需要重新授权',SAFETY_HALTED:'安全熔断，禁止继续调用',PROJECT_MONEY_LIMIT:'累计风险预算已用尽',PENDING_REQUEST:'有请求正在处理或等待结算',LEDGER_UNAVAILABLE:'费用账本不可用，模型调用已关闭'}
let timer:ReturnType<typeof setInterval>|undefined,alive=true
async function refresh(){
 try{const r=await fetch(base+'/api/local-mode-status');if(!r.ok)throw new Error('模型状态暂不可读取，本地浏览仍可用')
 const data=await r.json();if(alive){state.value=data;error.value=''}}catch(e){if(alive)error.value=e instanceof Error?e.message:'无法读取模型状态'}
}
function open(){input.value='';output.value='';embedding.value='';confirmed.value=false;version.value=state.value?.session_version||'';dialog.value=true;error.value=''}
async function authorize(){
 if(busy.value || !confirmed.value)return
 busy.value=true;error.value=''
 try{
  const r=await fetch(base+'/api/local-model-session/reauthorize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_version:version.value,confirmed:confirmed.value,region:'beijing',input_tier:'<=128K',llm_input_per_million:input.value,llm_output_per_million:output.value,embedding_input_per_million:embedding.value})})
  const data=await r.json();if(!r.ok)throw new Error(apiErrorMessage(data))
  state.value=data;dialog.value=false
 }catch(e){error.value=e instanceof Error?e.message:'重新授权失败';await refreshAfterFailure()}
 finally{busy.value=false}
}
async function refreshAfterFailure(){const message=error.value;await refresh();error.value=message}
function visible(){if(document.visibilityState==='visible' && real.value)void refresh()}
onMounted(async()=>{
 try{const r=await fetch(base+'/api/runtime-capabilities');if(!r.ok)return;real.value=(await r.json()).local_real_mode===true
 if(real.value){await refresh();timer=setInterval(()=>void refresh(),30000);document.addEventListener('visibilitychange',visible)}}catch{/* Mock/default mode has no model management UI. */}
})
onBeforeUnmount(()=>{alive=false;if(timer)clearInterval(timer);document.removeEventListener('visibilitychange',visible)})
</script>
<template>
 <section v-if="real" class="model-status" :class="{warning:!state?.session_valid || state?.budget_alert}" aria-label="本机模型运行状态">
  <strong>{{state?.session_valid?'真实模型已连接 · 模型已就绪':'本机服务正常，模型暂不可调用'}}</strong>
  <details><summary>模型状态</summary><div class="status-details">
  <p v-if="state?.personal_mode">个人学习模式：金额仅监控提醒。{{state.price_source}} · {{state.price_confirmed_at?new Date(state.price_confirmed_at*1000).toLocaleString():'尚未确认'}}。价格可能变化，请定期重新核对；估算不等于实际扣款。</p>
  <p v-if="state?.personal_mode">本次会话已知费用估算：¥{{state.session_known_cost_yuan?.toFixed(6)}}</p>
  <p v-if="state">qwen-flash：{{state.llm_configured?'已配置':'未确认'}} · Embedding：{{state.embedding_configured?'已配置':'未确认'}}</p>
  <p v-if="state">会话有效期：{{state.session_expires_at?new Date(state.session_expires_at*1000).toLocaleString():'未知'}}{{state.personal_mode?'（到期按需自动续期）':''}}；价格确认：{{state.personal_mode && state.price_confirmed_at?'持续使用最近人工确认值':state.price_expires_at?new Date(state.price_expires_at*1000).toLocaleString():'待核价'}}</p>
  <p v-if="state">已知费用估算：{{state.known_cost_yuan===undefined?'未知':'¥'+state.known_cost_yuan.toFixed(6)}}（不含UNKNOWN实际费用）</p>
  <span v-if="state"> · {{labels[state.model_state] || state.model_state}} · 累计风险 ¥{{state.project_risk_yuan?.toFixed(6)}}{{state.personal_mode?'（金额提醒）':' / ¥50'}}</span>
  <p v-if="!state?.session_valid">可以继续浏览资料、历史会话和保存学习偏好；调用模型需要有效授权。</p>
  <p v-if="state?.model_state==='SAFETY_HALTED'">安全熔断：请先排查错误。网页不能恢复 HALTED 会话。</p>
  <p v-if="state?.halt_reason">原因记录：{{state.halt_reason==='LEGACY_REASON_NOT_RECORDED'?'旧版本没有保存熔断原因，需独立诊断，不能自动恢复。':state.halt_reason}}</p>
  <button type="button" class="secondary" :disabled="busy" @click="refresh">刷新模型状态</button>
  </div></details>
  <p v-if="state?.personal_mode && state.fee_reminders?.length" class="error">费用风险提醒已达到：{{state.fee_reminders.map(x=>'¥'+x).join('、')}}（非金额硬限额）</p>
  <p v-if="state?.budget_alert && !state.personal_mode" class="error">累计风险已达到 ¥30，请关注剩余预算。</p>
  <p v-if="state && !state.session_valid" class="error">{{labels[state.model_state] || state.model_state}}；可浏览本地资料，模型调用暂停。</p>
  <button v-if="state?.personal_mode && state.session_valid" type="button" class="secondary" @click="open">重新核对价格</button>
  <button v-if="state?.can_reauthorize" type="button" @click="open">重新授权模型</button>
  <p v-if="state && !state.management_enabled">本机模型管理未开启；开发者首次升级启动时需启用 --enable-session-management。</p>
  <p v-if="error" class="error" role="status">{{error}}</p>
  <form v-if="dialog" role="dialog" aria-label="确认当前模型价格并授权" @submit.prevent="authorize">
   <h2>核对当前价格并重新授权</h2>
   <p>请现在查看<a href="https://help.aliyun.com/zh/model-studio/qwen-flash" target="_blank" rel="noopener noreferrer">Qwen Flash 官方价格</a>和<a href="https://help.aliyun.com/zh/model-studio/model-pricing" target="_blank" rel="noopener noreferrer">Embedding 官方价格</a>，核对北京地域、qwen-flash 输入≤128K，以及 text-embedding-v4 实时文本计费。无法确认时请取消。</p>
   <p>以下为人工价格确认，后台不会代替你查询控制台。每项单位均为元/百万 Token，不沿用旧价格。</p>
   <label>qwen-flash 输入价格<input v-model="input" required inputmode="decimal" pattern="[0-9]+([.][0-9]{1,3})?" :disabled="busy" /></label>
   <label>qwen-flash 输出价格<input v-model="output" required inputmode="decimal" pattern="[0-9]+([.][0-9]{1,3})?" :disabled="busy" /></label>
   <label>text-embedding-v4 输入价格<input v-model="embedding" required inputmode="decimal" pattern="[0-9]+([.][0-9]{1,3})?" :disabled="busy" /></label>
   <label><input type="checkbox" v-model="confirmed" :disabled="busy" required />我已重新核对上述价格和档位。{{state?.personal_mode?'授权个人模式保存价格并自动续期，¥30/¥50仅提醒，不设累计金额硬限额。':'授权在项目累计¥50风险预算内调用模型。'}}本地估算不是平台硬性扣费上限。</label>
   <button :disabled="busy || !confirmed">确认价格并授权</button><button type="button" class="secondary" :disabled="busy" @click="dialog=false">取消</button>
  </form>
 </section>
</template>
<style scoped>
.model-status{position:relative;margin-bottom:16px;padding:8px 12px;border-bottom:1px solid #d6e0d7;color:#34584c;font-size:14px;line-height:1.7}.model-status.warning{border-left:3px solid #b06a37;background:#fff7ed}.model-status details{display:inline-block;margin-left:16px}.status-details{padding:12px 0;overflow-wrap:anywhere}.model-status summary{cursor:pointer;color:#28644e}.model-status .error{margin:6px 0}.model-status button{margin:8px 10px 0 0}.model-status form{border-top:1px solid #d6e0d7;margin-top:16px;padding-top:16px}.model-status label{display:block;margin:12px 0}.model-status input:not([type=checkbox]){display:block;width:220px;max-width:100%;margin-top:5px}
</style>
