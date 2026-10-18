"""Default-window and batch-summary invariants; real storage, deterministic model."""
import asyncio
import json
from uuid import uuid4
import pytest
from app.core.errors import DomainError
from app.schemas.conversation import MessageWrite
from test_chat import rig
from test_conversation_execution import conversation, body
from test_agent import install, turn, call


def seed(rig, cid, start, end, size=20):
    rows = []
    for number in range(start, end + 1):
        rows.extend([MessageWrite(role='user', content=f'Q{number} ALPHA', turn_no=number),
                     MessageWrite(role='assistant', content=f'A{number} ' + 'x' * size, turn_no=number)])
    rig[0].app.state.conversations.save_messages(cid, rig[2], rows)


class SummaryProvider:
    def __init__(self, fail_at=None): self.inputs = []; self.fail_at = fail_at
    async def complete(self, messages):
        source = json.loads(messages[-1]['content']); self.inputs.append(source)
        if len(self.inputs) == self.fail_at: raise DomainError(502, 'LLM_TIMEOUT', 'mock')
        return 'summary-through-' + str(source['turns'][-1]['turn'])


@pytest.mark.parametrize('past_turns', [9, 10, 11])
def test_default_ten_turn_boundary(rig, past_turns):
    client = rig[0]; cid = conversation(rig); seed(rig, cid, 1, past_turns)
    assert rig[1].memory_window_turns == 10
    provider = SummaryProvider()
    context = asyncio.run(client.app.state.conversation_memory.context(cid, past_turns + 1, provider))
    assert [t['turn'] for t in context['recent_turns']] == list(range(max(1, past_turns - 9), past_turns + 1))
    assert len(provider.inputs) == (1 if past_turns == 11 else 0)
    detail = client.get(f'/api/conversations/{cid}').json()
    assert detail['summary_through_turn'] == max(0, past_turns - 10)
    assert client.get(f'/api/conversations/{cid}/messages').json()['total'] == past_turns * 2


def test_incremental_summary_consumes_only_new_older_turns(rig):
    client = rig[0]; cid = conversation(rig); seed(rig, cid, 1, 11)
    provider = SummaryProvider(); memory = client.app.state.conversation_memory
    first = asyncio.run(memory.context(cid, 12, provider))
    seed(rig, cid, 12, 12)
    second = asyncio.run(memory.context(cid, 13, provider))
    assert [[t['turn'] for t in x['turns']] for x in provider.inputs] == [[1], [2]]
    assert provider.inputs[1]['previous_summary'] == first['summary']
    assert second['summary'] == 'summary-through-2'
    assert [t['turn'] for t in second['recent_turns']] == list(range(3, 13))


@pytest.mark.parametrize('fail_at', [None, 2])
def test_multi_batch_summary_is_atomic_and_retryable(rig, fail_at):
    client, settings, *_ = rig; cid = conversation(rig)
    settings.memory_window_turns = 1; settings.memory_summary_input_chars = 1000; settings.memory_summary_max_chars = 100
    seed(rig, cid, 1, 4, size=600)
    original = client.get(f'/api/conversations/{cid}').json()
    original_messages = client.get(f'/api/conversations/{cid}/messages').json()
    provider = SummaryProvider(fail_at)
    memory = client.app.state.conversation_memory
    if fail_at:
        with pytest.raises(DomainError) as error: asyncio.run(memory.context(cid, 5, provider))
        assert error.value.code == 'MEMORY_SUMMARY_FAILED'
        assert client.get(f'/api/conversations/{cid}').json() == original
        assert client.get(f'/api/conversations/{cid}/messages').json() == original_messages
        provider = SummaryProvider()
    context = asyncio.run(memory.context(cid, 5, provider))
    assert [[t['turn'] for t in x['turns']] for x in provider.inputs] == [[1], [2], [3]]
    assert [x['previous_summary'] for x in provider.inputs] == ['', 'summary-through-1', 'summary-through-2']
    assert all(len(json.dumps(x, ensure_ascii=False)) <= 1000 for x in provider.inputs)
    assert context['summary'] == 'summary-through-3' and [t['turn'] for t in context['recent_turns']] == [4]
    detail = client.get(f'/api/conversations/{cid}').json()
    assert detail['summary_through_turn'] == 3 and detail['revision'] == original['revision'] + 1
    assert client.get(f'/api/conversations/{cid}/messages').json() == original_messages


def test_character_budget_summarizes_whole_turns_before_window_limit(rig):
    client, settings, *_ = rig; cid = conversation(rig)
    settings.memory_max_context_chars = 1600; settings.memory_summary_max_chars = 100
    seed(rig, cid, 1, 3, size=650)
    provider = SummaryProvider()
    context = asyncio.run(client.app.state.conversation_memory.context(cid, 4, provider))
    recent = [x['turn'] for x in context['recent_turns']]
    older = [t['turn'] for batch in provider.inputs for t in batch['turns']]
    assert older and recent and older + recent == [1, 2, 3]
    assert len(json.dumps(context, ensure_ascii=False)) <= 1600
    assert client.get(f'/api/conversations/{cid}/messages').json()['total'] == 6


def test_agent_next_turn_latest_regeneration_preferences_summary_and_citations(rig):
    client = rig[0]; cid = conversation(rig, 'agent')
    preferences = {'answer_style': 'detailed', 'learning_goal': 'exam', 'preferred_language': 'en'}
    for key, value in preferences.items(): assert client.put(f'/api/memory/{key}', json={'value': value}).status_code == 200
    request = {'knowledge_base_id': rig[2], 'task': 'ALPHA 学习', 'conversation_id': cid}
    def run(answer, **extra):
        captured = install(rig, [turn([call('search_documents', {'query': 'ALPHA', 'top_k': 1})]), turn(answer=answer)],
                           generate=lambda body: 'ALPHA past learning summary')
        result = client.post('/api/agent/run', json={**request, 'request_id': str(uuid4()), **extra})
        assert result.status_code == 200, result.text
        return result.json(), captured
    first, _ = run('OLD unique answer [1]')
    regenerated, _ = run('NEW unique answer [1]', regenerate_turn=1)
    second, captured = run('SECOND answer [1]', task='ALPHA follow up')
    context = json.loads(captured[0]['messages'][1]['content'])['conversation_context']
    assert context['preferences'] == preferences
    assert len(context['recent_turns']) == 1 and 'NEW unique' in context['recent_turns'][0]['answer']
    assert 'OLD unique' not in json.dumps(context) and '[1]' not in context['recent_turns'][0]['answer']
    assert regenerated['attempt_no'] == 2 and regenerated['turn_no'] == 1
    rig[1].memory_window_turns = 1
    _, captured = run('THIRD answer [1]', task='ALPHA next follow up')
    tool_request = next(x for x in captured if 'tools' in x)
    context = json.loads(tool_request['messages'][1]['content'])['conversation_context']
    assert context['summary'] == 'ALPHA past learning summary'
    assert [t['turn'] for t in context['recent_turns']] == [2]
    saved = client.get(f'/api/conversations/{cid}/messages?limit=100').json()['items']
    assert [m['content'] for m in saved if m['role'] == 'assistant'] == [first['answer'], regenerated['answer'], second['answer'], 'THIRD answer [1]']
    assert saved[1]['citations'] == first['citations'] and saved[1]['result_metadata']['tool_calls'] == 1
    assert client.delete(f"/api/documents/{rig[3]['id']}").status_code == 204
    assert client.get(f'/api/conversations/{cid}/messages').json()['items'][1]['citations'] == first['citations']
