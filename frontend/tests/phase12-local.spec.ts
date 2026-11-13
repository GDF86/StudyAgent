import {test,expect} from '@playwright/test'

test('Phase12 本机已熔断仍可保存偏好和浏览历史，禁止模型请求',async({page})=>{
 test.skip(process.env.STUDYAGENT_PHASE12_LOCAL!=='AUTHORIZED','默认不操作本地真实服务')
 await page.route('**/*',r=>{
  const req=r.request(),u=new URL(req.url())
  if(u.hostname!=='127.0.0.1' || !['5189','8010'].includes(u.port))return r.abort()
  if(!['GET','OPTIONS'].includes(req.method()) && !(req.method()==='PUT' && /^\/api\/memory\/(answer_style|learning_goal|preferred_language)$/.test(u.pathname)))return r.abort()
  return r.continue()
 })
 await page.goto('/preferences')
 await expect(page.getByLabel('本机模型运行状态')).toContainText('安全熔断')
 await expect(page.getByRole('button',{name:'重新授权模型',exact:true})).toHaveCount(0)
 const style=await page.getByLabel('回答风格').inputValue()
 await page.getByRole('button',{name:'保存偏好',exact:true}).click()
 await expect(page.getByRole('status')).toContainText('全局生效')
 await page.reload();await expect(page.getByLabel('回答风格')).toHaveValue(style)
 await page.screenshot({path:'test-results/phase12-evidence/local-preferences.png',fullPage:true})
 await page.goto('/conversations/98e6b890-1062-45a2-a068-f4b083830ded')
 await expect(page.locator('[data-role="assistant"][data-status="completed"]')).toHaveCount(2)
 await expect(page.getByLabel('本机模型运行状态')).toContainText('安全熔断')
})
