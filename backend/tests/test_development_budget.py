import asyncio
import json
import multiprocessing
import time
import httpx
import pytest
from app.core.live_test import Ledger,Stage,Blocked,provision,GuardAsync
from test_live_test_safety import authorize,send,payload,settings,usage,response
from test_rag_live_round import historical,snapshot

@pytest.fixture
def dev(tmp_path):
    base=authorize(provision(tmp_path/'budget.sqlite'));base.halt()
    return authorize(base.begin_development_session(grant_id='dev',allowed_tools=['calculator']))

def test_history_and_new_session_without_reset(historical):
    before=snapshot(historical.path);seal=historical.path.with_suffix('.sqlite.seal').read_bytes()
    dev=authorize(historical.begin_development_session(grant_id='dev-history',allowed_tools=['calculator']))
    for _ in range(8):
        with Stage(dev,'rag') as s:send(dev,'rag','llm');s.verify(True)
    dev.complete_development_session()
    assert dev.report()['used']==8 and dev.report()['manual_budget_yuan'] is None
    after=snapshot(dev.path)
    assert all(all(row in after[t] for row in rows) for t,rows in before.items())
    assert dev.path.with_suffix('.sqlite.seal').read_bytes()==seal
    assert dev.report()['project_cost_nano'] is None
    with pytest.raises(Blocked):dev.begin_development_session(grant_id='dev-history')

@pytest.mark.parametrize('status',[429,500,503])
@pytest.mark.parametrize('recover',[False,True])
def test_retry_once_and_redebit(dev,status,recover):
    seen=[]
    def reply(req):
        seen.append(1)
        with dev.transaction() as (c,r):assert c.execute("SELECT count(*) FROM requests WHERE status='PENDING'").fetchone()[0]==1
        return response(req) if recover and len(seen)==2 else httpx.Response(status,json={'error':{'code':'offline-error'}})
    if recover:
        with Stage(dev,'rag') as s:send(dev,'rag','llm',httpx.MockTransport(reply));s.verify(True)
    else:
        with pytest.raises(Blocked):
            with Stage(dev,'rag'):send(dev,'rag','llm',httpx.MockTransport(reply))
    report=dev.report()
    assert len(seen)==report['used']==2
    assert report['requests'][0]['status']=='FAILED' and report['requests'][0]['cost_nano'] is None
    with dev.transaction() as (c,r):assert [x[0] for x in c.execute('SELECT attempt FROM development_requests ORDER BY seq')]==[0,1]

@pytest.mark.parametrize('status',[400,401,403,404,422])
def test_permanent_errors_no_retry(dev,status):
    seen=[]
    def fail(req):seen.append(1);return httpx.Response(status,json={})
    with pytest.raises(Blocked):
        with Stage(dev,'rag'):send(dev,'rag','llm',httpx.MockTransport(fail))
    assert len(seen)==dev.report()['used']==1

@pytest.mark.parametrize('error',[httpx.ConnectError,httpx.ConnectTimeout,httpx.ReadTimeout])
def test_network_retry_once(dev,error):
    seen=[]
    def fail(req):seen.append(1);raise error('OFFLINE')
    with pytest.raises(Blocked):
        with Stage(dev,'rag'):send(dev,'rag','llm',httpx.MockTransport(fail))
    assert len(seen)==dev.report()['used']==2

def test_async_retry_and_usage(dev):
    seen=[]
    async def reply(req):
        seen.append(1)
        return httpx.Response(429,json={}) if len(seen)==1 else response(req)
    async def go():
        with Stage(dev,'rag') as s:
            async with httpx.AsyncClient(transport=GuardAsync(settings(dev),'llm',httpx.MockTransport(reply)),trust_env=False) as c:
                await c.post('https://offline.invalid/v1/chat/completions',json=payload('llm','rag'))
            s.verify(True)
    asyncio.run(go())
    assert len(seen)==2 and dev.report()['requests'][-1]['usage_status']=='KNOWN'

def test_short_size_bound_and_long_rejected(dev):
    with Stage(dev,'rag') as s:send(dev,'rag','llm');s.verify(True)
    r=dev.report()['requests'][0]
    assert r['input_bound']<20000 and r['reserved_nano']<10000000
    p=payload('llm','rag');p['messages'][0]['content']='x'*20000
    with pytest.raises(Blocked,match='INPUT_TOO_LONG'):
        dev.reserve('rag','llm',p,offline=True)

def test_missing_usage_halts_without_retry(dev):
    seen=[]
    def bad(req):seen.append(1);return httpx.Response(200,json={})
    with pytest.raises(Blocked):
        with Stage(dev,'rag'):send(dev,'rag','llm',httpx.MockTransport(bad))
    assert len(seen)==1 and dev.report()['requests'][0]['cost_nano'] is None

def test_known_settlement_only_new_rows_reduce_risk(dev):
    with Stage(dev,'rag') as s:send(dev,'rag','llm');s.verify(True)
    r=dev.report()
    assert r['project_risk_nano']==r['cost_nano']<r['reserved_nano']

@pytest.mark.parametrize('amount,allowed',[(30000000000,True),(50000000000,False)])
def test_alert_and_fifty_cap(dev,amount,allowed,caplog):
    with Stage(dev,'rag') as s:send(dev,'rag','llm');s.verify(True)
    # Temporary fixture only: represent accumulated FAILED/UNKNOWN risk.
    with dev.transaction() as (c,r):c.execute("UPDATE requests SET status='FAILED',usage_status='UNKNOWN',cost_nano=NULL,reserved_nano=?",(amount,))
    if allowed:
        with Stage(dev,'rag') as s:send(dev,'rag','llm');s.verify(True)
        assert 'PROJECT_BUDGET_ALERT_30_CNY' in caplog.text and dev.report()['budget_alert_30_yuan']
    else:
        with pytest.raises(Blocked,match='PROJECT_MONEY_LIMIT'):dev.reserve('rag','llm',payload('llm','rag'),offline=True)
        assert dev.report()['used']==1

def _worker(path,run_id,queue):
    try:
        n=Ledger(path,run_id).reserve('rag','llm',payload('llm','rag'),offline=True)
        queue.put(n)
    except Blocked:queue.put(None)

def test_two_processes_atomic_cap_and_restart(dev):
    with Stage(dev,'rag') as s:send(dev,'rag','llm');s.verify(True)
    with dev.transaction() as (c,r):c.execute("UPDATE requests SET status='FAILED',usage_status='UNKNOWN',cost_nano=NULL,reserved_nano=49997000000")
    ctx=multiprocessing.get_context('spawn');q=ctx.Queue()
    jobs=[ctx.Process(target=_worker,args=(str(dev.path),dev.run_id,q)) for _ in range(2)]
    for p in jobs:p.start()
    for p in jobs:p.join(15);assert p.exitcode==0
    assert sum(q.get(timeout=2) is not None for _ in jobs)<=1
    assert Ledger(dev.path,dev.run_id).report()['project_risk_nano']<=50000000000

def test_tool_embedding_and_work_limits(dev):
    from app.core.live_test import require_tool
    with pytest.raises(Blocked):
        with Stage(dev,'agent'):require_tool('search_documents')
    new=authorize(dev.begin_development_session(grant_id='no-embedding',allowed_tools=['calculator'],allow_embedding=False,max_requests=8))
    with pytest.raises(Blocked):new.reserve('agent','embedding',payload('embedding','agent'),offline=True)
    new2=authorize(new.begin_development_session(grant_id='finite-work',max_requests=8))
    for _ in range(8):
        with Stage(new2,'rag') as s:send(new2,'rag','llm');s.verify(True)
    with pytest.raises(Blocked,match='SESSION_WORK_LIMIT'):new2.reserve('rag','llm',payload('llm','rag'),offline=True)

def test_failed_unknown_can_open_next_session_without_mutation(dev):
    with pytest.raises(Blocked):
        with Stage(dev,'rag'):send(dev,'rag','llm',httpx.MockTransport(lambda r:httpx.Response(403,json={})))
    before=dev.report()['requests'][0].copy()
    again=authorize(dev.begin_development_session(grant_id='next-after-failure'))
    assert dev.report()['requests'][0]==before
    assert again.report()['project_risk_nano']>=before['reserved_nano']

def test_stream_interruption_does_not_retry(dev):
    attempts=[]
    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
            raise httpx.ReadTimeout('OFFLINE')
    async def handler(req):
        attempts.append(1);return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Broken())
    async def run():
        with Stage(dev,'stream'):
            async with httpx.AsyncClient(transport=GuardAsync(settings(dev),'llm',httpx.MockTransport(handler)),trust_env=False) as c:
                async with c.stream('POST','https://offline.invalid/v1/chat/completions',json=payload('llm','stream')) as r:
                    async for _ in r.aiter_bytes():pass
    with pytest.raises(Blocked):asyncio.run(run())
    assert len(attempts)==dev.report()['used']==1

@pytest.mark.parametrize('bad',['none','wrong_tool','bad_arguments',None])
def test_real_agent_calculator_mock_roundtrip(dev,tmp_path,bad):
    from calculator_development_once import validate
    from app.main import create_app
    from fastapi.testclient import TestClient
    from app.llm.provider import CompatibleLLMProvider
    from test_rag_live_round import config
    cfg=config(tmp_path,dev).model_copy(update={'agent_max_rounds':2,'agent_max_tool_calls':1})
    attempts=[]
    def handler(req):
        p=json.loads(req.content);attempts.append(p)
        if len(attempts)==1:
            msg={'role':'assistant','content':None,'tool_calls':[{'id':'calc-1','type':'function','function':{'name':'search_documents' if bad=='wrong_tool' else 'calculator','arguments':'{}' if bad=='bad_arguments' else '{"expression":"(125+375)/4"}'}}]}
            if bad=='none':msg={'role':'assistant','content':'125'}
            return httpx.Response(200,json={'choices':[{'message':msg,'finish_reason':'stop' if bad=='none' else 'tool_calls'}],'usage':usage('llm')})
        assert p['messages'][-1]['role']=='tool' and json.loads(p['messages'][-1]['content'])['result']['value']==125
        return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':'计算结果为125。'},'finish_reason':'stop'}],'usage':usage('llm')})
    with TestClient(create_app(cfg)) as client:
        kb=client.post('/api/knowledge-bases',json={'name':'calculator-only'}).json()['id']
        client.app.state.agent.provider=CompatibleLLMProvider(cfg,httpx.MockTransport(handler))
        r=asyncio.run(validate(client.app.state.agent,kb,dev,offline=True))
    assert r['status']==('FAILED' if bad else 'PASS'),r
    assert len(attempts)==(1 if bad else 2)
    if not bad:assert r['tool_executed'] and r['result_returned'] and r['tool_result']['value']==125

def test_agent_loop_remains_bounded(dev,tmp_path):
    from app.main import create_app
    from fastapi.testclient import TestClient
    from app.llm.provider import CompatibleLLMProvider
    from types import SimpleNamespace
    from test_rag_live_round import config
    cfg=config(tmp_path,dev).model_copy(update={'agent_max_rounds':2,'agent_max_tool_calls':2})
    attempts=[]
    def loop(req):
        attempts.append(1)
        return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':'call-'+str(len(attempts)),'type':'function','function':{'name':'calculator','arguments':'{"expression":"1+1"}'}}]},'finish_reason':'tool_calls'}],'usage':usage('llm')})
    with TestClient(create_app(cfg)) as client:
        kb=client.post('/api/knowledge-bases',json={'name':'bounded'}).json()['id']
        agent=client.app.state.agent;agent.provider=CompatibleLLMProvider(cfg,httpx.MockTransport(loop))
        async def run():
            context,messages=await agent.prepare(SimpleNamespace(knowledge_base_id=kb,task='calculate'))
            with Stage(dev,'agent'):await agent.run(context,messages)
        with pytest.raises(Blocked):asyncio.run(run())
    assert len(attempts)==2 and dev.report()['state']=='HALTED'

def test_local_proxy_development_path_is_guarded(tmp_path):
    from test_model_proxy import fake_proxy
    from app.core.live_test import GuardSync
    from pydantic import SecretStr
    base=authorize(provision(tmp_path/'proxy-dev.sqlite',offline=False));base.halt()
    ledger=authorize(base.begin_development_session(grant_id='proxy-dev'))
    with fake_proxy() as (url,events):
        cfg=settings(ledger,model_http_proxy=SecretStr(url))
        with pytest.raises(Blocked):
            with Stage(ledger,'ingestion',offline=False):
                with httpx.Client(transport=GuardSync(cfg,'embedding',None),trust_env=False) as c:
                    c.post('https://offline.invalid/v1/embeddings',json=payload('embedding','ingestion'))
        assert len(events)==1
    assert ledger.report()['used']==1 and ledger.report()['requests'][0]['predebit']==1
