import pytest
from acceptance_batch import AcceptanceLedger
from test_development_budget import dev
from test_live_test_safety import payload,usage
from app.core.live_test import Blocked

def test_subbudget_persists_and_rejects_next_kind_attempt(dev):
    batch=AcceptanceLedger(dev.path,dev.run_id)
    for i in range(10):
        seq=batch.reserve('rag','llm',payload('llm','rag'),offline=True)
        batch.settle(seq,200,usage('llm'))
    with pytest.raises(Blocked):AcceptanceLedger(dev.path,dev.run_id).reserve('rag','llm',payload('llm','rag'),offline=True)
    assert batch.report()['used']==10
    for i in range(10):
        seq=batch.reserve('rag','embedding',payload('embedding','rag'),offline=True)
        batch.settle(seq,200,usage('embedding'))
    with pytest.raises(Blocked):batch.reserve('rag','embedding',payload('embedding','rag'),offline=True)
    assert batch.report()['used']==20

def test_money_rejection_rolls_back_before_send(dev):
    batch=AcceptanceLedger(dev.path,dev.run_id)
    with batch.transaction() as (c,r):c.execute('UPDATE run SET input_rate=10000000 WHERE id=?',(batch.run_id,))
    with pytest.raises(Blocked):batch.reserve('rag','llm',payload('llm','rag'),offline=True)
    assert batch.report()['used']==0

def test_hidden_summary_is_blocked_before_any_send(dev):
    batch=AcceptanceLedger(dev.path,dev.run_id);p=payload('llm','rag')
    p['messages']=[{'role':'user','content':'{"purpose":"conversation_summary"}'}]
    with pytest.raises(Blocked):batch.reserve('rag','llm',p,offline=True)
    assert batch.report()['used']==0
