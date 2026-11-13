import json
import httpx,pytest
from fastapi.testclient import TestClient
from app.core.config import Settings
from app.core.live_test import provision,Stage
from test_live_test_safety import authorize

@pytest.fixture
def local_client(tmp_path,monkeypatch):
    import app.core.web_demo as web
    class OfflineStage(Stage):
        def __init__(self,ledger,stage,**kwargs):super().__init__(ledger,stage,offline=True)
    monkeypatch.setattr(web,'Stage',OfflineStage)
    base=authorize(provision(tmp_path/'budget.sqlite'));base.halt()
    ledger=authorize(base.begin_development_session(grant_id='local-learning',allowed_tools=['calculator','search_documents','summarize_document','compare_documents','generate_quiz']))
    cfg=Settings(_env_file=None,database_url='sqlite:///'+(tmp_path/'business.db').as_posix(),data_dir=tmp_path/'data',live_test_mode=True,live_test_ledger=ledger.path,web_demo_enabled=True,web_demo_allow_writes=True,web_demo_round_id=ledger.run_id,
        cors_origins=['http://127.0.0.1:5189'],max_upload_bytes=2*1024*1024,max_chunks=32,chunk_size=512,chunk_overlap=64,embedding_batch_size=8,
        embedding_model='text-embedding-v4',embedding_base_url='https://offline.invalid/v1',embedding_api_key='OFFLINE',llm_model='qwen-flash',llm_base_url='https://offline.invalid/v1',llm_api_key='OFFLINE',retrieval_min_score=0)
    calls=[]
    def reply(req):
        p=json.loads(req.content);calls.append(p)
        if 'input' in p:return httpx.Response(200,json={'data':[{'index':i,'embedding':[1.,0.]} for i in range(len(p['input']))],'usage':{'total_tokens':10}})
        if p.get('stream'):
            events=[{'choices':[{'delta':{'content':'进程是程序的一次执行。[1]'},'finish_reason':None}]},{'choices':[{'delta':{},'finish_reason':'stop'}]},{'choices':[],'usage':{'prompt_tokens':100,'completion_tokens':20}}]
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=''.join('data: '+json.dumps(x)+'\n\n' for x in events)+'data: [DONE]\n\n')
        return httpx.Response(200,json={'choices':[{'message':{'content':'进程是程序的一次执行。[1]'},'finish_reason':'stop'}],'usage':{'prompt_tokens':100,'completion_tokens':20}})
    from app.main import create_app
    from app.rag.embeddings import CompatibleEmbeddingProvider
    from app.llm.provider import CompatibleLLMProvider
    with TestClient(create_app(cfg),base_url='http://127.0.0.1:8010',client=('127.0.0.1',123),headers={'origin':cfg.cors_origins[0]}) as c:
        c.app.state.documents.embeddings=CompatibleEmbeddingProvider(cfg,httpx.MockTransport(reply))
        c.app.state.chat.provider=CompatibleLLMProvider(cfg,httpx.MockTransport(reply))
        yield c,ledger,calls

def test_create_edit_upload_rag_sse_delete(local_client):
    c,ledger,calls=local_client
    r=c.post('/api/knowledge-bases',json={'name':'操作系统'});assert r.status_code==201
    kb=r.json()['id'];assert c.put(f'/api/knowledge-bases/{kb}',json={'name':'操作系统','description':'学习资料'}).status_code==200
    body=('进程是程序的一次执行。线程是CPU调度的基本单位。'*100).encode()
    r=c.post(f'/api/knowledge-bases/{kb}/documents',files={'file':('os.txt',body,'text/plain')});assert r.status_code==201,r.text
    doc=r.json();assert doc['status']=='ready' and doc['chunk_count']>1
    for path in ('/api/chat','/api/chat/stream'):
        r=c.post(path,json={'knowledge_base_id':kb,'question':'什么是进程？'})
        assert r.status_code==200 and ('answered' in r.text if path.endswith('stream') else r.json()['citations'])
    assert c.delete('/api/documents/'+doc['id']).status_code==403
    assert c.delete('/api/documents/'+doc['id'],headers={'X-Confirm-Delete':'true'}).status_code==204
    assert c.delete(f'/api/knowledge-bases/{kb}',headers={'X-Confirm-Delete':'true'}).status_code==204
    assert ledger.report()['state']=='READY'

def test_validation_errors_do_not_halt_or_call_models(local_client):
    c,ledger,calls=local_client
    kb=c.post('/api/knowledge-bases',json={'name':'操作系统'}).json()['id']
    for name,body in [('bad.exe',b'bad'),('empty.txt',b''),('large.txt',b'x'*(2*1024*1024+1)),('chunks.txt',('学习'*20000).encode())]:
        assert c.post(f'/api/knowledge-bases/{kb}/documents',files={'file':(name,body)}).status_code>=400
    assert not calls and ledger.report()['state']=='READY'
    assert c.post('/api/knowledge-bases',json={'name':'操作系统'}).status_code==409
    assert ledger.report()['state']=='READY'

def test_reference_kb_protected_and_wrong_origin_denied(local_client):
    c,ledger,calls=local_client
    kb=c.post('/api/knowledge-bases',json={'name':'Bailian-RAG-Live-Test'}).json()['id']
    assert c.put(f'/api/knowledge-bases/{kb}',json={'name':'renamed'}).status_code==403
    assert c.delete(f'/api/knowledge-bases/{kb}',headers={'X-Confirm-Delete':'true'}).status_code==403
    assert c.post('/api/knowledge-bases',json={'name':'unsafe'},headers={'origin':'https://evil.invalid'}).status_code==403
    assert not calls and ledger.report()['state']=='READY'

def test_permission_diagnostics_and_readonly(local_client,caplog):
    c,ledger,calls=local_client
    c.app.state.settings.web_demo_allow_writes=False
    r=c.post('/api/knowledge-bases',json={'name':'PRIVATE_BODY_NOT_LOGGED'},headers={'Authorization':'PRIVATE_HEADER_NOT_LOGGED'})
    assert r.status_code==403 and r.json()['detail']['reason']=='WRITES_DISABLED'
    assert r.json()['detail']['writes_enabled'] is False
    assert 'PRIVATE_BODY_NOT_LOGGED' not in caplog.text and 'PRIVATE_HEADER_NOT_LOGGED' not in caplog.text
    assert not calls

def test_route_method_and_current_status(local_client):
    c,ledger,calls=local_client
    status=c.get('/api/local-mode-status').json()
    assert status['document_api_version']==2 and status['document_writes'] and status['session_valid']
    r=c.get('/api/knowledge-bases/1/documents/preview')
    assert r.status_code==405 and r.json()['detail']['reason']=='PREVIEW_METHOD_MISMATCH'
    assert r.json()['detail']['writes_enabled'] is True
    assert not calls

@pytest.mark.parametrize('expired',['session','price'])
def test_expired_session_or_price_allows_local_writes_not_models(local_client,expired):
    c,ledger,calls=local_client
    with ledger.transaction() as (db,r):
        if expired=='session':db.execute('UPDATE development_sessions SET expires=0 WHERE run_id=?',(ledger.run_id,))
        else:db.execute('UPDATE run SET verified_until=0 WHERE id=?',(ledger.run_id,))
    r=c.post('/api/knowledge-bases',json={'name':'blocked'})
    assert r.status_code==201
    r=c.post('/api/chat',json={'knowledge_base_id':r.json()['id'],'question':'blocked'})
    assert r.status_code==403 and r.json()['code']=='MODEL_AUTH_REQUIRED'
    assert not c.get('/api/local-mode-status').json()['session_valid'] and not calls

def test_host_remote_and_origin_still_blocked(local_client):
    c,ledger,calls=local_client
    assert c.post('/api/knowledge-bases',json={},headers={'host':'evil.invalid'}).json()['detail']['reason']=='NON_LOOPBACK_OR_HOST'
    assert c.post('/api/knowledge-bases',json={},headers={'origin':'https://evil.invalid'}).json()['detail']['reason']=='ORIGIN_MISMATCH'
    remote=TestClient(c.app,base_url='http://127.0.0.1:8010',client=('203.0.113.1',123))
    assert remote.post('/api/knowledge-bases',json={}).json()['detail']['reason']=='NON_LOOPBACK_OR_HOST'
    assert not calls
