import pytest
from calculator_retest_guard import CalculatorRetestLedger
from app.core.live_test import Blocked
from test_development_budget import dev
from test_live_test_safety import payload,usage
from test_local_learning import local_client

def test_two_attempts_persist_and_third_is_blocked(dev):
    guard=CalculatorRetestLedger(dev.path,dev.run_id)
    for i in range(2):
        n=guard.reserve('agent','llm',payload('llm','agent'),offline=True)
        guard.settle(n,200,usage('llm'))
    with pytest.raises(Blocked):CalculatorRetestLedger(dev.path,dev.run_id).reserve('agent','llm',payload('llm','agent'),offline=True)
    assert guard.report()['used']==2

@pytest.mark.parametrize('bad',['embedding','tool','summary','money'])
def test_disallowed_call_has_zero_debits(dev,bad):
    guard=CalculatorRetestLedger(dev.path,dev.run_id);p=payload('llm','agent');kind='llm';stage='agent'
    if bad=='embedding':kind='embedding';p=payload(kind,stage)
    if bad=='tool':p['tools'][0]['function']['name']='search_documents'
    if bad=='summary':stage='rag'
    if bad=='money':
        with dev.transaction() as (c,r):c.execute('UPDATE run SET input_rate=10000000 WHERE id=?',(dev.run_id,))
    with pytest.raises(Blocked):guard.reserve(stage,kind,p,offline=True)
    assert guard.report()['used']==0

def test_failure_uses_one_and_blocks_further_attempts(dev):
    guard=CalculatorRetestLedger(dev.path,dev.run_id)
    n=guard.reserve('agent','llm',payload('llm','agent'),offline=True)
    with pytest.raises(Blocked):guard.settle(n,403,error='HTTP_403')
    with pytest.raises(Blocked):guard.reserve('agent','llm',payload('llm','agent'),offline=True)
    assert guard.report()['used']==1 and guard.report()['state']=='HALTED'

def test_old_twenty_batch_cannot_move_remaining_quota_to_new_run(dev):
    from acceptance_batch import AcceptanceLedger
    from test_live_test_safety import authorize
    old=AcceptanceLedger(dev.path,dev.run_id)
    n=old.reserve('agent','llm',payload('llm','agent'),offline=True);old.settle(n,200,usage('llm'))
    old.complete_development_session()
    new=authorize(old.begin_development_session(grant_id='isolated-next',allowed_tools=['calculator']))
    with pytest.raises(Blocked):AcceptanceLedger(new.path,new.run_id).reserve('agent','llm',payload('llm','agent'),offline=True)
    assert new.report()['used']==0 and old.report()['used']==1

@pytest.mark.parametrize('pending',[False,True])
def test_readonly_review_does_not_change_known_halt(local_client,pending):
    from test_personal_learning import start
    from calculator_recovery_review import inspect
    c,old,new,cfg,calls=start(local_client)
    n=new.reserve('agent','llm',payload('llm','agent'),offline=True)
    if not pending:new.settle(n,200,usage('llm'))
    new.halt('AGENT_FAILED')
    before=new.report()
    with new.transaction() as (db,r):result=inspect(db,new.run_id)
    assert result['financial_checks']==('BLOCKED' if pending else 'PASS')
    assert result['manual_approval_required'] and result['recovery_execution'].startswith('BLOCKED')
    assert new.report()==before
