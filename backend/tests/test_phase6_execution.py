"""Phase 6: synchronized API concurrency, real SQL commit faults and SSE lifecycle."""
import asyncio
from contextlib import aclosing
import sqlite3
from uuid import uuid4
import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database.models import Message
from app.schemas.chat import ChatRequest
from test_chat import rig
from test_conversation_execution import conversation, body, messages


class GatedProvider:
    def __init__(self):
        self.started = asyncio.Event()
        self.finish = asyncio.Event()
        self.calls = 0
    async def stream(self, prompt):
        self.calls += 1
        self.started.set()
        yield 'ALPHA '
        await self.finish.wait()
        yield 'verified [1]'


@pytest.mark.parametrize('same_request', [True, False])
def test_overlapping_http_requests_are_serial_and_replay_once(rig, same_request):
    client = rig[0]; cid = conversation(rig); request = body(rig, cid)
    async def scenario():
        provider = GatedProvider(); client.app.state.chat.provider = provider
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app), base_url='http://test') as http:
            first = asyncio.create_task(http.post('/api/chat', json=request))
            await asyncio.wait_for(provider.started.wait(), 3)
            second = await http.post('/api/chat', json=request if same_request else body(rig, cid))
            assert second.status_code == 409 and second.json()['code'] == 'CONVERSATION_BUSY'
            assert len(messages(client, cid)) == 2
            provider.finish.set()
            result = await asyncio.wait_for(first, 3)
            assert result.status_code == 200
            retries = await asyncio.gather(*(http.post('/api/chat', json=request) for _ in range(3)))
            assert all(r.json() == result.json() for r in retries)
            assert provider.calls == 1
    asyncio.run(scenario())
    saved = messages(client, cid)
    assert len(saved) == 2 and saved[1]['status'] == 'completed'
    assert not client.app.state.execution.running and not client.app.state.conversations.active


def test_different_conversations_can_generate_concurrently(rig):
    client = rig[0]; ids = [conversation(rig), conversation(rig)]
    async def scenario():
        provider = GatedProvider(); client.app.state.chat.provider = provider
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app), base_url='http://test') as http:
            tasks = [asyncio.create_task(http.post('/api/chat', json=body(rig, cid))) for cid in ids]
            async def both_started():
                while provider.calls < 2: await asyncio.sleep(.001)
            await asyncio.wait_for(both_started(), 3)
            assert set(client.app.state.conversations.active) == set(ids)
            provider.finish.set()
            results = await asyncio.gather(*tasks)
            assert all(r.status_code == 200 for r in results)
    asyncio.run(scenario())
    assert all(len(messages(client, cid)) == 2 for cid in ids)


@pytest.mark.parametrize('winner', ['cancel', 'complete'])
def test_cancel_complete_race_has_one_terminal_state(rig, winner):
    client = rig[0]; cid = conversation(rig); request = body(rig, cid)
    async def scenario():
        provider = GatedProvider(); client.app.state.chat.provider = provider
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app), base_url='http://test') as http:
            task = asyncio.create_task(http.post('/api/chat', json=request))
            await asyncio.wait_for(provider.started.wait(), 3)
            if winner == 'complete':
                provider.finish.set()
                assert (await task).status_code == 200
            result = await http.post(f"/api/conversations/{cid}/requests/{request['request_id']}/cancel")
            assert result.status_code == 204
            if winner == 'cancel':
                with pytest.raises(asyncio.CancelledError): await task
                provider.finish.set()
            again = await http.post('/api/chat', json=request)
            assert again.status_code == (200 if winner == 'complete' else 409)
            assert provider.calls == 1
    asyncio.run(scenario())
    saved = messages(client, cid)
    assert len(saved) == 2 and saved[-1]['status'] == ('completed' if winner == 'complete' else 'cancelled')
    assert bool(saved[-1]['content']) == (winner == 'complete')
    assert not client.app.state.conversations.active


@pytest.mark.parametrize('stage', ['reserve', 'complete', 'complete_and_fail'])
def test_dbapi_commit_fault_rolls_back_unlocks_and_recovers(rig, monkeypatch, stage):
    client = rig[0]; cid = conversation(rig); request = body(rig, cid)
    engine = client.app.state.engine; original = engine.dialect.do_commit
    failures = []
    def faulty_commit(connection):
        # Inspect the actual uncommitted SQLite changes, then fail at DBAPI COMMIT.
        cursor = connection.cursor()
        rows = cursor.execute('SELECT status,content FROM messages WHERE role=?', ('assistant',)).fetchall()
        cursor.close()
        should_fail = (stage == 'reserve' and not failures and any(s == 'pending' for s, _ in rows))
        should_fail |= not failures and any(s == 'completed' for s, _ in rows)
        should_fail |= stage == 'complete_and_fail' and len(failures) == 1 and any(s == 'failed' for s, _ in rows)
        if should_fail:
            failures.append(rows)
            raise sqlite3.OperationalError('PRIVATE_COMMIT_FAULT')
        return original(connection)
    monkeypatch.setattr(engine.dialect, 'do_commit', faulty_commit)
    response = client.post('/api/chat', json=request)
    assert response.status_code == 500 and 'PRIVATE' not in response.text
    monkeypatch.setattr(engine.dialect, 'do_commit', original)
    assert len(failures) == (2 if stage == 'complete_and_fail' else 1)
    assert not client.app.state.execution.running and not client.app.state.conversations.active
    saved = messages(client, cid)
    if stage == 'reserve':
        assert saved == []
        assert client.get(f'/api/conversations/{cid}').json()['revision'] == 0
    else:
        assert len(saved) == 2 and saved[1]['content'] == '' and saved[1]['citations'] == []
        assert saved[1]['status'] == ('pending' if stage == 'complete_and_fail' else 'failed')
        if stage == 'complete_and_fail':
            # Recovery on retry must be durable even though the retry is rejected.
            retry = client.post('/api/chat', json=request)
            assert retry.status_code == 409 and retry.json()['code'] == 'REQUEST_INTERRUPTED'
            assert messages(client, cid)[1]['status'] == 'interrupted'
    assert client.post('/api/chat', json=body(rig, cid)).status_code == 200
    assert messages(client, cid)[-1]['status'] == 'completed'


def test_partial_sse_close_cancels_without_verified_content(rig):
    client = rig[0]; cid = conversation(rig); request = body(rig, cid)
    async def scenario():
        provider = GatedProvider(); client.app.state.chat.provider = provider
        async with aclosing(client.app.state.execution.events(client.app, ChatRequest(**request), 'chat')) as stream:
            async for event in stream:
                if event['type'] == 'token':
                    assert event['content'] == 'ALPHA '
                    break
        assert provider.calls == 1
    asyncio.run(scenario())
    saved = messages(client, cid)
    assert saved[1]['status'] == 'cancelled' and saved[1]['content'] == '' and saved[1]['citations'] == []
    assert not client.app.state.conversations.active


def test_committed_done_lost_replays_without_generation(rig):
    client = rig[0]; cid = conversation(rig); request = body(rig, cid)
    async def scenario():
        async with aclosing(client.app.state.execution.events(client.app, ChatRequest(**request), 'chat')) as stream:
            async for event in stream:
                if event['type'] == 'done':
                    assert messages(client, cid)[1]['status'] == 'completed'
                    # The transport drops this event before delivering it to its consumer.
                    return {k:v for k,v in event.items() if k != 'type'}
    committed = asyncio.run(scenario()); count = len(rig[4])
    assert client.post('/api/chat', json=request).json() == committed
    assert len(rig[4]) == count and len(messages(client, cid)) == 2
    assert not client.app.state.conversations.active


def test_abrupt_process_exit_recovers_pending_from_disk(rig):
    import subprocess
    import sys
    from fastapi.testclient import TestClient
    from app.main import create_app
    client, settings, *_ = rig; cid = conversation(rig); request = body(rig, cid)
    child = """
import json,os,sys
from fastapi.testclient import TestClient
from app.main import create_app
from app.core.config import Settings
from app.schemas.chat import ChatRequest
settings=Settings(database_url=sys.argv[1],data_dir=sys.argv[2],_env_file=None)
with TestClient(create_app(settings)) as client:
    client.app.state.execution.reserve(ChatRequest(**json.loads(sys.argv[3])), 'chat')
    os._exit(23)  # No generator/session/lifespan cleanup, as with an interrupted worker.
"""
    import json
    result = subprocess.run([sys.executable, '-B', '-c', child, settings.database_url, str(settings.data_dir), json.dumps(request)], capture_output=True, timeout=15)
    assert result.returncode == 23, result.stderr.decode(errors='replace')
    assert messages(client, cid)[1]['status'] == 'pending'
    with TestClient(create_app(settings)) as restarted:
        recovered = messages(restarted, cid)
        assert recovered[1]['status'] == 'interrupted' and recovered[1]['error_code'] == 'REQUEST_INTERRUPTED'
        assert not restarted.app.state.conversations.active
        retry = restarted.post('/api/chat', json=request)
        assert retry.status_code == 409 and retry.json()['code'] == 'REQUEST_INTERRUPTED'
