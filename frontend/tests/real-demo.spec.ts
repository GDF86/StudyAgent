import {test,expect} from '@playwright/test'
test('显式授权的真实网页Demo，失败即停，不重试',async({page})=>{
 test.skip(process.env.STUDYAGENT_REAL_DEMO!=='AUTHORIZED','默认禁止真实调用')
 await page.route('**/*',r=>{
  const u=new URL(r.request().url());return u.hostname==='127.0.0.1' && ['5189','8010'].includes(u.port)?r.continue():r.abort()
 })
 const shot=async(name:string)=>page.screenshot({path:`../docs/screenshots/real-phase8/${name}.png`,fullPage:true})
 const resume=process.env.STUDYAGENT_DEMO_RESUME==='OUTPUT_FIX'
 if(!resume){
 await page.goto('/knowledge-bases/2')
 await expect(page.getByText('bailian-rag-fixture.txt',{exact:true})).toBeVisible()
 await expect(page.getByText('bailian-second-fixture.txt',{exact:true})).toBeVisible();await shot('01-documents')
 await page.goto('/knowledge-bases/2/chat');await page.getByLabel('使用流式回答').uncheck()
 await page.getByLabel('输入问题').fill('请根据 bailian-rag-fixture.txt（标题为 StudyAgent 测试资料）回答：项目代号是什么？请引用该文件来源。');await page.getByRole('button',{name:'发送问题'}).click()
 await expect(page.getByText('回答完成',{exact:true})).toBeVisible({timeout:60000})
 await expect(page.locator('.markdown-answer')).toContainText('SA-2026');await page.locator('.citation-card summary').first().click();await shot('02-rag')
 await page.getByLabel('使用流式回答').check();await page.getByLabel('输入问题').fill('请根据 bailian-rag-fixture.txt（标题为 StudyAgent 测试资料）回答：测试城市和测试编号分别是什么？请引用该文件来源。');await page.getByRole('button',{name:'发送问题'}).click()
 await expect(page.getByText('回答完成',{exact:true})).toHaveCount(2,{timeout:60000})
 await expect(page.locator('.exchange').last()).toContainText('深圳');await expect(page.locator('.exchange').last()).toContainText('RAG-1024');await shot('03-stream')
 }
 await page.goto('/knowledge-bases/2/agent')
 for(const [name,task,expected] of [
  ['04-calculator','请使用 calculator 工具计算 (125+375)/4，并告诉我结果。','125'],
  ['05-compare','请使用 compare_documents 工具比较目录中的两篇测试资料，说明代号、城市、编号、共同功能和新增功能。','SA-2027'],
  ['06-quiz','请使用 generate_quiz 工具，根据 StudyAgent 测试资料生成两道选择题，并给出答案。','答案']]){
   if(resume && name==='04-calculator')continue
   await page.getByLabel('学习任务').fill(task!);await page.getByRole('button',{name:'执行任务',exact:true}).click()
   await expect(page.getByRole('heading',{name:'任务完成'})).toBeVisible({timeout:60000})
   await expect(page.locator('.markdown-answer')).toContainText(expected!)
   await expect(page.locator('[data-phase="tool_succeeded"]')).toHaveCount(1)
   await expect(page.getByText('执行了 1 次工具调用')).toBeVisible();await shot(name!)
 }
})
