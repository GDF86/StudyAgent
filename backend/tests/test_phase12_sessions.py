import multiprocessing,time,json
import pytest
from app.core.live_test import Ledger,Blocked
from app.core.development_budget import renew_web_session
from test_local_learning import local_client
from test_live_test_safety import payload

URL='/api/local-model-session/reauthorize'

def expire(ledger):
    with ledger.transaction() as (c,r):c.execute('UPDATE run SET verified_until=0 WHERE id=?',(ledger.run_id,))

def form(c):
    return dict(session_version=c.get('/api/local-mode-status').json()['session_version'],confirmed=True,region='beijing',input_tier='<=128K',
                llm_input_per_million='0.15',llm_output_per_million='1.5',embedding_input_per_million='0.5')

def manager(c):c.app.state.settings.web_demo_allow_session_management=True

def test_local_operations_survive_expiry_and_model_halt(local_client):
    c,ledger,calls=local_client
    kb=c.post('/api/knowledge-bases',json={'name':'local'}).json()['id']
    cid=c.post('/api/conversations',json={'title':'local','knowledge_base_id':kb,'mode':'chat'}).json()['id']
    expire(ledger)
    for path in ('/api/knowledge-bases',f'/api/knowledge-bases/{kb}/documents',f'/api/conversations/{cid}/messages','/api/memory'):
        assert c.get(path).status_code==200
    assert c.put('/api/memory/answer_style',json={'value':'detailed'}).status_code==200
    assert c.get('/api/memory').json()['preferences']['answer_style']=='detailed'
    r=c.post(f'/api/knowledge-bases/{kb}/documents/preview',files={'file':('public.txt','进程调度测试。'.encode())})
    assert r.status_code==200 and r.json()['estimated_reserve_yuan'] is None and not r.json()['embedding_called']
    ledger.halt()
    assert c.put('/api/memory/learning_goal',json={'value':'exam'}).status_code==200
    assert not calls

@pytest.mark.parametrize('path,body',[
    ('/api/chat',{'knowledge_base_id':1,'question':'local'}),
    ('/api/chat/stream',{'knowledge_base_id':1,'question':'local'}),
    ('/api/agent/run',{'knowledge_base_id':1,'task':'local'}),
    ('/api/agent/stream',{'knowledge_base_id':1,'task':'local'}),
    ('/api/rag/compare',{'knowledge_base_id':1,'question':'local'}),
])
def test_expired_models_block_before_http(local_client,path,body):
    c,ledger,calls=local_client;expire(ledger)
    assert c.post(path,json=body).json()['code']=='MODEL_AUTH_REQUIRED'
    assert not calls and ledger.report()['used']==0 and ledger.report()['state']=='READY'

def test_upload_still_requires_authorization(local_client):
    c,ledger,calls=local_client;expire(ledger)
    r=c.post('/api/knowledge-bases/1/documents',files={'file':('public.txt',b'local')})
    assert r.status_code==403 and not calls

def test_atomic_reauthorize_then_guarded_query_and_stale_tab(local_client):
    c,ledger,calls=local_client;manager(c)
    kb=c.post('/api/knowledge-bases',json={'name':'local'}).json()['id']
    c.post(f'/api/knowledge-bases/{kb}/documents',files={'file':('public.txt','进程是程序的一次执行。'.encode())})
    before=ledger.report()['requests'];seal=ledger.path.with_suffix('.sqlite.seal').read_bytes()
    expire(ledger);data=form(c)
    r=c.post(URL,json=data);assert r.status_code==200 and r.json()['session_valid'],r.text
    current=Ledger(ledger.path,r.json()['session_version'])
    assert current.run_id!=ledger.run_id and ledger.report()['state']=='COMPLETE'
    assert ledger.report()['requests']==before and ledger.path.with_suffix('.sqlite.seal').read_bytes()==seal
    assert c.post(URL,json=data).status_code==409
    assert c.post('/api/chat',json={'knowledge_base_id':kb,'question':'进程'}).status_code==200
    assert current.report()['used']==2 and current.report()['project_risk_nano']>=ledger.report()['project_risk_nano']

@pytest.mark.parametrize('change',[{'confirmed':False},{'input_tier':'>128K'},{'region':'unknown'},{'llm_input_per_million':''},{'llm_input_per_million':'NaN'},{'llm_output_per_million':'0'},{'embedding_input_per_million':'0.000001'}])
def test_price_reconfirmation_is_required(local_client,change):
    c,ledger,calls=local_client;manager(c);expire(ledger);data=form(c);data.update(change)
    assert c.post(URL,json=data).status_code==400
    assert ledger.report()['state']=='READY' and not calls

def test_management_permissions_and_halted_state(local_client):
    c,ledger,calls=local_client;expire(ledger)
    assert c.post(URL,json=form(c)).status_code==403
    manager(c)
    assert c.post(URL,json=form(c),headers={'origin':'https://evil.invalid'}).status_code==403
    assert c.post(URL,json=form(c),headers={'host':'localhost:8010'}).status_code==403
    ledger.halt()
    assert c.post(URL,json=form(c)).status_code==409
    assert ledger.report()['state']=='HALTED' and not c.get('/api/local-mode-status').json()['can_reauthorize']
    assert not calls

def test_active_and_pending_sessions_not_replaced(local_client):
    c,ledger,calls=local_client;manager(c)
    assert c.post(URL,json=form(c)).json()['message']=='SESSION_STILL_VALID'
    ledger.reserve('rag','llm',payload('llm','rag'),offline=True)
    expire(ledger)
    assert c.post(URL,json=form(c)).json()['message']=='PENDING_REQUEST_REQUIRES_RECONCILIATION'
    assert ledger.report()['requests'][0]['status']=='PENDING'

def test_failed_renewal_rolls_back_old_session(local_client,monkeypatch):
    import app.core.development_budget as budget
    c,ledger,calls=local_client;manager(c);expire(ledger)
    monkeypatch.setattr(budget,'risk',lambda c:50_000_000_000)
    assert c.post(URL,json=form(c)).json()['message']=='PROJECT_MONEY_LIMIT'
    assert ledger.report()['state']=='READY' and not calls

def renew_worker(path,ident,barrier,q):
    barrier.wait()
    try:
        new=renew_web_session(Ledger(path,ident),ident,input_rate=150,output_rate=1500,embedding_rate=500,confirmed=True)
        q.put(new.run_id)
    except Blocked:q.put('blocked')

def test_two_processes_cannot_duplicate_authorization(local_client):
    c,ledger,calls=local_client;expire(ledger)
    ctx=multiprocessing.get_context('spawn');barrier=ctx.Barrier(2);q=ctx.Queue()
    ps=[ctx.Process(target=renew_worker,args=(str(ledger.path),ledger.run_id,barrier,q)) for _ in range(2)]
    for p in ps:p.start()
    for p in ps:p.join(20);assert p.exitcode==0
    values=[q.get(timeout=2) for p in ps];assert values.count('blocked')==1
    with ledger.transaction() as (db,r):assert db.execute("SELECT count(*) FROM run WHERE state='READY'").fetchone()[0]==1
    assert ledger.report()['used']==0

@pytest.mark.parametrize('state',['expired','complete','halted'])
def test_restart_can_serve_local_data_without_model_authorization(local_client,state):
    from app.core.web_demo import WebDemoMiddleware
    from fastapi.testclient import TestClient
    from app.main import create_app
    c,ledger,calls=local_client
    if state=='complete':ledger.complete_development_session()
    elif state=='halted':ledger.halt()
    else:expire(ledger)
    with TestClient(create_app(c.app.state.settings),base_url='http://127.0.0.1:8010',client=('127.0.0.1',123)) as restarted:
        assert restarted.get('/api/memory').status_code==200
        assert not restarted.get('/api/local-mode-status').json()['session_valid']
    assert not calls

def test_session_expiry_can_be_renewed_without_changing_old_price(local_client):
    c,ledger,calls=local_client;manager(c)
    with ledger.transaction() as (db,r):db.execute('UPDATE development_sessions SET expires=0 WHERE run_id=?',(ledger.run_id,))
    r=c.post(URL,json=form(c));assert r.status_code==200
    with ledger.transaction() as (db,old):assert old['input_rate']==150 and old['state']=='COMPLETE'
    assert r.json()['session_version']!=ledger.run_id

@pytest.mark.parametrize('path',['/api/chat','/api/chat/stream'])
def test_expiry_between_guard_and_middleware_is_not_safety_halt(local_client,path,monkeypatch):
    c,ledger,calls=local_client;manager(c)
    kb=c.post('/api/knowledge-bases',json={'name':'local'}).json()['id']
    c.post(f'/api/knowledge-bases/{kb}/documents',files={'file':('public.txt','进程是程序的一次执行。'.encode())})
    original=c.app.state.chat.provider._connection
    def expire_before_llm():
        expire(ledger)
        return original()
    monkeypatch.setattr(c.app.state.chat.provider,'_connection',expire_before_llm)
    before=len(calls)
    r=c.post(path,json={'knowledge_base_id':kb,'question':'进程'})
    assert r.status_code==403 or any(json.loads(line[5:]).get('code')=='MODEL_AUTH_REQUIRED' for line in r.text.splitlines() if line.startswith('data:'))
    assert len(calls)==before+1  # Only query embedding; expired LLM never leaves guard.
    assert ledger.report()['state']=='READY'
    assert c.post(URL,json=form(c)).status_code==200
