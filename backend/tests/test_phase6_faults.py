"""Phase 6 fault injection at filesystem, SQL and model-protocol boundaries."""
import asyncio
import json
import logging
from pathlib import Path
import sqlite3
import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.main import create_app
from app.database.models import Document, Chunk
from app.core.errors import DomainError
from app.rag.embeddings import CompatibleEmbeddingProvider
from app.llm.provider import CompatibleLLMProvider
from test_documents import settings, embedding_reply, upload
from test_chat import rig
from test_agent import install, turn, call, results


@pytest.fixture
def file_client(settings):
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        client.app.state.documents.embeddings = CompatibleEmbeddingProvider(settings, httpx.MockTransport(embedding_reply))
        kb = client.post('/api/knowledge-bases', json={'name': 'faults'}).json()['id']
        yield client, kb


@pytest.mark.parametrize('failure', ['raw_write', 'vector_replace', 'ready_commit'])
def test_ingestion_fault_consistent_failed_record(file_client, monkeypatch, failure):
    client, kb = file_client; service = client.app.state.documents
    if failure == 'raw_write':
        original = Path.write_bytes
        def fail(path, data):
            if path.parent == service.upload_dir:
                original(path, data[:2]); raise OSError('PRIVATE_DISK_FAULT')
            return original(path, data)
        monkeypatch.setattr(Path, 'write_bytes', fail)
    elif failure == 'vector_replace':
        original = Path.replace
        def fail(path, target):
            if path.parent == service.vectors.directory: raise OSError('PRIVATE_DISK_FAULT')
            return original(path, target)
        monkeypatch.setattr(Path, 'replace', fail)
    else:
        engine = client.app.state.engine; original = engine.dialect.do_commit
        def fail(connection):
            cursor = connection.cursor()
            ready = cursor.execute("SELECT count(*) FROM documents WHERE status='ready'").fetchone()[0]; cursor.close()
            if ready: raise sqlite3.OperationalError('PRIVATE_DISK_FAULT')
            return original(connection)
        monkeypatch.setattr(engine.dialect, 'do_commit', fail)
    response = upload(client, kb)
    assert response.status_code == 500 and 'PRIVATE' not in response.text
    records = client.get(f'/api/knowledge-bases/{kb}/documents').json()
    assert len(records) == 1 and records[0]['status'] == 'failed' and records[0]['chunk_count'] == 0
    with Session(client.app.state.engine) as db: assert db.scalars(select(Chunk)).all() == []
    assert list(service.vectors.directory.iterdir()) == []
    monkeypatch.undo()
    assert client.delete(f"/api/documents/{records[0]['id']}").status_code == 204
    assert list(service.upload_dir.iterdir()) == []
    assert upload(client, kb).status_code == 201


@pytest.mark.parametrize('failure', ['vector_unlink', 'raw_unlink', 'delete_commit'])
def test_interrupted_delete_resumes_without_ready_or_orphan_rows(file_client, monkeypatch, failure):
    client, kb = file_client; service = client.app.state.documents
    doc = upload(client, kb).json()
    if failure.endswith('unlink'):
        original = Path.unlink
        target = service.vectors.path(doc['id']) if failure == 'vector_unlink' else service.upload_dir / (doc['id'] + '.txt')
        def fail(path, *args, **kwargs):
            if path == target: raise OSError('PRIVATE_DELETE_FAULT')
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'unlink', fail)
    else:
        engine = client.app.state.engine; original = engine.dialect.do_commit
        def fail(connection):
            cursor = connection.cursor(); count = cursor.execute('SELECT count(*) FROM documents').fetchone()[0]; cursor.close()
            if not count: raise sqlite3.OperationalError('PRIVATE_DELETE_FAULT')
            return original(connection)
        monkeypatch.setattr(engine.dialect, 'do_commit', fail)
    response = client.delete(f"/api/documents/{doc['id']}")
    assert response.status_code == 500 and 'PRIVATE' not in response.text
    detail = client.get(f"/api/documents/{doc['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()['status'] == 'deleting'
    monkeypatch.undo()
    with Session(client.app.state.engine) as db: service.recover(db)
    assert client.get(f"/api/documents/{doc['id']}").status_code == 404
    with Session(client.app.state.engine) as db:
        assert db.scalars(select(Chunk)).all() == [] and db.scalars(select(Document)).all() == []
    assert list(service.upload_dir.iterdir()) == [] and list(service.vectors.directory.iterdir()) == []


@pytest.mark.parametrize('data', [
    [{'index': 0, 'embedding': [float('nan'), 1]}],
    [{'index': 0, 'embedding': [float('inf'), 1]}],
    [{'index': 0, 'embedding': [1e30, 1]}],
    [{'index': 0, 'embedding': [1]}, {'index': 0, 'embedding': [2]}],
    [{'index': False, 'embedding': [1]}],
    [{'index': 0.0, 'embedding': [1]}],
])
def test_malformed_embedding_numbers_and_indices(settings, data):
    provider = CompatibleEmbeddingProvider(settings, httpx.MockTransport(lambda _: httpx.Response(200, content=json.dumps({'data': data}))))
    with pytest.raises(DomainError) as caught: provider.embed_texts(['ALPHA'])
    assert caught.value.code == 'EMBEDDING_INVALID_RESPONSE'


@pytest.mark.parametrize('kind,patch', [
    ('multiple_choice', {'options': ['A', 'B', 'C']}),
    ('multiple_choice', {'options': ['A', 'A', 'C', 'D']}),
    ('multiple_choice', {'options': ['A', 'B', 'C', ' ']}),
    ('multiple_choice', {'answer': 'Z'}),
    ('true_false', {'options': [], 'answer': 'maybe'}),
    ('short_answer', {'options': ['unexpected']}),
    ('short_answer', {'question': ' '}),
    ('short_answer', {'explanation': ''}),
    ('short_answer', {'citation_ids': []}),
    ('short_answer', {'extra': 'private'}),
    ('short_answer', {'wrong_count': True}),
])
def test_invalid_quiz_structure_never_publishes_citations(rig, kind, patch):
    question = {'question': 'ALPHA?', 'options': ['A', 'B', 'C', 'D'] if kind == 'multiple_choice' else [], 'answer': 'A', 'explanation': 'source', 'citation_ids': [1]}
    question.update({k:v for k,v in patch.items() if k != 'wrong_count'})
    generated = json.dumps({'questions': [question] * (2 if patch.get('wrong_count') else 1)})
    captured = install(rig, [turn([call('generate_quiz', {'topic':'ALPHA', 'count':1, 'question_type':kind})]),
                             turn([call('calculator', {'expression':'1'}, 'safe')]), turn(answer='1')], generate=lambda _: generated)
    response = rig[0].post('/api/agent/run', json={'knowledge_base_id':rig[2], 'task':'quiz'})
    assert response.status_code == 200 and response.json()['citations'] == []
    assert results(captured[-1])[0]['error']['code'] == 'QUIZ_INVALID_RESPONSE'


def test_authorization_and_sensitive_logs_are_sanitized(rig, caplog):
    client, settings, kb, _, _ = rig
    secret = 'TEST_ONLY_SECRET_6'; settings.llm_api_key = SecretStr(secret); settings.embedding_api_key = SecretStr(secret)
    observed = []
    def fail(request):
        observed.append(request.headers.get('Authorization'))
        return httpx.Response(500, text=secret)
    caplog.set_level(logging.INFO)
    provider = CompatibleEmbeddingProvider(settings, httpx.MockTransport(fail))
    with pytest.raises(DomainError) as caught: provider.embed_texts(['private document'])
    assert secret not in caught.value.message
    client.app.state.chat.provider = CompatibleLLMProvider(settings, httpx.MockTransport(fail))
    response = client.post('/api/chat', json={'knowledge_base_id':kb, 'question':'ALPHA'})
    assert response.status_code == 502 and secret not in response.text
    assert observed == ['Bearer ' + secret, 'Bearer ' + secret]
    assert secret not in caplog.text and 'private document' not in caplog.text


def test_chunked_upload_without_content_length_is_bounded(settings):
    settings.max_upload_bytes = 8
    app = create_app(settings)
    with TestClient(app) as client:
        kb = client.post('/api/knowledge-bases', json={'name':'chunked'}).json()['id']
        async def scenario():
            async def chunks():
                for _ in range(20): yield b'x' * 4096
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as http:
                request = http.build_request('POST', f'/api/knowledge-bases/{kb}/documents', content=chunks(), headers={'Content-Type':'multipart/form-data; boundary=phase6'})
                assert 'Content-Length' not in request.headers
                response = await http.send(request)
                assert response.status_code == 413 and response.json()['code'] == 'FILE_TOO_LARGE'
        asyncio.run(scenario())
        assert client.get(f'/api/knowledge-bases/{kb}/documents').json() == []
        assert list(app.state.documents.upload_dir.iterdir()) == []
