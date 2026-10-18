"""Phase 5 integration: real persistence/retrieval, local MockTransport models only."""
import asyncio
import json
from uuid import uuid4
import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.errors import DomainError
from app.database.models import Message
from app.llm.provider import CompatibleLLMProvider
from app.schemas.chat import ChatRequest
from app.main import create_app
from fastapi.testclient import TestClient
from test_chat import rig, events
from test_agent import install, turn, call


def conversation(rig, mode='chat'):
    response = rig[0].post('/api/conversations', json={'knowledge_base_id': rig[2], 'title': '学习历史', 'mode': mode})
    assert response.status_code == 201
    return response.json()['id']


def body(rig, cid, **extra):
    return {'knowledge_base_id': rig[2], 'question': 'ALPHA', 'conversation_id': cid, 'request_id': str(uuid4()), **extra}


def messages(client, cid):
    return client.get(f'/api/conversations/{cid}/messages').json()['items']


@pytest.mark.parametrize('stream', [False, True])
def test_persist_replay_context_preferences_and_snapshot(rig, stream):
    client, _, kb, doc, captured = rig
    cid = conversation(rig)
    client.put('/api/memory/preferred_language', json={'value': 'en'})
    request = body(rig, cid)
    path = '/api/chat/stream' if stream else '/api/chat'
    response = client.post(path, json=request)
    result = events(response)[-1] if stream else response.json()
    assert response.status_code == 200 and result['conversation_id'] == cid
    saved = messages(client, cid)
    assert len(saved) == 2 and all(m['status'] == 'completed' for m in saved)
    assert saved[1]['content'] == result['answer'] and saved[1]['citations'] == result['citations']
    count = len(captured)
    replay = client.post(path, json=request)
    assert (events(replay)[-1] if stream else replay.json()) == result
    assert len(captured) == count and len(messages(client, cid)) == 2
    assert client.post('/api/chat', json=body(rig, cid)).status_code == 200
    context = json.loads(captured[-1]['messages'][-1]['content'])['conversation_context']
    assert context['preferences']['preferred_language'] == 'en'
    assert len(context['recent_turns']) == 1 and '[1]' not in context['recent_turns'][0]['answer']
    assert client.delete(f"/api/documents/{doc['id']}").status_code == 204
    assert messages(client, cid)[1]['citations'] == result['citations']


def test_isolation_mode_and_request_conflict(rig):
    client = rig[0]; cid = conversation(rig); other = conversation(rig)
    request = body(rig, cid)
    assert client.post('/api/chat', json=request).status_code == 200
    assert client.post('/api/chat', json={**request, 'question': 'BETA'}).status_code == 409
    assert client.post('/api/chat', json=body(rig, other)).status_code == 200
    assert json.loads(rig[4][-1]['messages'][-1]['content'])['conversation_context']['recent_turns'] == []
    assert client.post('/api/chat', json=body(rig, cid, knowledge_base_id=9999)).status_code == 404
    assert client.post('/api/chat', json=body(rig, conversation(rig, 'agent'))).status_code == 409
    assert len(messages(client, cid)) == 2


def test_regeneration_retains_attempts_and_uses_latest(rig):
    client = rig[0]; cid = conversation(rig)
    assert client.post('/api/chat', json=body(rig, cid)).status_code == 200
    result = client.post('/api/chat', json=body(rig, cid, regenerate_turn=1))
    assert result.status_code == 200 and result.json()['attempt_no'] == 2
    assert len(messages(client, cid)) == 4
    assert client.post('/api/chat', json=body(rig, cid, regenerate_turn=1, question='BETA')).status_code == 409
    assert client.post('/api/chat', json=body(rig, cid)).status_code == 200
    history = json.loads(rig[4][-1]['messages'][-1]['content'])['conversation_context']['recent_turns']
    assert len(history) == 1
    assert client.post('/api/chat', json=body(rig, cid, regenerate_turn=1)).status_code == 409


@pytest.mark.parametrize('stream', [False, True])
def test_agent_persistence_and_memory(rig, stream):
    client = rig[0]; cid = conversation(rig, 'agent')
    captured = install(rig, [turn([call('calculator', {'expression': '6*7'})]), turn(answer='42')])
    request = {'knowledge_base_id': rig[2], 'task': '计算', 'conversation_id': cid, 'request_id': str(uuid4())}
    response = client.post('/api/agent/' + ('stream' if stream else 'run'), json=request)
    result = events(response)[-1] if stream else response.json()
    assert result['answer'] == '42' and result['tool_calls'] == 1
    assert messages(client, cid)[1]['content'] == '42'
    assert 'conversation_context' in json.loads(captured[0]['messages'][1]['content'])
    install(rig, [turn([call('calculator', {'expression': '42+1'})]), turn(answer='43')])
    assert client.post('/api/agent/run', json={**request, 'request_id': str(uuid4())}).status_code == 200
    assert len(messages(client, cid)) == 4


def summary_provider(rig, failure=None):
    client, settings, _, _, captured = rig
    original = client.app.state.chat.provider
    class Provider:
        async def complete(self, prompt):
            captured.append({'summary_prompt': prompt})
            if failure == 'error': raise DomainError(502, 'LLM_FAILED', 'mock')
            return '' if failure == 'empty' else ('x' * 5000 if failure == 'long' else '已讨论 ALPHA [1]')
        def stream(self, prompt): return original.stream(prompt)
    client.app.state.chat.provider = Provider()


def test_summary_window_incremental_and_original_preserved(rig):
    client, settings, _, _, captured = rig
    settings.memory_window_turns = 1
    cid = conversation(rig); summary_provider(rig)
    for _ in range(4): assert client.post('/api/chat', json=body(rig, cid)).status_code == 200
    detail = client.get(f'/api/conversations/{cid}').json()
    assert detail['summary_through_turn'] == 2 and '[1]' not in detail['summary']
    assert len(messages(client, cid)) == 8
    summaries = [x for x in captured if 'summary_prompt' in x]
    assert len(summaries) == 2
    assert json.loads(summaries[-1]['summary_prompt'][1]['content'])['previous_summary'] == detail['summary']
    history = json.loads([x for x in captured if 'messages' in x][-1]['messages'][-1]['content'])['conversation_context']
    assert [t['turn'] for t in history['recent_turns']] == [3]


@pytest.mark.parametrize('failure', ['error', 'empty', 'long'])
def test_failed_summary_keeps_original_and_watermark(rig, failure):
    client, settings, *_ = rig; settings.memory_window_turns = 1; cid = conversation(rig)
    for _ in range(2): assert client.post('/api/chat', json=body(rig, cid)).status_code == 200
    summary_provider(rig, failure)
    response = client.post('/api/chat', json=body(rig, cid))
    assert response.status_code == 502 and response.json()['code'] == 'MEMORY_SUMMARY_FAILED'
    assert client.get(f'/api/conversations/{cid}').json()['summary_through_turn'] == 0
    saved = messages(client, cid)
    assert len(saved) == 6 and saved[-1]['status'] == 'failed'
    assert all(m['status'] == 'completed' for m in saved[:4])


@pytest.mark.parametrize('budget', ['conversation_max_prompt_chars', 'conversation_max_prompt_tokens'])
def test_prompt_budget_prevents_llm_and_marks_failure(rig, budget):
    client, settings, *_ = rig; cid = conversation(rig)
    setattr(settings, budget, 1)
    response = client.post('/api/chat', json=body(rig, cid))
    assert response.status_code == 413
    assert not rig[4] and messages(client, cid)[1]['status'] == 'failed'


def test_failed_turn_excluded_from_memory(rig):
    client = rig[0]; cid = conversation(rig)
    provider = client.app.state.chat.provider
    client.app.state.chat.provider = CompatibleLLMProvider(rig[1], httpx.MockTransport(lambda r: httpx.Response(500)))
    failed = body(rig, cid)
    assert client.post('/api/chat', json=failed).status_code == 502
    assert client.post('/api/chat', json=failed).status_code == 502
    client.app.state.chat.provider = provider
    assert client.post('/api/chat', json=body(rig, cid)).status_code == 200
    assert json.loads(rig[4][-1]['messages'][-1]['content'])['conversation_context']['recent_turns'] == []


def test_restart_recovers_pending_and_keeps_completed(rig):
    client, settings, *_ = rig; cid = conversation(rig)
    assert client.post('/api/chat', json=body(rig, cid)).status_code == 200
    attempt, _ = client.app.state.execution.reserve(ChatRequest(**body(rig, cid)), 'chat')
    client.app.state.execution.release(attempt)
    with TestClient(create_app(settings)) as restarted:
        saved = messages(restarted, cid)
        assert saved[1]['status'] == 'completed' and saved[3]['status'] == 'interrupted'
        assert saved[3]['error_code'] == 'REQUEST_INTERRUPTED'


def test_busy_blocks_concurrent_requests_and_deletion(rig):
    client = rig[0]; cid = conversation(rig); execution = client.app.state.execution
    request = body(rig, cid)
    attempt, _ = execution.reserve(ChatRequest(**request), 'chat')
    try:
        assert client.post('/api/chat', json=request).status_code == 409
        assert client.post('/api/chat', json=body(rig, cid)).status_code == 409
        assert client.delete(f'/api/conversations/{cid}').status_code == 409
        assert client.put(f'/api/conversations/{cid}', json={'title':'更名'}).status_code == 409
        assert client.delete(f'/api/knowledge-bases/{rig[2]}').status_code == 409
    finally:
        execution.fail(attempt, 'REQUEST_CANCELLED', 'cancelled'); execution.release(attempt)
    assert client.delete(f'/api/conversations/{cid}').status_code == 204


def test_cancellation_releases_and_persists(rig):
    client = rig[0]; cid = conversation(rig); execution = client.app.state.execution
    request = body(rig, cid)
    async def scenario():
        started = asyncio.Event()
        async def blocked(*args):
            started.set(); await asyncio.Event().wait()
        client.app.state.conversation_memory.context = blocked
        async def consume():
            async for _ in execution.events(client.app, ChatRequest(**request), 'chat'): pass
        task = asyncio.create_task(consume())
        await started.wait()
        execution.cancel(cid, request['request_id'])
        with pytest.raises(asyncio.CancelledError): await task
    asyncio.run(scenario())
    assert messages(client, cid)[1]['status'] == 'cancelled'
    assert not execution.running and not client.app.state.conversations.active


def test_save_failure_never_emits_done(rig, monkeypatch):
    client = rig[0]; cid = conversation(rig)
    def fail(*args): raise DomainError(500, 'HISTORY_STORAGE_ERROR', 'mock storage failure')
    monkeypatch.setattr(client.app.state.execution, 'complete', fail)
    result = events(client.post('/api/chat/stream', json=body(rig, cid)))
    assert result[-1]['type'] == 'error' and not any(e['type'] == 'done' for e in result)
    assert messages(client, cid)[1]['status'] == 'failed'


def test_summary_input_budget_retains_history(rig):
    client, settings, *_ = rig; cid = conversation(rig)
    for _ in range(2): assert client.post('/api/chat', json=body(rig, cid)).status_code == 200
    settings.memory_window_turns = 1; settings.memory_summary_input_chars = 1
    result = client.post('/api/chat', json=body(rig, cid))
    assert result.status_code == 413 and result.json()['code'] == 'MEMORY_TURN_TOO_LARGE'
    assert len(messages(client, cid)) == 6
    assert client.get(f'/api/conversations/{cid}').json()['summary'] == ''


def test_summary_revision_conflict_preserves_previous_summary(rig):
    client, settings, *_ = rig; cid = conversation(rig); settings.memory_window_turns = 1
    for _ in range(2): assert client.post('/api/chat', json=body(rig, cid)).status_code == 200
    from app.schemas.conversation import ConversationUpdate
    class Provider:
        async def complete(self, prompt):
            client.app.state.conversations.rename(cid, ConversationUpdate(title='changed'))
            return '摘要'
    with pytest.raises(DomainError) as caught:
        asyncio.run(client.app.state.conversation_memory.context(cid, 3, Provider()))
    assert caught.value.code == 'MEMORY_SUMMARY_CONFLICT'
    assert client.get(f'/api/conversations/{cid}').json()['summary'] == ''


def test_total_timeout_marks_failure_and_unlocks(rig):
    client, settings, *_ = rig; cid = conversation(rig); settings.conversation_timeout_seconds = .01
    async def blocked(*args): await asyncio.sleep(1)
    client.app.state.conversation_memory.context = blocked
    result = client.post('/api/chat', json=body(rig, cid))
    assert result.status_code == 504 and result.json()['code'] == 'CONVERSATION_TIMEOUT'
    assert messages(client, cid)[1]['status'] == 'failed'
    assert not client.app.state.conversations.active


def test_replay_does_not_unlock_other_active_request(rig):
    client = rig[0]; cid = conversation(rig); execution = client.app.state.execution
    previous = body(rig, cid)
    assert client.post('/api/chat', json=previous).status_code == 200
    attempt, _ = execution.reserve(ChatRequest(**body(rig, cid)), 'chat')
    try:
        assert client.post('/api/chat', json=previous).status_code == 200
        assert client.app.state.conversations.active[cid] == attempt.request_id
    finally:
        execution.fail(attempt, 'REQUEST_CANCELLED', 'cancelled'); execution.release(attempt)


@pytest.mark.parametrize('stream', [False, True])
def test_empty_knowledge_base_persists_without_llm(rig, stream):
    client = rig[0]; cid = conversation(rig)
    client.delete(f"/api/documents/{rig[3]['id']}")
    result = client.post('/api/chat' + ('/stream' if stream else ''), json=body(rig, cid))
    answer = events(result)[-1] if stream else result.json()
    assert answer['status'] == 'empty_knowledge_base' and not rig[4]
    assert messages(client, cid)[1]['status'] == 'completed'


def test_failed_regeneration_keeps_prior_success_in_context(rig):
    client = rig[0]; cid = conversation(rig)
    assert client.post('/api/chat', json=body(rig, cid)).status_code == 200
    provider = client.app.state.chat.provider
    client.app.state.chat.provider = CompatibleLLMProvider(rig[1], httpx.MockTransport(lambda r: httpx.Response(500)))
    assert client.post('/api/chat', json=body(rig, cid, regenerate_turn=1)).status_code == 502
    client.app.state.chat.provider = provider
    assert client.post('/api/chat', json=body(rig, cid)).status_code == 200
    history = json.loads(rig[4][-1]['messages'][-1]['content'])['conversation_context']['recent_turns']
    assert len(history) == 1 and 'ALPHA' in history[0]['answer']


def test_local_multiturn_reuses_verified_ssl_context(rig):
    import ssl
    provider = CompatibleLLMProvider(rig[1])
    context = provider._verification(False)
    assert provider._verification(False) is context
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert provider._verification(True) is True
