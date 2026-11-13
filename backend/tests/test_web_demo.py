import json
import pytest
from types import SimpleNamespace
from starlette.testclient import TestClient
from starlette.concurrency import run_in_threadpool
from app.core.live_test import CURRENT,provision
from app.core.web_demo import WebDemoMiddleware
from test_live_test_safety import authorize,payload,usage

@pytest.mark.parametrize('stream',[False,True])
def test_scope_covers_threadpool_stream_and_repeated_requests(tmp_path,stream):
    base=authorize(provision(tmp_path/'budget.sqlite',offline=False));base.halt()
    ledger=authorize(base.begin_development_session(grant_id='web',allowed_tools=['calculator']))
    async def app(scope,receive,send):
        if scope['type']=='lifespan':
            await receive();await send({'type':'lifespan.startup.complete'})
            await receive();await send({'type':'lifespan.shutdown.complete'});return
        assert CURRENT.get().ledger.run_id==ledger.run_id
        from app.core.live_test import controlled_payload
        assert controlled_payload({'max_tokens':2048})['max_tokens']==1024
        def model():
            s=CURRENT.get();p=payload('llm',s.stage);n=s.ledger.reserve(s.stage,'llm',p,offline=False);s.ledger.settle(n,200,usage('llm'))
        await run_in_threadpool(model)
        await send({'type':'http.response.start','status':200,'headers':[]})
        if stream:
            await send({'type':'http.response.body','body':b'data: {"type":"token","content":"ok"}\n\n','more_body':True})
            assert CURRENT.get() is not None
            await send({'type':'http.response.body','body':b'data: {"type":"done"}\n\n'})
        else:await send({'type':'http.response.body','body':b'{}'})
    cfg=SimpleNamespace(live_test_mode=True,live_test_ledger=ledger.path,web_demo_round_id=ledger.run_id,cors_origins=['http://127.0.0.1:5189'])
    with TestClient(WebDemoMiddleware(app,cfg),base_url='http://127.0.0.1:8010',client=('127.0.0.1',123)) as c:
        for _ in range(2):assert c.post('/api/chat/stream' if stream else '/api/chat',json={'question':'test'},headers={'origin':cfg.cors_origins[0]}).status_code==200
        assert c.post('/api/chat',json={},headers={'origin':'https://evil.invalid'}).status_code==403
        assert c.post('/api/chat',json={'conversation_id':'x'},headers={'origin':cfg.cors_origins[0]}).status_code==200
        assert c.post('/api/knowledge-bases',json={},headers={'origin':cfg.cors_origins[0]}).status_code==403
    assert ledger.report()['used']==3 and ledger.report()['state']=='READY'
    assert CURRENT.get() is None

def test_remote_and_missing_grant_refused(tmp_path):
    from app.core.config import Settings
    assert Settings(_env_file=None).web_demo_enabled is False
    with pytest.raises(RuntimeError):WebDemoMiddleware(None,SimpleNamespace(live_test_mode=False,live_test_ledger=tmp_path/'missing',web_demo_round_id=''))

def test_started_sse_error_is_explicit_and_halts(tmp_path):
    base=authorize(provision(tmp_path/'budget.sqlite',offline=False));base.halt()
    ledger=authorize(base.begin_development_session(grant_id='sse-failure'))
    async def app(scope,receive,send):
        await send({'type':'http.response.start','status':200,'headers':[(b'content-type',b'text/event-stream')]})
        raise RuntimeError('OFFLINE_ERROR_NOT_EXPOSED')
    cfg=SimpleNamespace(live_test_mode=True,live_test_ledger=ledger.path,web_demo_round_id=ledger.run_id,cors_origins=['http://127.0.0.1:5189'])
    c=TestClient(WebDemoMiddleware(app,cfg),base_url='http://127.0.0.1:8010',client=('127.0.0.1',123))
    r=c.post('/api/agent/stream',json={},headers={'origin':cfg.cors_origins[0]})
    assert '"type":"error"' in r.text and 'OFFLINE_ERROR_NOT_EXPOSED' not in r.text
    assert ledger.report()['state']=='HALTED'
