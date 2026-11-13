import { test, expect } from '@playwright/test'

test('个人模式刷新不重复填写价格，超过50仅提醒',async({page})=>{
 let auth=0
 await page.route('**/api/runtime-capabilities',r=>r.fulfill({json:{local_real_mode:true}}))
 await page.route('**/api/local-mode-status',r=>r.fulfill({json:{personal_mode:true,session_valid:true,model_state:'READY',can_reauthorize:false,management_enabled:true,price_confirmed_at:1794360000,price_source:'最近一次人工确认价格',project_risk_yuan:60,budget_alert:true,fee_reminders:[30,50],known_cost_yuan:59,session_known_cost_yuan:0.1}}))
 await page.route('**/api/local-model-session/reauthorize',r=>{auth++;return r.abort()})
 await page.goto('/comparison');await page.reload()
 await expect(page.getByRole('dialog')).toHaveCount(0)
 await expect(page.getByLabel('本机模型运行状态')).toContainText('非金额硬限额')
 await page.getByLabel('本机模型运行状态').locator('summary').click()
 await expect(page.getByText(/最近一次人工确认价格/)).toBeVisible()
 expect(auth).toBe(0)
})

test('正常模型状态保持紧凑，详情可展开，移动端无水平溢出',async({page})=>{
 await page.route('**/api/runtime-capabilities',r=>r.fulfill({json:{local_real_mode:true}}))
 await page.route('**/api/local-mode-status',r=>r.fulfill({json:{session_valid:true,model_state:'READY',management_enabled:true,can_reauthorize:false,project_risk_yuan:2.5,llm_configured:true,embedding_configured:true,known_cost_yuan:0.02}}))
 await page.goto('/comparison')
 const bar=page.getByLabel('本机模型运行状态')
 await expect(bar).toContainText('真实模型已连接')
 expect((await bar.boundingBox())!.height).toBeLessThan(100)
 await bar.locator('summary').click();await expect(bar.getByText(/已知费用估算/)).toBeVisible()
 await bar.locator('summary').click();await page.setViewportSize({width:390,height:844})
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
 await page.screenshot({path:'test-results/learning-experience/mobile.png',fullPage:true})
})

test('输出上限仅提示当前回答失败，不伪装完成或权限失效，也不自动重试',async({page})=>{
 let calls=0
 await page.route('**/api/runtime-capabilities',r=>r.fulfill({json:{local_real_mode:true}}))
 await page.route('**/api/local-mode-status',r=>r.fulfill({json:{session_valid:true,model_state:'READY',management_enabled:true,can_reauthorize:false,project_risk_yuan:2.5}}))
 await page.route('**/api/chat',r=>{calls++;return r.fulfill({status:502,json:{code:'LLM_OUTPUT_LIMIT',message:'回答达到输出上限，未完整完成；本次调用仍计费。请缩小问题后主动提问。'}})})
 await page.goto('/knowledge-bases/2/chat');await page.getByLabel('使用流式回答').uncheck()
 await page.getByLabel('输入问题').fill('详细解释');await page.getByRole('button',{name:'发送问题'}).click()
 await expect(page.getByRole('alert')).toContainText('输出上限')
 await expect(page.getByText('回答完成',{exact:true})).toHaveCount(0)
 await expect(page.getByLabel('本机模型运行状态')).toContainText('模型已就绪');expect(calls).toBe(1)
})

const expiredModel={session_valid:false,model_state:'PRICE_CONFIRMATION_EXPIRED',session_version:'offline-version',can_reauthorize:true,management_enabled:true,project_risk_yuan:2.5,budget_alert:false}
test('模型授权过期可保存本地偏好，只有显式核价确认才续期',async({page})=>{
 let renewed=0,puts=0
 await page.route('**/api/runtime-capabilities',r=>r.fulfill({json:{local_real_mode:true}}))
 await page.route('**/api/local-mode-status',r=>r.fulfill({json:renewed?{...expiredModel,session_valid:true,model_state:'READY',can_reauthorize:false}:expiredModel}))
 await page.route('**/api/local-model-session/reauthorize',r=>{
  const data=r.request().postDataJSON();expect(data.confirmed).toBe(true);expect(data.llm_input_per_million).toBe('0.15');expect(data.session_version).toBe('offline-version')
  renewed++;return r.fulfill({json:{...expiredModel,session_valid:true,model_state:'READY',can_reauthorize:false}})
 })
 const prefs={answer_style:'concise',learning_goal:'general',preferred_language:'zh-CN'}
 await page.route('**/api/memory**',r=>{if(r.request().method()==='PUT')puts++;return r.fulfill({json:{preferences:prefs,defaults:prefs,overrides:{}}})})
 await page.goto('/preferences');await expect(page.getByLabel('本机模型运行状态')).toContainText('模型暂不可调用')
 await page.getByRole('button',{name:'保存偏好',exact:true}).click();await expect(page.getByRole('status')).toContainText('全局生效');expect(puts).toBe(3);expect(renewed).toBe(0)
 await page.getByRole('button',{name:'重新授权模型',exact:true}).click()
 const dialog=page.getByRole('dialog',{name:'确认当前模型价格并授权'})
 await expect(dialog.getByLabel('qwen-flash 输入价格')).toHaveValue('')
 await expect(dialog.getByRole('button',{name:'确认价格并授权'})).toBeDisabled()
 await dialog.getByLabel('qwen-flash 输入价格').fill('0.15');await dialog.getByLabel('qwen-flash 输出价格').fill('1.5');await dialog.getByLabel('text-embedding-v4 输入价格').fill('0.5')
 await dialog.getByRole('checkbox').check();await dialog.getByRole('button',{name:'确认价格并授权'}).click()
 await expect(page.getByLabel('本机模型运行状态')).toContainText('模型已就绪');expect(renewed).toBe(1)
})

test('模型安全熔断时不提供重新授权按钮',async({page})=>{
 await page.route('**/api/runtime-capabilities',r=>r.fulfill({json:{local_real_mode:true}}))
 await page.route('**/api/local-mode-status',r=>r.fulfill({json:{...expiredModel,model_state:'SAFETY_HALTED',can_reauthorize:false}}))
 await page.goto('/comparison');await expect(page.getByLabel('本机模型运行状态')).toContainText('安全熔断')
 await expect(page.getByRole('button',{name:'重新授权模型',exact:true})).toHaveCount(0)
})

test('学习偏好保存刷新恢复，明确全局作用域',async({page})=>{
 const defaults={answer_style:'concise',learning_goal:'general',preferred_language:'zh-CN'}
 const overrides:Record<string,string>={}
 let writes=0
 await page.route('**/api/memory**',r=>{
  const key=new URL(r.request().url()).pathname.split('/').at(-1)!
  if(r.request().method()==='PUT'){overrides[key]=r.request().postDataJSON().value;writes++}
  return r.fulfill({json:{defaults,overrides,preferences:{...defaults,...overrides}}})
 })
 await page.goto('/preferences')
 await page.getByLabel('回答风格').selectOption('detailed');await page.getByLabel('学习目标').selectOption('exam')
 await page.getByRole('button',{name:'保存偏好',exact:true}).click()
 await expect(page.getByRole('status')).toContainText('全局生效');expect(writes).toBe(3)
 await page.reload();await expect(page.getByLabel('回答风格')).toHaveValue('detailed')
 await expect(page.getByLabel('学习目标')).toHaveValue('exam');await expect(page.getByLabel('回答语言')).toHaveValue('zh-CN')
})

test('检索对比展示真实来源与未启用重排提示',async({page})=>{
 let requests=0
 await page.route('**/api/rag/compare',r=>{requests++;return r.fulfill({json:{candidate_k:20,runs:[{mode:'basic',citations:[{...citation,chunk_id:'second',document_name:'second.txt',content:'SA-2027'},citation],elapsed_ms:10,error_code:null},{mode:'reranked',citations:[],elapsed_ms:0,error_code:'RERANKER_NOT_ENABLED',error_message:'真实Reranker未启用，不执行重排'}]}})})
 await page.goto('/comparison');await page.getByLabel('问题',{exact:true}).fill('项目代号？')
 await page.getByRole('button',{name:'开始对比'}).click()
 await expect(page.locator('[data-mode="reranked"]')).toContainText('RERANKER_NOT_ENABLED')
 const source=page.locator('[data-mode="basic"] li').filter({hasText:'bailian-rag-fixture.txt'})
 await source.locator('summary').click();await expect(source.locator('.source-content')).toContainText('SA-2026')
 expect(requests).toBe(1)
})
const kb={id:2,name:'Bailian-RAG-Live-Test',description:'离线公开测试资料',document_count:2,created_at:'2026-11-10',updated_at:'2026-11-10'}
const citation={id:1,knowledge_base_id:2,document_id:'fixture',document_name:'bailian-rag-fixture.txt',page:null,chunk_id:'chunk',content:'项目代号 SA-2026，深圳，RAG-1024。',score:0.8}
const final={knowledge_base_id:2,answer:'项目代号 SA-2026 [1]。',citations:[citation],status:'answered'}
test.beforeEach(async({page})=>{
  await page.route('**/*',async route=>{
    const u=new URL(route.request().url())
    if(u.pathname.startsWith('/api/')){
      if(u.pathname==='/api/knowledge-bases')return route.fulfill({json:[kb]})
      if(u.pathname==='/api/knowledge-bases/2')return route.fulfill({json:kb})
      if(u.pathname==='/api/knowledge-bases/2/documents')return route.fulfill({json:[]})
      return route.fulfill({status:500,json:{message:'未定义的离线契约'}})
    }
    if(u.hostname==='127.0.0.1' && u.port==='5188')return route.continue()
    return route.abort()
  })
})
test('普通 RAG 显示引用并可展开',async({page})=>{
  await page.route('**/api/chat',r=>r.fulfill({json:final}))
  await page.goto('/knowledge-bases/2/chat')
  await page.getByLabel('使用流式回答').uncheck()
  await page.getByLabel('输入问题').fill('项目代号？');await page.getByRole('button',{name:'发送问题'}).click()
  await expect(page.locator('.markdown-answer')).toContainText('SA-2026')
  await page.locator('.citation-card summary').click()
  await expect(page.locator('.citation-content')).toContainText('深圳')
})
test('流式 token/citation/done 正常完成',async({page})=>{
  const events=[{type:'citation',data:citation},{type:'token',content:final.answer},{type:'done',...final}]
  await page.route('**/api/chat/stream',r=>r.fulfill({contentType:'text/event-stream',body:events.map(x=>'data: '+JSON.stringify(x)+'\r\n\r\n').join('')}))
  await page.goto('/knowledge-bases/2/chat');await page.getByLabel('输入问题').fill('项目代号？');await page.getByRole('button',{name:'发送问题'}).click()
  await expect(page.getByText('回答完成',{exact:true})).toBeVisible()
  await expect(page.locator('.citation-card')).toHaveCount(1)
})
test('最终跨库引用拒绝显示',async({page})=>{
  await page.route('**/api/chat',r=>r.fulfill({json:{...final,citations:[{...citation,knowledge_base_id:99}]}}))
  await page.goto('/knowledge-bases/2/chat');await page.getByLabel('使用流式回答').uncheck()
  await page.getByLabel('输入问题').fill('项目代号？');await page.getByRole('button',{name:'发送问题'}).click()
  await expect(page.getByRole('alert')).toContainText('引用知识库不匹配')
  await expect(page.locator('.citation-card')).toHaveCount(0)
})
test('Agent 状态、工具次数与引用',async({page})=>{
  const frames=[{type:'status',phase:'tool_started',tool:'search_documents',call:1,message:'开始执行工具'},{type:'status',phase:'tool_succeeded',tool:'search_documents',call:1,message:'工具执行完成'},{type:'done',...final,tool_calls:1}]
  await page.route('**/api/agent/stream',r=>r.fulfill({contentType:'text/event-stream',body:frames.map(x=>'data: '+JSON.stringify(x)+'\n\n').join('')}))
  await page.goto('/knowledge-bases/2/agent');await page.getByLabel('学习任务').fill('检索项目代号');await page.getByRole('button',{name:'执行任务',exact:true}).click()
  await expect(page.getByRole('heading',{name:'任务完成'})).toBeVisible()
  await expect(page.getByText('执行了 1 次工具调用')).toBeVisible()
  await expect(page.locator('[data-phase="tool_succeeded"]')).toHaveCount(1)
})
test('Agent 错误与非法完成事件不会伪装成功',async({page})=>{
  await page.route('**/api/agent/stream',r=>r.fulfill({contentType:'text/event-stream',body:'data: '+JSON.stringify({type:'done',...final,tool_calls:-1})+'\n\n'}))
  await page.goto('/knowledge-bases/2/agent');await page.getByLabel('学习任务').fill('计算');await page.getByRole('button',{name:'执行任务',exact:true}).click()
  await expect(page.getByRole('alert')).toContainText('完成事件格式无效')
  await expect(page.getByRole('heading',{name:'任务完成'})).toHaveCount(0)
})

const work={parsed_chars:26137,planned_chunks:49,embedding_requests:7,max_attempts_with_retry:14,estimated_reserve_yuan:0.159814,worst_reserve_with_retry_yuan:0.319628,embedding_called:false}
const caps={local_real_mode:true,document_writes:true,document_api_version:2,max_upload_bytes:2097152,max_chunks:128,embedding_batch_size:8}
const doc={id:'fixture-doc',knowledge_base_id:2,filename:'public.docx',file_type:'.docx',file_size:1024,status:'ready',error_message:null,chunk_count:49,embedding_dimension:1024,embedding_model:'text-embedding-v4',created_at:'2026-11-10T00:00:00Z'}
test('旧后端缺少新版接口时提示版本不一致而不是缺启动参数',async({page})=>{
 await page.route('**/api/runtime-capabilities',r=>r.fulfill({json:{...caps,document_api_version:undefined}}))
 await page.goto('/knowledge-bases/2');await expect(page.getByText(/本机真实学习模式/)).toBeVisible()
 await page.locator('#document-file').setInputFiles({name:'public.txt',mimeType:'text/plain',buffer:Buffer.from('MOCK')})
 await page.getByRole('button',{name:'分析资料（不调用模型）'}).click()
 await expect(page.getByRole('alert')).toContainText('与前端版本不一致')
})
test('真实模式先本地分析再确认，首次分析不提交索引',async({page})=>{
 let uploads=0
 await page.route('**/api/runtime-capabilities',r=>r.fulfill({json:caps}))
 await page.route('**/api/knowledge-bases/2/documents/preview',r=>r.fulfill({json:work}))
 await page.route('**/api/knowledge-bases/2/documents',r=>r.request().method()==='POST'?(uploads++,r.fulfill({status:201,json:doc})):r.fulfill({json:uploads?[doc]:[]}))
 await page.goto('/knowledge-bases/2');await expect(page.getByText(/本机真实学习模式/)).toBeVisible()
 await page.locator('#document-file').setInputFiles({name:'public.docx',mimeType:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',buffer:Buffer.from('MOCK')})
 await page.getByRole('button',{name:'分析资料（不调用模型）'}).click()
 await expect(page.getByLabel('文档处理工作量')).toContainText('49 个分片')
 expect(uploads).toBe(0)
 await page.getByRole('button',{name:'确认并生成索引',exact:true}).click()
 await expect(page.getByText('文档已解析，文本分片和向量已保存',{exact:true})).toBeVisible();expect(uploads).toBe(1)
})
test('失败文件分析不收费，确认重试才提交且携带确认头',async({page})=>{
 let retries=0
 await page.route('**/api/runtime-capabilities',r=>r.fulfill({json:caps}))
 await page.route('**/api/knowledge-bases/2/documents',r=>r.fulfill({json:[retries?doc:{...doc,status:'failed',chunk_count:0,error_message:'分片数超限'}]}))
 await page.route('**/api/documents/fixture-doc/processing-plan',r=>r.fulfill({json:work}))
 await page.route('**/api/documents/fixture-doc/retry',r=>{expect(r.request().headers()['x-confirm-retry']).toBe('true');retries++;return r.fulfill({json:doc})})
 await page.goto('/knowledge-bases/2');await page.getByRole('button',{name:'分析原文件与重试'}).click()
 await expect(page.getByLabel('文档处理工作量')).toContainText('7 次 Embedding');expect(retries).toBe(0)
 await page.getByRole('button',{name:'确认重试并生成索引'}).click()
 await expect(page.getByText('原文件已重新处理，索引已就绪',{exact:true})).toBeVisible();expect(retries).toBe(1)
})
