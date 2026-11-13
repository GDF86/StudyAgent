import pytest,json,httpx
from test_local_learning import local_client
from test_halt_diagnosis import kb
from app.llm.provider import CompatibleLLMProvider

@pytest.mark.parametrize('style,cap',[('concise',1024),('detailed',2048)])
@pytest.mark.parametrize('persistent',[False,True])
@pytest.mark.parametrize('stream',[False,True])
def test_learning_limits_match_guard_and_prompt(local_client,style,cap,stream,persistent):
    c,ledger,calls=local_client
    ident=c.post('/api/knowledge-bases',json={'name':'OS synthetic'}).json()['id']
    assert c.post(f'/api/knowledge-bases/{ident}/documents',files={'file':('public.txt','进程拥有独立资源；线程共享所属进程资源。'.encode())}).status_code==201
    c.app.state.settings.llm_max_tokens=2048
    c.put('/api/memory/answer_style',json={'value':style})
    seen=[]
    def reply(req):
        p=json.loads(req.content);seen.append(p)
        assert p['max_tokens']==cap and '对比题先列核心差异' in p['messages'][0]['content']
        usage={'prompt_tokens':100,'completion_tokens':30}
        text='进程有独立资源，线程共享进程资源。[1]'
        if p['stream']:
            frames=[{'choices':[{'delta':{'content':text},'finish_reason':None}]},{'choices':[{'delta':{},'finish_reason':'stop'}]},{'choices':[],'usage':usage}]
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=''.join('data: '+json.dumps(x)+'\n\n' for x in frames)+'data: [DONE]\n\n')
        return httpx.Response(200,json={'choices':[{'message':{'content':text},'finish_reason':'stop'}],'usage':usage})
    c.app.state.chat.provider=CompatibleLLMProvider(c.app.state.settings,httpx.MockTransport(reply))
    cid=c.post('/api/conversations',json={'knowledge_base_id':ident,'title':'OS','mode':'chat'}).json()['id']
    from uuid import uuid4
    body={'knowledge_base_id':ident,'question':'进程和线程有什么区别'}
    if persistent:body.update(conversation_id=cid,request_id=str(uuid4()))
    r=c.post('/api/chat/stream' if stream else '/api/chat',json=body)
    assert r.status_code==200 and len(seen)==1
    q=ledger.report()['requests'][-1]
    assert q['output_bound']==cap and q['reserved_nano']>=cap*1500
    if persistent:assert c.get(f'/api/conversations/{cid}/messages').json()['items'][-1]['status']=='completed'
