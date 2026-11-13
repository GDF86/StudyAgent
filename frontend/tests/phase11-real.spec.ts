import {test,expect} from '@playwright/test'
import {writeFileSync,mkdirSync,readFileSync} from 'node:fs'

test('Phase11 显式授权的历史、偏好与检索网页验收，失败即停',async({page})=>{
 test.skip(process.env.STUDYAGENT_PHASE11!=='AUTHORIZED','默认不连接真实模型')
 let modelOperations=0
 // Playwright clears outputDir on every invocation; keep resumable evidence outside it.
 const out='test-results/phase11-evidence';mkdirSync(out,{recursive:true})
 const resume=process.env.STUDYAGENT_PHASE11_RESUME==='COMPARISON'
 const report:Record<string,unknown>=resume?JSON.parse(readFileSync(out+'/results.json','utf8')):{}
 await page.route('**/*',r=>{
  const req=r.request(),u=new URL(req.url())
  if(u.hostname!=='127.0.0.1' || !['5189','8010'].includes(u.port))return r.abort()
  if(u.port==='8010' && req.method()==='POST' && ['/api/chat','/api/chat/stream','/api/rag/compare','/api/agent/stream'].includes(u.pathname)){
   if(++modelOperations>4)return r.abort()
  }else if(u.port==='8010' && !['GET','OPTIONS','PUT'].includes(req.method()) && u.pathname!=='/api/conversations')return r.abort()
  return r.continue()
 })
 const preference=async(style:string)=>{
  await page.goto('/preferences');await page.getByLabel('回答风格').selectOption(style)
  await page.getByLabel('学习目标').selectOption('exam');await page.getByLabel('回答语言').selectOption('zh-CN')
  await page.getByRole('button',{name:'保存偏好',exact:true}).click()
  await expect(page.getByRole('status')).toContainText('全局生效')
  await page.reload();await expect(page.getByLabel('回答风格')).toHaveValue(style)
  await expect(page.getByLabel('回答语言')).toHaveValue('zh-CN')
 }
 const create=async(mode:string)=>{
  await page.goto('/history');await page.getByLabel('会话标题',{exact:true}).fill('Phase11 '+mode+' 学习验证')
  await page.getByLabel('会话知识库',{exact:true}).selectOption('2');await page.getByLabel('会话模式',{exact:true}).selectOption(mode)
  await page.getByRole('button',{name:'创建会话',exact:true}).click();await expect(page).toHaveURL(/conversations\//)
  return page.url()
 }
 try {
  if(!resume){
  await preference('detailed')
  await page.screenshot({path:out+'/preferences.png',fullPage:true})
  const chat=await create('chat');report.chat_url=chat
  await page.getByLabel('流式回答',{exact:true}).uncheck()
  await page.getByLabel('学习问题或任务').fill('请根据 bailian-rag-fixture.txt（StudyAgent 测试资料）说明项目代号、测试城市和测试编号，并简要解释用途，引用该文件。')
  await page.getByRole('button',{name:'发送并保存'}).click()
  const replies=page.locator('[data-role="assistant"][data-status="completed"]')
  await expect(replies).toHaveCount(1,{timeout:60000});await expect(replies.first()).toContainText('SA-2026')
  report.detailed_answer=await replies.first().innerText()
  await page.reload();await expect(replies).toHaveCount(1)
  await expect(page.locator('.citation-card')).not.toHaveCount(0)
  await preference('concise')
  await page.goto(chat);await page.getByLabel('流式回答',{exact:true}).check()
  await page.getByLabel('学习问题或任务').fill('继续刚才的资料问答：只简洁列出 bailian-rag-fixture.txt 的测试城市和测试编号，请引用该文件。')
  await page.getByRole('button',{name:'发送并保存'}).click()
  await expect(replies).toHaveCount(2,{timeout:60000});await expect(replies.last()).toContainText('深圳');await expect(replies.last()).toContainText('RAG-1024')
  report.concise_answer=await replies.last().innerText()
  await page.reload();await expect(replies).toHaveCount(2)
  await page.locator('.citation-card summary').first().click();await page.screenshot({path:out+'/history.png',fullPage:true})
  }
  await page.goto('/comparison');await page.getByLabel('知识库',{exact:true}).selectOption('2')
  await page.getByLabel('问题',{exact:true}).fill('bailian-rag-fixture.txt 项目代号 SA-2026')
  await page.getByRole('button',{name:'开始对比'}).click()
  await expect(page.locator('[data-mode="reranked"]')).toContainText('RERANKER_NOT_ENABLED',{timeout:60000})
  await expect(page.locator('[data-mode="basic"]')).toContainText('bailian-rag-fixture.txt')
  const source=page.locator('[data-mode="basic"] li').filter({hasText:'bailian-rag-fixture.txt'})
  await source.locator('summary').click();await expect(source.locator('.source-content')).toContainText('SA-2026')
  report.comparison=await page.locator('.comparison-grid').innerText()
  await page.screenshot({path:out+'/comparison.png',fullPage:true})
  report.agent_url=await create('agent')
  const replies=page.locator('[data-role="assistant"][data-status="completed"]')
  await page.getByLabel('学习问题或任务').fill('请使用 calculator 工具计算 (125+375)/4，并告诉我结果。')
  await page.getByRole('button',{name:'发送并保存'}).click()
  await expect(replies).toHaveCount(1,{timeout:60000});await expect(replies).toContainText('125')
  report.agent_answer=await replies.innerText()
  await page.reload();await expect(replies).toContainText('125')
  await page.screenshot({path:out+'/agent-history.png',fullPage:true})
  report.passed=true
 }finally{report.model_operations=(resume?Number(report.model_operations||0):0)+modelOperations;writeFileSync(out+'/results.json',JSON.stringify(report,null,2))}
})

test('Phase11 只读复核已保存会话与偏好，不调用模型',async({page})=>{
 test.skip(process.env.STUDYAGENT_PHASE11_READONLY!=='AUTHORIZED','默认不读取本机真实数据库')
 const out='test-results/phase11-evidence'
 const report=JSON.parse(readFileSync(out+'/results.json','utf8'))
 await page.route('**/*',r=>{
  const u=new URL(r.request().url())
  return u.hostname==='127.0.0.1' && ['5189','8010'].includes(u.port) && ['GET','OPTIONS'].includes(r.request().method())?r.continue():r.abort()
 })
 await page.goto(report.chat_url)
 const replies=page.locator('[data-role="assistant"][data-status="completed"]')
 await expect(replies).toHaveCount(2)
 report.detailed_answer=await replies.first().innerText();report.concise_answer=await replies.last().innerText()
 await page.reload();await expect(replies).toHaveCount(2)
 await expect(page.locator('.citation-card')).not.toHaveCount(0)
 await page.locator('.citation-card summary').first().click()
 await page.screenshot({path:out+'/history.png',fullPage:true})
 await page.goto('/preferences')
 await expect(page.getByLabel('回答风格')).toHaveValue('concise')
 await expect(page.getByLabel('学习目标')).toHaveValue('exam')
 await expect(page.getByLabel('回答语言')).toHaveValue('zh-CN')
 await page.screenshot({path:out+'/preferences.png',fullPage:true})
 report.readback_passed=true
 writeFileSync(out+'/results.json',JSON.stringify(report,null,2))
})
