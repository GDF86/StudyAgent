"""Offline-only RAG grant and end-to-end safety regression."""
import json
import sqlite3
import pytest
import httpx
from app.core.live_test import Ledger, Stage, Blocked, provision
from test_live_test_safety import authorize, payload, send, usage, failed_parent
from rag_live_round import execute

def snapshot(path):
    with sqlite3.connect(path) as c:
        return {t:c.execute(f'SELECT * FROM {t} ORDER BY 1').fetchall() for t in ('run','requests','response_errors')}

@pytest.fixture
def historical(tmp_path):
    first=authorize(provision(tmp_path/'budget.sqlite'))
    failed_parent(first)
    second=authorize(first.begin_diagnostic(grant_id='old-two',console_permissions_confirmed=True))
    failed_parent(second)
    third=authorize(second.begin_diagnostic(grant_id='old-three',console_permissions_confirmed=True))
    failed_parent(third)
    fourth=authorize(third.begin_diagnostic(grant_id='old-four',console_permissions_confirmed=True))
    with Stage(fourth,'ingestion') as s:
        send(fourth,'ingestion','embedding');s.verify(True)
    return fourth

def new_round(parent):
    return authorize(parent.begin_rag_round(grant_id='new-rag-authorized'))

def finish_stage(ledger,stage):
    with Stage(ledger,stage) as scope:
        send(ledger,stage,'embedding')
        if stage!='ingestion': send(ledger,stage,'llm')
        scope.verify(True)

def test_history_four_and_seal_unchanged_and_sixth_denied(historical):
    before=snapshot(historical.path)
    seal=historical.path.with_suffix('.sqlite.seal').read_bytes()
    ledger=new_round(historical)
    assert ledger.report()['max_requests']==5 and ledger.report()['manual_budget_yuan']==5
    for stage in ('ingestion','rag','stream'):finish_stage(ledger,stage)
    restarted=Ledger(ledger.path,ledger.run_id)
    assert restarted.report()['state']=='COMPLETE' and restarted.report()['used']==5
    with pytest.raises(Blocked): restarted.reserve('agent','llm',payload('llm','agent'),offline=True)
    after=snapshot(historical.path)
    assert all(all(row in after[t] for row in rows) for t,rows in before.items())
    assert historical.path.with_suffix('.sqlite.seal').read_bytes()==seal
    assert restarted.report()['project_cost_nano'] is None
    assert restarted.report()['project_reserved_nano']>historical.report()['reserved_nano']

def test_grant_cannot_reuse(historical):
    ledger=new_round(historical); ledger.halt()
    with pytest.raises(Blocked,match='RAG_GRANT_ALREADY_USED'): new_round(historical)

@pytest.mark.parametrize('bad', ['pending','unknown_llm','under_reserved'])
def test_unbounded_history_refused(historical,bad):
    with historical.transaction() as (c,r):
        if bad=='pending':c.execute("UPDATE requests SET status='PENDING' WHERE seq=1")
        elif bad=='unknown_llm':c.execute("UPDATE requests SET kind='llm' WHERE seq=1")
        else:c.execute('UPDATE requests SET reserved_nano=1 WHERE seq=1')
    with pytest.raises(Blocked):new_round(historical)

def test_whole_round_project_admission(historical):
    with historical.transaction() as (c,r):c.execute('UPDATE requests SET reserved_nano=49000000000 WHERE seq=4')
    with pytest.raises(Blocked,match='PROJECT_MONEY_LIMIT'):new_round(historical)

def test_round_five_yuan_denied(historical):
    ledger=new_round(historical)
    with ledger.transaction() as (c,r):c.execute('UPDATE run SET embedding_rate=1000000 WHERE id=?',(ledger.run_id,))
    with pytest.raises(Blocked,match='ROUND_MONEY_LIMIT'):
        ledger.reserve('ingestion','embedding',payload('embedding','ingestion'),offline=True)
    assert ledger.report()['used']==0

def test_project_fifty_yuan_denied_at_send(historical):
    ledger=new_round(historical)
    with ledger.transaction() as (c,r):c.execute('UPDATE requests SET reserved_nano=50000000000 WHERE seq=4')
    with pytest.raises(Blocked,match='PROJECT_MONEY_LIMIT'):
        ledger.reserve('ingestion','embedding',payload('embedding','ingestion'),offline=True)
    assert ledger.report()['used']==0

def test_live_ceiling_quote_whole_round_is_reserved(historical):
    ledger=new_round(historical)
    # Temporary offline fixture only: exercise live math without any HTTP client.
    with ledger.transaction() as (c,r):c.execute('UPDATE run SET offline=0 WHERE id=?',(ledger.run_id,))
    for stage in ('ingestion','rag','stream'):
        with Stage(ledger,stage,offline=False) as scope:
            for kind in (('embedding',) if stage=='ingestion' else ('embedding','llm')):
                p=payload(kind,stage)
                if kind=='llm':p['max_tokens']=512
                seq=ledger.reserve(stage,kind,p,offline=False)
                ledger.settle(seq,200,usage(kind))
            scope.verify(True)
    assert ledger.report()['reserved_nano']==2424576000
    assert ledger.report()['state']=='COMPLETE'

@pytest.mark.parametrize('stage',['rag','stream'])
def test_no_extra_llm_per_stage(historical,stage):
    ledger=new_round(historical)
    finish_stage(ledger,'ingestion')
    if stage=='stream':finish_stage(ledger,'rag')
    before=ledger.report()['used']
    with pytest.raises(Blocked):
        with Stage(ledger,stage):
            send(ledger,stage,'embedding');send(ledger,stage,'llm');send(ledger,stage,'llm')
    assert ledger.report()['used']==before+2

@pytest.mark.parametrize('stage', ['ingestion','rag','stream'])
def test_no_extra_embedding_per_stage(historical,stage):
    ledger=new_round(historical)
    for s in ('ingestion','rag','stream'):
        if s==stage:break
        finish_stage(ledger,s)
    with pytest.raises(Blocked):
        with Stage(ledger,stage):
            send(ledger,stage,'embedding');send(ledger,stage,'embedding')

@pytest.mark.parametrize('feature',['tools','tool_message','agent','summary','reranker'])
def test_hidden_calls_denied(historical,feature):
    from app.core.live_test import forbid_extra,require_tool
    ledger=new_round(historical)
    finish_stage(ledger,'ingestion')
    before=ledger.report()['used']
    with pytest.raises(Blocked):
        with Stage(ledger,'rag'):
            if feature in ('summary','reranker'):forbid_extra(feature.upper()+'_FORBIDDEN')
            elif feature=='agent':require_tool('calculator')
            else:
                p=payload('llm','rag')
                if feature=='tools':p['tools']=[]
                else:p['messages']=[{'role':'tool','content':'unapproved'}]
                ledger.reserve('rag','llm',p,offline=True)
    assert ledger.report()['used']==before

def config(tmp_path,ledger):
    from app.core.config import Settings
    return Settings(_env_file=None,database_url='sqlite:///'+(tmp_path/'business.sqlite').as_posix(),data_dir=tmp_path/'data',
        llm_base_url='https://offline.invalid/v1',llm_model='qwen-flash',llm_api_key='OFFLINE',
        embedding_base_url='https://offline.invalid/v1',embedding_model='text-embedding-v4',embedding_api_key='OFFLINE',
        live_test_mode=True,live_test_ledger=ledger.path,llm_max_tokens=512,retrieval_min_score=0)

@pytest.mark.parametrize('interrupt',[False,True])
def test_offline_full_rag_http_and_stream_no_retry(tmp_path,historical,interrupt):
    ledger=new_round(historical)
    calls=[]
    def reply(req):
        p=json.loads(req.content);calls.append('embedding' if 'input' in p else 'llm')
        if 'input' in p:return httpx.Response(200,json={'data':[{'index':0,'embedding':[1.0]+[0.0]*1023}],'usage':usage('embedding')})
        if not p['stream']:return httpx.Response(200,json={'choices':[{'message':{'content':'项目代号为 SA-2026。[1]'},'finish_reason':'stop'}],'usage':usage('llm')})
        if interrupt:
            class Broken(httpx.AsyncByteStream):
                async def __aiter__(self):
                    yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
                    raise httpx.ReadTimeout('OFFLINE')
            return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Broken())
        events=[{'choices':[{'delta':{'content':'测试城市为深圳，测试编号为 RAG-1024。[1]'},'finish_reason':None}]},
                {'choices':[{'delta':{},'finish_reason':'stop'}]},{'choices':[],'usage':usage('llm')}]
        return httpx.Response(200,headers={'content-type':'text/event-stream'},content=''.join('data: '+json.dumps(e)+'\n\n' for e in events)+'data: [DONE]\n\n')
    transport=httpx.MockTransport(reply)
    result=execute(config(tmp_path,ledger),ledger,offline=True,embedding_transport=transport,llm_transport=transport)
    assert calls==['embedding','embedding','llm','embedding','llm'],result
    assert result['tests']=={'ingestion':'PASS','rag':'PASS','stream':'FAILED' if interrupt else 'PASS'},result
    assert result['ledger']['used']==5
    assert result['ledger']['state']==('HALTED' if interrupt else 'COMPLETE')
    if interrupt:assert result['ledger']['requests'][-1]['usage_status']=='UNKNOWN'
    else:assert all(r['usage_status']=='KNOWN' for r in result['ledger']['requests'])
