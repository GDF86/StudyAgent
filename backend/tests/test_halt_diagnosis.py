import asyncio,json
from uuid import uuid4
import httpx,pytest
from test_local_learning import local_client
from test_live_test_safety import send,payload
from app.llm.provider import CompatibleLLMProvider

class Frames(httpx.AsyncByteStream):
    def __init__(self,events):self.events=events
    async def __aiter__(self):
        for item in self.events:
            await asyncio.sleep(0)
            yield ('data: '+(item if isinstance(item,str) else json.dumps(item))+'\n\n').encode()

def kb(c):
    ident=c.post('/api/knowledge-bases',json={'name':'halt-test'}).json()['id']
    assert c.post(f'/api/knowledge-bases/{ident}/documents',files={'file':('public.txt','进程是程序的一次执行。'.encode())}).status_code==201
    return ident

@pytest.mark.parametrize('stream',[False,True])
@pytest.mark.parametrize('persistent',[False,True])
def test_output_cap_known_usage_stops_only_task_not_session(local_client,stream,persistent):
    c,ledger,calls=local_client;ident=kb(c);seen=[]
    def response(req):
        seen.append(1);p=json.loads(req.content);reason='length' if len(seen)==1 else 'stop'
        usage={'prompt_tokens':100,'completion_tokens':1024 if reason=='length' else 20}
        if p['stream']:
            return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Frames([
                {'choices':[{'delta':{'content':'进程是程序的一次执行。[1]'},'finish_reason':None}]},
                {'choices':[{'delta':{},'finish_reason':reason}]},
                {'choices':[],'usage':usage},'[DONE]']))
        return httpx.Response(200,json={'choices':[{'message':{'content':'进程是程序的一次执行。[1]'},'finish_reason':reason}],'usage':usage})
    c.app.state.chat.provider=CompatibleLLMProvider(c.app.state.settings,httpx.MockTransport(response))
    body={'knowledge_base_id':ident,'question':'进程'}
    if persistent:
        cid=c.post('/api/conversations',json={'knowledge_base_id':ident,'mode':'chat','title':'public'}).json()['id']
        body.update(conversation_id=cid,request_id=str(uuid4()))
    r=c.post('/api/chat/stream' if stream else '/api/chat',json=body)
    assert 'LLM_OUTPUT_LIMIT' in r.text and len(seen)==1,r.text
    assert ledger.report()['state']=='READY'
    last=ledger.report()['requests'][-1]
    assert last['status']=='SUCCESS' and last['output_tokens']==1024 and last['cost_nano']>0
    if persistent:
        messages=c.get(f'/api/conversations/{cid}/messages').json()['items']
        assert messages[-1]['status']=='failed' and messages[-1]['error_code']=='LLM_OUTPUT_LIMIT'
        body['request_id']=str(uuid4())
    # Explicit user retry is a separate request and is debited, never automatic.
    again=c.post('/api/chat',json=body)
    assert again.status_code==200 and len(seen)==2 and ledger.report()['used']==5
    with ledger.transaction() as (db,r):
        events=[tuple(r) for r in db.execute('SELECT kind,reason FROM runtime_events')]
    assert ('MODEL_FINISH','LENGTH') in events and ('REQUEST_STOPPED','LLM_OUTPUT_LIMIT') in events

@pytest.mark.parametrize('stream',[False,True])
def test_unknown_usage_output_cap_remains_halted(local_client,stream):
    c,ledger,calls=local_client;ident=kb(c)
    def response(req):
        if stream:return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Frames([
            {'choices':[{'delta':{'content':'partial'},'finish_reason':'length'}]},'[DONE]']))
        return httpx.Response(200,json={'choices':[{'message':{'content':'partial'},'finish_reason':'length'}]})
    c.app.state.chat.provider=CompatibleLLMProvider(c.app.state.settings,httpx.MockTransport(response))
    c.post('/api/chat/stream' if stream else '/api/chat',json={'knowledge_base_id':ident,'question':'进程'})
    assert ledger.report()['state']=='HALTED' and ledger.report()['requests'][-1]['usage_status']=='UNKNOWN'

@pytest.mark.parametrize('pending',[False,True])
def test_disconnect_can_only_preserve_ready_after_known_settlement(local_client,pending):
    from app.core.web_demo import WebDemoMiddleware
    c,ledger,calls=local_client
    middleware=c.app.middleware_stack
    while not isinstance(middleware,WebDemoMiddleware):middleware=middleware.app
    async def app(scope,receive,send_event):
        await receive()
        if pending:ledger.reserve('rag','llm',payload('llm','rag'),offline=True)
        else:send(ledger,'rag','llm')
        await receive()  # Explicit disconnect, not an unexplained missing done event.
        await send_event({'type':'http.response.start','status':200,'headers':[]})
        await send_event({'type':'http.response.body','body':b'','more_body':True})
    middleware.app=app
    messages=iter([{'type':'http.request','body':b'{}','more_body':False},{'type':'http.disconnect'}])
    async def receive():return next(messages)
    async def sink(event):pass
    scope={'type':'http','method':'POST','path':'/api/chat','client':('127.0.0.1',1),'headers':[(b'host',b'127.0.0.1:8010'),(b'origin',b'http://127.0.0.1:5189')]}
    asyncio.run(middleware(scope,receive,sink))
    assert ledger.report()['state']==('HALTED' if pending else 'READY')
    if not pending:
        with ledger.transaction() as (db,r):assert db.execute("SELECT 1 FROM runtime_events WHERE reason='CLIENT_CANCELLED_WITH_KNOWN_USAGE'").fetchone()

def test_audit_contains_codes_not_private_response_text(local_client):
    c,ledger,calls=local_client
    ledger.record_event('PRIVATE request body','sk-private PRIVATE_MESSAGE')
    ledger.halt('PRIVATE-message-with-key')
    with ledger.transaction() as (db,r):
        rows=[tuple(r) for r in db.execute('SELECT kind,reason FROM runtime_events')]
    assert rows[-2:]==[('UNSPECIFIED','UNSPECIFIED'),('SAFETY_HALT','UNSPECIFIED')]
    assert c.get('/api/local-mode-status').json()['halt_reason']=='UNSPECIFIED'

def legacy_halt(ledger):
    from app.core.live_test import Stage
    with Stage(ledger,'rag') as scope:send(ledger,'rag','llm');scope.verify(True)
    # Simulate a historical ledger with no saved halt reason; temporary DB only.
    with ledger.transaction() as (db,r):db.execute("UPDATE run SET state='HALTED' WHERE id=?",(ledger.run_id,))

def test_reviewed_new_session_preserves_old_halt_and_is_not_authorized(local_client):
    from app.core.development_budget import create_reviewed_replacement
    from app.core.live_test import Blocked
    c,ledger,calls=local_client;legacy_halt(ledger)
    before=ledger.report();seal=ledger.path.with_suffix('.sqlite.seal').read_bytes()
    with pytest.raises(Blocked):create_reviewed_replacement(ledger,ledger.run_id,grant_id='review',reviewed=False)
    new=create_reviewed_replacement(ledger,ledger.run_id,grant_id='review',reviewed=True)
    assert ledger.report()==before and ledger.report()['state']=='HALTED'
    assert seal==ledger.path.with_suffix('.sqlite.seal').read_bytes()
    assert new.report()['project_risk_nano']==before['project_risk_nano']
    with new.transaction() as (db,r):assert r['authorized']==0 and r['verified_until'] is None
    with pytest.raises(Blocked):create_reviewed_replacement(ledger,ledger.run_id,grant_id='second',reviewed=True)

@pytest.mark.parametrize('fault',['pending','known_halt','unknown_usage','bound'])
def test_review_cannot_bypass_unresolved_guard_fault(local_client,fault):
    from app.core.development_budget import create_reviewed_replacement
    from app.core.live_test import Blocked
    c,ledger,calls=local_client;legacy_halt(ledger)
    if fault=='known_halt':ledger.halt('UNAUTHORIZED_TOOL')
    else:
        with ledger.transaction() as (db,r):
            if fault=='pending':db.execute("UPDATE requests SET status='PENDING' WHERE run_id=?",(ledger.run_id,))
            elif fault=='unknown_usage':db.execute("UPDATE requests SET usage_status='UNKNOWN',cost_nano=NULL WHERE run_id=?",(ledger.run_id,))
            else:db.execute('UPDATE requests SET input_tokens=input_bound+1 WHERE run_id=?',(ledger.run_id,))
    with pytest.raises(Blocked):create_reviewed_replacement(ledger,ledger.run_id,grant_id='blocked',reviewed=True)
    assert ledger.report()['state']=='HALTED'

def test_nested_tool_llm_output_cap_stops_without_extra_decision(local_client):
    c,ledger,calls=local_client;ident=kb(c)
    doc=c.get(f'/api/knowledge-bases/{ident}/documents').json()[0]['id'];seen=[]
    def response(req):
        seen.append(1)
        if len(seen)==1:
            message={'role':'assistant','content':None,'tool_calls':[{'id':'summary','type':'function','function':{'name':'summarize_document','arguments':json.dumps({'document_id':doc})}}]}
            reason='tool_calls';tokens=30
        else:message={'role':'assistant','content':'partial'};reason='length';tokens=1024
        return httpx.Response(200,json={'choices':[{'message':message,'finish_reason':reason}],'usage':{'prompt_tokens':100,'completion_tokens':tokens}})
    c.app.state.agent.provider=CompatibleLLMProvider(c.app.state.settings,httpx.MockTransport(response))
    r=c.post('/api/agent/run',json={'knowledge_base_id':ident,'task':'总结测试资料'})
    assert r.status_code==502 and r.json()['code']=='LLM_OUTPUT_LIMIT'
    assert len(seen)==2 and ledger.report()['state']=='READY'
    assert ledger.report()['used']==3  # ingestion + decision + tool summarization; no final/extra LLM.
