import json,asyncio
from uuid import uuid4
import httpx,pytest
from test_local_learning import local_client
from app.core.live_test import Stage
from app.llm.provider import CompatibleLLMProvider

def setup_kb(c):
    kb=c.post('/api/knowledge-bases',json={'name':'历史学习测试'}).json()['id']
    r=c.post(f'/api/knowledge-bases/{kb}/documents',files={'file':('public.txt','进程是程序的一次执行。线程是CPU调度的基本单位。'.encode())})
    assert r.status_code==201
    return kb

def test_history_save_replay_preferences_and_comparison(local_client):
    c,ledger,calls=local_client;kb=setup_kb(c)
    assert c.put('/api/memory/answer_style',json={'value':'detailed'}).status_code==200
    assert c.put('/api/memory/learning_goal',json={'value':'exam'}).status_code==200
    assert c.get('/api/memory').json()['preferences']['preferred_language']=='zh-CN'
    cid=c.post('/api/conversations',json={'knowledge_base_id':kb,'title':'学习','mode':'chat'}).json()['id']
    body={'knowledge_base_id':kb,'conversation_id':cid,'request_id':str(uuid4()),'question':'什么是进程？'}
    r=c.post('/api/chat/stream',json=body);assert r.status_code==200 and '"type": "done"' in r.text
    assert '详细解释' in calls[-1]['messages'][0]['content'] and '考试复习' in calls[-1]['messages'][0]['content']
    before=ledger.report()['used'];r=c.post('/api/chat/stream',json=body)
    assert ledger.report()['used']==before and '"type": "done"' in r.text
    assert c.get(f'/api/conversations/{cid}/messages').json()['total']==2
    assert c.put('/api/memory/answer_style',json={'value':'concise'}).status_code==200
    body.update(request_id=str(uuid4()),question='继续说明线程。')
    r=c.post('/api/chat',json=body);assert r.status_code==200,r.text
    msg=json.loads(calls[-1]['messages'][-1]['content'])
    assert msg['conversation_context']['recent_turns'] and '简洁' in calls[-1]['messages'][0]['content']
    assert c.get(f'/api/conversations/{cid}/messages').json()['total']==4
    before=ledger.report()['used'];r=c.post('/api/rag/compare',json={'knowledge_base_id':kb,'question':'进程','top_k':5})
    assert r.status_code==200 and ledger.report()['used']==before+1
    assert r.json()['runs'][0]['citations'] and r.json()['runs'][1]['error_code']=='RERANKER_NOT_ENABLED'
    assert c.delete(f'/api/conversations/{cid}').status_code==403
    assert c.delete(f'/api/conversations/{cid}',headers={'X-Confirm-Delete':'true'}).status_code==204

def test_persistent_agent_and_stateless_preferences(local_client):
    c,ledger,calls=local_client;kb=setup_kb(c)
    c.put('/api/memory/answer_style',json={'value':'detailed'})
    seen=[]
    def reply(req):
        p=json.loads(req.content);seen.append(p)
        assert '详细解释' in p['messages'][0]['content']
        if p['messages'][-1]['role']=='tool':msg={'role':'assistant','content':'结果125。'};finish='stop'
        else:msg={'role':'assistant','content':None,'tool_calls':[{'id':'calc','type':'function','function':{'name':'calculator','arguments':'{"expression":"(125+375)/4"}'}}]};finish='tool_calls'
        return httpx.Response(200,json={'choices':[{'message':msg,'finish_reason':finish}],'usage':{'prompt_tokens':100,'completion_tokens':20}})
    c.app.state.agent.provider=CompatibleLLMProvider(c.app.state.settings,httpx.MockTransport(reply))
    cid=c.post('/api/conversations',json={'knowledge_base_id':kb,'title':'Agent','mode':'agent'}).json()['id']
    r=c.post('/api/agent/run',json={'knowledge_base_id':kb,'conversation_id':cid,'request_id':str(uuid4()),'task':'计算125'})
    assert r.status_code==200,r.text
    assert r.json()['tool_calls']==1 and len(seen)==2
    assert json.loads(seen[0]['messages'][-1]['content']).get('conversation_context') is not None
    assert c.get(f'/api/conversations/{cid}/messages').json()['total']==2

def test_automatic_summary_is_guarded_and_limited(local_client):
    from app.schemas.conversation import MessageWrite
    c,ledger,calls=local_client;kb=setup_kb(c)
    cid=c.post('/api/conversations',json={'knowledge_base_id':kb,'title':'摘要','mode':'chat'}).json()['id']
    c.app.state.settings.memory_window_turns=1
    rows=[]
    for i in range(1,4):
        rows.extend([MessageWrite(role='user',content=f'问题{i}',turn_no=i),MessageWrite(role='assistant',content=f'回答{i}',turn_no=i)])
    c.app.state.conversations.save_messages(cid,kb,rows)
    before=ledger.report()['used']
    async def run():
        with Stage(ledger,'rag') as scope:
            scope.history_enabled=True
            result=await c.app.state.conversation_memory.context(cid,4,c.app.state.chat.provider)
            assert result['summary'];scope.verify(True)
    asyncio.run(run())
    assert ledger.report()['used']==before+1
    assert ledger.report()['requests'][-1]['kind']=='llm'

def test_cancel_endpoint_not_blocked_by_busy_gate(local_client):
    c,ledger,calls=local_client;kb=setup_kb(c)
    cid=c.post('/api/conversations',json={'knowledge_base_id':kb,'title':'取消','mode':'chat'}).json()['id']
    middleware=c.app.middleware_stack
    from app.core.web_demo import WebDemoMiddleware
    while not isinstance(middleware,WebDemoMiddleware):middleware=middleware.app
    middleware.busy=True
    # Cancellation must reach service validation even while generation owns busy.
    r=c.post(f'/api/conversations/{cid}/requests/{uuid4()}/cancel')
    assert r.status_code==404 and r.json()['code']=='REQUEST_NOT_FOUND'


@pytest.mark.parametrize('path,method,body',[
    ('/api/conversations','post',{'knowledge_base_id':1,'title':'local','mode':'chat'}),
    ('/api/memory/answer_style','put',{'value':'detailed'}),
    ('/api/rag/compare','post',{'knowledge_base_id':1,'question':'local'}),
])
def test_new_routes_retain_local_security(local_client,path,method,body):
    c,ledger,calls=local_client
    call=getattr(c,method)
    assert call(path,json=body,headers={'origin':'https://evil.invalid'}).status_code==403
    c.app.state.settings.web_demo_allow_writes=False
    assert call(path,json=body).json()['detail']['reason']=='WRITES_DISABLED'
    c.app.state.settings.web_demo_allow_writes=True
    ledger.halt()
    result=call(path,json=body)
    if path=='/api/rag/compare':assert result.json()['code']=='MODEL_AUTH_REQUIRED'
    else:assert result.status_code!=403  # Pure local writes remain available after model halt.
    assert not calls
