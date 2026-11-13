"""Remote protocol mocked; real tools, FAISS, persistence and budget guard execute."""
import asyncio
import json

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.core.live_test import Blocked, Stage
from app.database.models import Message
from app.llm.provider import CompatibleLLMProvider
from calculator_retest_guard import CalculatorRetestLedger
from calculator_retest_once import execute
from test_halt_diagnosis import kb
from test_local_learning import local_client
from test_development_budget import dev
from test_live_test_safety import payload, usage


def install(client, tool='calculator', bad=False, safety=None):
    seen = []

    def reply(request):
        body = json.loads(request.content)
        seen.append(body)
        if len(seen) == 1:
            arguments = {'expression': '(128 * 8) / 4'} if tool == 'calculator' else {'query': '进程', 'top_k': 1}
            message = {'role': 'assistant', 'content': None, 'tool_calls': [{
                'id': 'call-calculator-real-shape', 'type': 'function',
                'function': {'name': tool, 'arguments': json.dumps(arguments)},
            }]}
            reason = 'tool_calls'
        else:
            assert len(seen) == 2, 'Application failures must never retry'
            assert body['messages'][-1]['tool_call_id'] == 'call-calculator-real-shape'
            result = json.loads(body['messages'][-1]['content'])['result']
            system = body['messages'][0]['content']
            if tool == 'calculator':
                assert result['value'] == 256 and result['result_kind'] == 'calculation'
                assert 'citations' not in result and 'chunk_id' not in result
                assert '本次已成功调用的工具只有 calculator' in system
                assert '合法文档引用编号为空' in system
                answer = '计算结果为 256。[1]' if bad else '计算结果为 256。'
            else:
                source = result['citations'][0]
                assert source['chunk_id'] and '进程' in source['content']
                assert 'Chunk 引用编号仅为 [1]' in system
                answer = '进程是程序的一次执行。[999]' if bad else '进程是程序的一次执行。[1]'
            message = {'role': 'assistant', 'content': answer}
            reason = 'stop'
        data = {'choices': [{'message': message, 'finish_reason': reason}],
                'usage': {'prompt_tokens': 678 if len(seen) == 1 else 751,
                          'completion_tokens': 27 if len(seen) == 1 else 137}}
        if safety == 'unknown':
            data.pop('usage')
        if safety == 'protocol':
            data['choices'] = []
        return httpx.Response(200, json=data)

    client.app.state.agent.provider = CompatibleLLMProvider(client.app.state.settings, httpx.MockTransport(reply))
    return seen


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('persistent', [False, True])
@pytest.mark.parametrize('tool,bad', [('calculator', False), ('calculator', True),
                                    ('search_documents', False), ('search_documents', True)])
def test_current_sources_and_errors_across_agent_routes(local_client, stream, persistent, tool, bad):
    from uuid import uuid4
    client, ledger, _ = local_client
    ident = kb(client)
    seen = install(client, tool, bad)
    body = {'knowledge_base_id': ident, 'task': '计算 (128*8)/4' if tool == 'calculator' else '解释进程'}
    cid = None
    if persistent:
        cid = client.post('/api/conversations', json={'knowledge_base_id': ident, 'title': 'offline', 'mode': 'agent'}).json()['id']
        body.update(conversation_id=cid, request_id=str(uuid4()))
    response = client.post('/api/agent/stream' if stream else '/api/agent/run', json=body)
    assert len(seen) == 2
    if bad:
        assert 'LLM_INVALID_CITATION' in response.text
        assert '"type":"done"' not in response.text.replace(' ', '')
    elif stream:
        assert '"type":"done"' in response.text.replace(' ', '')
    else:
        assert response.status_code == 200, response.text
        citations = response.json()['citations']
        assert bool(citations) == (tool == 'search_documents')
        if citations:
            with Session(client.app.state.engine) as db:
                from app.database.models import Chunk
                chunk = db.get(Chunk, citations[0]['chunk_id'])
                assert chunk and chunk.content == citations[0]['content']
    if persistent:
        row = client.get(f'/api/conversations/{cid}/messages').json()['items'][-1]
        assert row['status'] == ('failed' if bad else 'completed')
        assert row['error_code'] == ('LLM_INVALID_CITATION' if bad else None)
    assert ledger.report()['state'] == 'READY'
    assert all(q['status'] == 'SUCCESS' and q['usage_status'] == 'KNOWN' for q in ledger.report()['requests'])


@pytest.mark.parametrize('bad', [False, True])
def test_runner_propagates_failure_without_global_halt_or_extra_calls(local_client, tmp_path, bad):
    client, ledger, _ = local_client
    ident = kb(client)
    seen = install(client, bad=bad)
    guard = CalculatorRetestLedger(ledger.path, ledger.run_id)
    output = tmp_path / 'trace.json'
    run = execute(client.app, guard, output, offline=True, knowledge_base_id=ident)
    if bad:
        with pytest.raises(DomainError) as error:
            asyncio.run(run)
        assert error.value.code == 'LLM_INVALID_CITATION'
    else:
        asyncio.run(run)
    trace = json.loads(output.read_text(encoding='utf8'))
    assert len(seen) == 2 and trace['history_status'] == ('failed' if bad else 'completed')
    assert trace['state'] == ('failed' if bad else 'completed')
    assert guard.report()['state'] == 'READY'
    with guard.transaction() as (db, _):
        events = [(q[0], q[1]) for q in db.execute('SELECT kind,reason FROM runtime_events WHERE run_id=?', (guard.run_id,))]
        assert not any(kind == 'SAFETY_HALT' for kind, _ in events)
        if bad:
            assert ('APPLICATION_FAILURE', 'LLM_INVALID_CITATION') in events
            assert ('REQUEST_STOPPED', 'LLM_INVALID_CITATION') in events
            assert trace['error_code'] == trace['history_error_code'] == 'LLM_INVALID_CITATION'
    before = guard.report()['used']
    with pytest.raises(Blocked):
        guard.reserve('agent', 'llm', payload('llm', 'agent'), offline=True)
    assert guard.report()['used'] == before and len(seen) == 2


@pytest.mark.parametrize('safety', ['unknown', 'protocol'])
def test_runner_still_halts_on_unsafe_response(local_client, tmp_path, safety):
    client, ledger, _ = local_client
    ident = kb(client)
    seen = install(client, safety=safety)
    guard = CalculatorRetestLedger(ledger.path, ledger.run_id)
    with pytest.raises(DomainError):
        asyncio.run(execute(client.app, guard, tmp_path / 'trace.json', offline=True, knowledge_base_id=ident))
    assert len(seen) == 1 and guard.report()['state'] == 'HALTED'
    assert guard.report()['requests'][-1]['usage_status'] == ('UNKNOWN' if safety == 'unknown' else 'KNOWN')


@pytest.mark.parametrize('unsafe', ['pending', 'unknown', 'cost', 'tokens', 'halted', 'unbounded'])
def test_opt_in_stage_does_not_downgrade_unsafe_billing(dev, unsafe):
    seq = dev.reserve('agent', 'llm', payload('llm', 'agent'), offline=True)
    dev.settle(seq, 200, usage('llm'))
    with pytest.raises(DomainError):
        with Stage(dev, 'agent', allow_application_failure=True) as scope:
            seq = dev.reserve('agent', 'llm', payload('llm', 'agent'), offline=True)
            dev.settle(seq, 200, usage('llm'))
            with dev.transaction() as (db, _):
                if unsafe == 'pending':db.execute("UPDATE requests SET status='PENDING' WHERE seq=?", (seq,))
                if unsafe == 'unknown':db.execute("UPDATE requests SET usage_status='UNKNOWN' WHERE seq=?", (seq,))
                if unsafe == 'cost':db.execute('UPDATE requests SET cost_nano=reserved_nano+1 WHERE seq=?', (seq,))
                if unsafe == 'tokens':db.execute('UPDATE requests SET output_tokens=output_bound+1 WHERE seq=?', (seq,))
                if unsafe == 'unbounded':db.execute('UPDATE requests SET reserved_nano=-1 WHERE seq=?', (seq,))
            if unsafe == 'halted':dev.halt('LLM_INVALID_TOOL_RESPONSE')
            scope.application_error = 'LLM_INVALID_CITATION'
            raise DomainError(502, 'LLM_INVALID_CITATION', 'offline')
    with dev.transaction() as (_, run):
        assert run['state'] == 'HALTED'


@pytest.mark.parametrize('opt_in,code', [(False, 'LLM_INVALID_CITATION'), (True, 'LLM_INVALID_TOOL_RESPONSE')])
def test_stage_downgrade_requires_opt_in_and_recognized_application_error(dev, opt_in, code):
    with pytest.raises(DomainError):
        with Stage(dev, 'agent', allow_application_failure=opt_in) as scope:
            scope.application_error = code
            raise DomainError(502, code, 'offline')
    assert dev.report()['state'] == 'HALTED'


def test_history_citation_ids_do_not_become_current_calculator_sources(local_client):
    from app.database.models import Conversation
    from uuid import uuid4
    client, ledger, _ = local_client
    ident = kb(client)
    seen = install(client, tool='search_documents')
    cid = client.post('/api/conversations', json={'knowledge_base_id': ident, 'title': 'history', 'mode': 'agent'}).json()['id']
    def request(task):
        return client.post('/api/agent/run', json={'knowledge_base_id': ident, 'task': task, 'conversation_id': cid, 'request_id': str(uuid4())})
    assert request('解释进程').status_code == 200
    with Session(client.app.state.engine) as db, db.begin():
        conversation = db.get(Conversation, cid)
        conversation.summary = '旧资料结论 [987]'
    seen = install(client)
    assert request('计算 (128*8)/4').status_code == 200
    history = json.loads(seen[0]['messages'][1]['content'])['conversation_context']
    assert '[1]' not in json.dumps(history) and '[987]' not in json.dumps(history)
    assert seen[1]['messages'][0]['content'].count('本轮工具证据与最终回答规则') == 1
    with Session(client.app.state.engine) as db:
        old = db.scalars(select(Message).where(Message.conversation_id == cid, Message.turn_no == 1, Message.role == 'assistant')).one()
        assert '[1]' in old.content  # Only the model context is sanitized; original history is intact.
    assert ledger.report()['state'] == 'READY'
