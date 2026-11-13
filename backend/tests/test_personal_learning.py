import json,time,multiprocessing
import pytest
from app.core.live_test import Ledger,Blocked
from app.core.personal_learning import ensure,is_personal
from test_local_learning import local_client

def start(local_client):
    c,old,calls=local_client
    old.complete_development_session()
    cfg=c.app.state.settings;cfg.personal_learning=True;cfg.web_demo_allow_session_management=True
    cfg.llm_base_url=cfg.embedding_base_url='https://offline.cn-beijing.maas.aliyuncs.com/compatible-mode/v1'
    data={'confirmed':True,'session_version':old.run_id,'region':'beijing','input_tier':'<=128K','llm_input_per_million':'0.15','llm_output_per_million':'1.5','embedding_input_per_million':'0.5'}
    r=c.post('/api/local-model-session/reauthorize',json=data)
    assert r.status_code==200,r.text
    new=Ledger(old.path,r.json()['session_version'])
    return c,old,new,cfg,calls

def test_persist_reuse_expiry_restart_and_no_old_authorization_changes(local_client):
    c,old,new,cfg,calls=start(local_client)
    assert old.report()['state']=='COMPLETE'
    with new.transaction() as (db,r):
        original_price=r['input_rate'];db.execute('UPDATE development_sessions SET expires=0 WHERE run_id=?',(new.run_id,))
    again=ensure(new,cfg)
    assert again.run_id!=new.run_id and new.report()['state']=='COMPLETE'
    assert ensure(Ledger(old.path,again.run_id),cfg).run_id==again.run_id
    with again.transaction() as (db,r):
        assert r['input_rate']==original_price and is_personal(db,again.run_id)
        assert db.execute('SELECT count(*) FROM personal_prices').fetchone()[0]==1
    assert not calls

def test_monitoring_over_50_keeps_guard_and_alerts(local_client,monkeypatch):
    import app.core.development_budget as budget
    c,old,new,cfg,calls=start(local_client)
    monkeypatch.setattr(budget,'risk',lambda c:60_000_000_000)
    kb=c.post('/api/knowledge-bases',json={'name':'monitor'}).json()['id']
    r=c.post(f'/api/knowledge-bases/{kb}/documents',files={'file':('public.txt','进程是程序的一次执行。'.encode())})
    assert r.status_code==201,r.text
    assert len(calls)==1
    with new.transaction() as (db,r):assert [q[0] for q in db.execute('SELECT threshold FROM personal_alerts ORDER BY threshold')]==[30,50]
    assert new.report()['requests'][0]['predebit']==1

def test_halt_and_configuration_change_never_auto_recover(local_client):
    c,old,new,cfg,calls=start(local_client)
    cfg.embedding_base_url='https://different.cn-beijing.maas.aliyuncs.com/compatible-mode/v1'
    assert c.get('/api/local-mode-status').json()['model_state']=='PRICE_CONFIGURATION_CHANGED'
    with pytest.raises(Blocked):ensure(new,cfg)
    new.halt('UNAUTHORIZED_TOOL')
    with pytest.raises(Blocked):ensure(new,cfg)
    assert new.report()['state']=='HALTED' and not calls

def test_pending_rejects_rollover_and_repricing(local_client):
    from test_live_test_safety import payload
    c,old,new,cfg,calls=start(local_client)
    new.reserve('rag','llm',payload('llm','rag'),offline=True)
    with pytest.raises(Blocked):ensure(new,cfg)
    assert new.report()['requests'][0]['status']=='PENDING' and not calls

def test_alert_change_is_versioned_without_refreshing_price_date(local_client):
    c,old,new,cfg,calls=start(local_client)
    with new.transaction() as (db,r):date=db.execute('SELECT confirmed FROM personal_prices').fetchone()[0]
    cfg.personal_alert_thresholds=[80]
    again=ensure(new,cfg)
    with again.transaction() as (db,r):
        prices=db.execute('SELECT * FROM personal_prices ORDER BY rowid').fetchall()
        assert len(prices)==2 and prices[-1]['confirmed']==date
        assert json.loads(prices[-1]['thresholds'])==[30,50,80]

def worker(path,ident,values,barrier,q):
    from types import SimpleNamespace
    barrier.wait()
    q.put(ensure(Ledger(path,ident),SimpleNamespace(**values)).run_id)

def test_two_processes_rollover_one_session(local_client):
    c,old,new,cfg,calls=start(local_client)
    with new.transaction() as (db,r):db.execute('UPDATE development_sessions SET expires=0 WHERE run_id=?',(new.run_id,))
    values={k:getattr(cfg,k) for k in ('llm_model','embedding_model','llm_base_url','embedding_base_url','enable_reranker','personal_alert_thresholds')}
    ctx=multiprocessing.get_context('spawn');barrier=ctx.Barrier(2);q=ctx.Queue()
    ps=[ctx.Process(target=worker,args=(str(new.path),new.run_id,values,barrier,q)) for _ in range(2)]
    for p in ps:p.start()
    for p in ps:p.join(20);assert p.exitcode==0
    assert q.get(timeout=2)==q.get(timeout=2)
    with new.transaction() as (db,r):assert db.execute("SELECT count(*) FROM run WHERE state='READY'").fetchone()[0]==1

def test_daily_limit_survives_session_rollover(local_client):
    from test_live_test_safety import payload
    c,old,new,cfg,calls=start(local_client)
    n=new.reserve('rag','llm',payload('llm','rag'),offline=True)
    new.settle(n,200,{'prompt_tokens':100,'completion_tokens':10})
    with new.transaction() as (db,r):
        row=dict(db.execute('SELECT * FROM requests WHERE seq=?',(n,)).fetchone())
        for i in range(199):
            row['seq']=1000+i
            db.execute('INSERT INTO requests('+','.join(row)+') VALUES ('+','.join('?' for k in row)+')',tuple(row.values()))
    with pytest.raises(Blocked):new.reserve('rag','llm',payload('llm','rag'),offline=True)
    assert new.report()['state']=='HALTED' and not calls

def test_unknown_usage_stops_without_retry_or_automatic_recovery(local_client):
    import httpx
    from app.rag.embeddings import CompatibleEmbeddingProvider
    c,old,new,cfg,calls=start(local_client);seen=[]
    def fail(req):seen.append(1);return httpx.Response(503,json={'error':{'code':'offline_failure'}})
    c.app.state.documents.embeddings=CompatibleEmbeddingProvider(cfg,httpx.MockTransport(fail))
    kb=c.post('/api/knowledge-bases',json={'name':'failure'}).json()['id']
    c.post(f'/api/knowledge-bases/{kb}/documents',files={'file':('public.txt',b'public')})
    assert len(seen)==1 and new.report()['state']=='HALTED'
    assert new.report()['requests'][-1]['usage_status']=='UNKNOWN'
    with pytest.raises(Blocked):ensure(new,cfg)


def test_preview_uses_saved_prices_after_old_price_deadline(local_client):
    c,old,new,cfg,calls=start(local_client)
    kb=c.post('/api/knowledge-bases',json={'name':'preview'}).json()['id']
    with new.transaction() as (db,r):db.execute('UPDATE run SET verified_until=0 WHERE id=?',(new.run_id,))
    r=c.post(f'/api/knowledge-bases/{kb}/documents/preview',files={'file':('public.txt',b'local-only')})
    assert r.status_code==200 and r.json()['estimated_reserve_yuan']>0 and not calls
