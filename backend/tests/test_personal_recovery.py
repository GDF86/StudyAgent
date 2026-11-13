"""Reuse existing recovery transaction and UI confirmation; never use production data."""
import json

import pytest
from fastapi.testclient import TestClient

from app.core.development_budget import create_reviewed_replacement
from app.core.live_test import Blocked
from app.core.personal_learning import ensure, review_settled_application_halt
from test_local_learning import local_client
from test_personal_learning import start
from test_live_test_safety import payload, usage


@pytest.fixture
def incident(local_client):
    client, _, old, cfg, calls = start(local_client)
    for _ in range(2):
        seq=old.reserve('agent','llm',payload('llm','agent'),offline=True)
        old.settle(seq,200,usage('llm'))
    old.record_event('APPLICATION_FAILURE','LLM_INVALID_CITATION')
    old.halt()
    return client,old,cfg,calls


def snapshot(ledger):
    with ledger.transaction() as (c,_):
        return {t:[tuple(r) for r in c.execute('SELECT * FROM "'+t+'" ORDER BY rowid')]
                for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def create(old,cfg,**kwargs):
    return create_reviewed_replacement(old,old.run_id,grant_id='personal-reviewed-offline',
        reviewed=True,personal_settings=cfg,application_fix_reviewed=True,**kwargs)


def test_personal_recovery_is_unapproved_until_ui_confirmation(incident):
    client,old,cfg,calls=incident
    before=snapshot(old);seal=old.path.with_suffix('.sqlite.seal').read_bytes()
    with old.transaction() as (c,r):
        assert review_settled_application_halt(c,r,old.run_id,cfg)['technical_checks_passed']
    assert snapshot(old)==before
    with pytest.raises(Blocked):
        create_reviewed_replacement(old,old.run_id,grant_id='no-approval',personal_settings=cfg)
    with pytest.raises(Blocked):
        create_reviewed_replacement(old,old.run_id,grant_id='no-fix-ack',reviewed=True,personal_settings=cfg)
    new=create(old,cfg)
    after=snapshot(new)
    assert all(after[t][:len(rows)]==rows for t,rows in before.items())
    with new.transaction() as (c,r):
        assert r['authorized']==0 and r['state']=='READY'
        policy=c.execute('SELECT * FROM development_sessions WHERE run_id=?',(new.run_id,)).fetchone()
        assert len(json.loads(policy['tools']))==5 and policy['embedding']==1 and policy['max_requests']==100
    with pytest.raises(Blocked):ensure(new,cfg)
    with new.transaction() as (_,r):assert r['authorized']==0
    from app.main import create_app
    restarted_cfg=cfg.model_copy(update={'web_demo_round_id':new.run_id})
    with TestClient(create_app(restarted_cfg),base_url='http://127.0.0.1:8010',client=('127.0.0.1',123),headers={'origin':cfg.cors_origins[0]}) as restarted:
        assert restarted.get('/api/local-mode-status').json()['can_reauthorize']
        data={'confirmed':True,'session_version':new.run_id,'region':'beijing','input_tier':'<=128K',
              'llm_input_per_million':'0.15','llm_output_per_million':'1.5','embedding_input_per_million':'0.5'}
        response=restarted.post('/api/local-model-session/reauthorize',json=data)
        assert response.status_code==200,response.text
        assert response.json()['session_version']==new.run_id and response.json()['session_valid']
    assert ensure(new,cfg).run_id==new.run_id
    assert len(snapshot(new)['run'])==len(before['run'])+1
    assert old.report()['state']=='HALTED' and new.report()['used']==0
    assert old.path.with_suffix('.sqlite.seal').read_bytes()==seal and not calls


@pytest.mark.parametrize('fault',['pending','unknown','protocol','configuration','latest'])
def test_personal_review_rejects_unsafe_evidence_without_writes(incident,fault):
    _,old,cfg,_=incident
    if fault=='configuration':cfg.llm_model='different'
    else:
        with old.transaction() as (c,_):
            if fault=='pending':c.execute("UPDATE requests SET status='PENDING' WHERE run_id=?",(old.run_id,))
            if fault=='unknown':c.execute("UPDATE requests SET usage_status='UNKNOWN',status='FAILED',cost_nano=NULL WHERE run_id=?",(old.run_id,))
            if fault=='protocol':c.execute("INSERT INTO runtime_events(run_id,created,kind,reason) VALUES (?,0,'SAFETY_HALT','LLM_INVALID_TOOL_RESPONSE')",(old.run_id,))
            if fault=='latest':c.execute("INSERT INTO run SELECT 'later-run',offline,authorized,'COMPLETE',stage,input_rate,output_rate,embedding_rate,verified_until,evidence_hash,budget_nano FROM run WHERE id=?",(old.run_id,))
    before=snapshot(old)
    with pytest.raises(Blocked):create(old,cfg)
    assert snapshot(old)==before


def test_personal_creation_rolls_back_after_insert(incident,monkeypatch):
    _,old,cfg,_=incident;before=snapshot(old)
    import app.core.development_budget as budget
    original=budget._begin_locked
    def fail(*args,**kwargs):
        original(*args,**kwargs)
        raise RuntimeError('offline injected interruption')
    monkeypatch.setattr(budget,'_begin_locked',fail)
    with pytest.raises(Blocked):create(old,cfg)
    assert snapshot(old)==before
