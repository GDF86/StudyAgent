import {test,expect} from '@playwright/test'
test('真实本机权限：创建与仅预览，不允许任何模型操作',async({page})=>{
 test.skip(process.env.STUDYAGENT_PERMISSION_CHECK!=='LOCAL_ONLY','默认不连接运行中的后端')
 let kb:string|undefined
 await page.route('**/*',route=>{
  const req=route.request();const u=new URL(req.url())
  if(u.hostname!=='127.0.0.1' || !['5189','8010'].includes(u.port))return route.abort()
  if(u.port==='8010' && !['GET','OPTIONS'].includes(req.method()) && !(req.method()==='POST' && (u.pathname==='/api/knowledge-bases' || u.pathname.endsWith('/documents/preview'))))return route.abort()
  return route.continue()
 })
 try {
  await page.goto('/knowledge-bases');await page.getByRole('button',{name:'＋ 创建知识库',exact:true}).click()
  await page.getByLabel('知识库名称').fill('网页权限验证-20261111');await page.getByRole('button',{name:'保存知识库'}).click()
  await expect(page.getByText('知识库已创建',{exact:true})).toBeVisible()
  await page.locator('.kb-card').filter({has:page.getByRole('heading',{name:'网页权限验证-20261111',exact:true})}).getByRole('link',{name:'打开文档 →',exact:true}).click()
  kb=page.url().match(/knowledge-bases\/(\d+)/)![1]
  await expect(page.getByText(/本机真实学习模式/)).toBeVisible()
  await page.locator('#document-file').setInputFiles({name:'public-check.txt',mimeType:'text/plain',buffer:Buffer.from('公开本地权限验证，只分析，不生成索引。')})
  await page.getByRole('button',{name:'分析资料（不调用模型）'}).click()
  await expect(page.getByLabel('文档处理工作量')).toContainText('未调用 Embedding')
  await expect(page.getByRole('button',{name:'确认并生成索引',exact:true})).toBeVisible()
  await expect(page.getByRole('alert')).toHaveCount(0)
  await page.screenshot({path:'test-results/permissions-local/preview-no-model.png',fullPage:true})
 } finally {
  if(kb){const r=await page.request.delete(`http://127.0.0.1:8010/api/knowledge-bases/${kb}`,{headers:{Origin:'http://127.0.0.1:5189','X-Confirm-Delete':'true'}});expect(r.status()).toBe(204)}
 }
})
