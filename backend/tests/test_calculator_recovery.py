import pytest
from app.core.calculator_recovery import TARGET,prepare,create,authorize,db_hash
from app.core.live_test import Ledger,Blocked
from test_local_learning import local_client
from test_personal_learning import start
from test_live_test_safety import payload,usage

@pytest.fixture
def incident(local_client):
    client,old,run,cfg,calls=start(local_client)
    for _ in range(18):
        seq=run.reserve('agent','llm',payload('llm','agent'),offline=True)
        run.settle(seq,200,usage('llm'))
    run.halt('AGENT_FAILED')
    with run.transaction() as (c,r):
        for table,col in [('run','id'),('requests','run_id'),('development_sessions','run_id'),('personal_runs','run_id'),('runtime_events','run_id')]:
            c.execute(f'UPDATE {table} SET {col}=? WHERE {col}=?',(TARGET,run.run_id))
        for _ in range(3):
            cols=[r[1] for r in c.execute('PRAGMA table_info(requests)') if r[1]!='seq']
            c.execute('INSERT INTO requests ('+','.join(cols)+') SELECT '+','.join(cols)+' FROM requests LIMIT 1')
            c.execute("UPDATE requests SET run_id=?,status='FAILED',usage_status='UNKNOWN',cost_nano=NULL,reserved_nano=1000000000 WHERE seq=last_insert_rowid()",(old.run_id,))
    return client,Ledger(run.path,TARGET),cfg,calls

def snapshot(run):
    with run.transaction() as (c,r):return db_hash(c)

def test_recovery_append_only_and_explicit_authorization(incident):
    client,old,cfg,calls=incident
    with old.transaction() as (c,r):
        original={t:[tuple(row) for row in c.execute('SELECT * FROM '+t+' ORDER BY rowid')] for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    before=snapshot(old);seal=old.path.with_suffix('.sqlite.seal').read_bytes()
    ticket=prepare(old.path,TARGET)
    assert snapshot(old)==before
    with pytest.raises(Blocked):create(old.path,ticket)
    new=create(old.path,ticket,True)
    with new.transaction() as (c,r):
        assert r['authorized']==0
        assert c.execute('SELECT state FROM run WHERE id=?',(TARGET,)).fetchone()[0]=='HALTED'
        assert c.execute('SELECT count(*) FROM requests').fetchone()[0]==21
    from app.core.personal_learning import ensure
    with pytest.raises(Blocked):ensure(new,cfg)
    with pytest.raises(Blocked):authorize(new,ticket,cfg)
    authorize(new,ticket,cfg,True)
    with pytest.raises(Blocked):authorize(new,ticket,cfg,True)
    with pytest.raises(Blocked):create(old.path,ticket,True)
    assert ensure(new,cfg).run_id==new.run_id
    with new.transaction() as (c,r):
        for table,rows in original.items():
            assert [tuple(row) for row in c.execute('SELECT * FROM '+table+' ORDER BY rowid')][:len(rows)]==rows
    assert old.path.with_suffix('.sqlite.seal').read_bytes()==seal and not calls

@pytest.mark.parametrize('change',['request','pending','halt','price','code','seal','target'])
def test_changed_evidence_rolls_back(incident,change,monkeypatch):
    _,old,cfg,calls=incident;ticket=prepare(old.path,TARGET)
    if change=='code':ticket['code_hash']='changed'
    elif change=='target':ticket['target']='wrong'
    elif change=='seal':old.path.with_suffix('.sqlite.seal').write_text('wrong')
    else:
        with old.transaction() as (c,r):
            if change=='request':c.execute('UPDATE requests SET elapsed_ms=999 WHERE seq=1')
            if change=='pending':c.execute("UPDATE requests SET status='PENDING' WHERE seq=1")
            if change=='halt':c.execute("UPDATE run SET state='COMPLETE' WHERE id=?",(TARGET,))
            if change=='price':c.execute('UPDATE personal_prices SET input_rate=999')
    before=old.path.read_bytes()
    with pytest.raises(Blocked):create(old.path,ticket,True)
    assert old.path.read_bytes()==before and not calls

def test_authorization_rechecks_history(incident):
    _,old,cfg,_=incident;ticket=prepare(old.path,TARGET);new=create(old.path,ticket,True)
    with new.transaction() as (c,r):c.execute('UPDATE requests SET elapsed_ms=999 WHERE seq=1')
    before=snapshot(new)
    with pytest.raises(Blocked):authorize(new,ticket,cfg,True)
    assert snapshot(new)==before

def test_create_rolls_back_after_insert(incident,monkeypatch):
    _,old,_,_=incident;ticket=prepare(old.path,TARGET);before=snapshot(old)
    import app.core.calculator_recovery as recovery
    original=recovery._begin_locked
    def fail(*args,**kwargs):
        original(*args,**kwargs)
        raise RuntimeError('injected after new run insertion')
    monkeypatch.setattr(recovery,'_begin_locked',fail)
    with pytest.raises(Blocked):create(old.path,ticket,True)
    assert snapshot(old)==before

def test_independent_runner_mock_closed_loop(local_client,tmp_path):
    import asyncio,json,httpx
    from types import SimpleNamespace
    from calculator_retest_once import execute
    from calculator_retest_guard import CalculatorRetestLedger
    from test_halt_diagnosis import kb
    from app.llm.provider import CompatibleLLMProvider
    client,old,_=local_client;ident=kb(client);seen=[]
    def reply(req):
        p=json.loads(req.content);seen.append(p)
        if len(seen)==1:
            message={'role':'assistant','content':None,'tool_calls':[{'id':'calc-1','type':'function','function':{'name':'calculator','arguments':json.dumps({'expression':'(128*8)/4'})}}]};reason='tool_calls'
        else:
            assert p['messages'][-1]['tool_call_id']=='calc-1'
            message={'role':'assistant','content':'256'};reason='stop'
        return httpx.Response(200,json={'choices':[{'message':message,'finish_reason':reason}],'usage':{'prompt_tokens':100,'completion_tokens':10}})
    client.app.state.agent.provider=CompatibleLLMProvider(client.app.state.settings,httpx.MockTransport(reply))
    guard=CalculatorRetestLedger(old.path,old.run_id);out=tmp_path/'trace.json'
    asyncio.run(execute(client.app,guard,out,offline=True,knowledge_base_id=ident))
    trace=json.loads(out.read_text(encoding='utf8'))
    assert len(seen)==2 and trace['state']=='completed' and trace['history_status']=='completed'
    assert trace['billing']['used']==3 and all(q['usage_status']=='KNOWN' for q in trace['billing']['requests'][-2:])
