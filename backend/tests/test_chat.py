import json

import httpx
import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.llm.provider import CompatibleLLMProvider
from app.main import create_app
from app.rag.embeddings import CompatibleEmbeddingProvider


def embedding_response(request):
    def vector(text):
        if "MIX" in text:
            return [0.8, 0.6, 0]
        if "ALPHA" in text or "SECRET" in text:
            return [1, 0, 0]
        if "BETA" in text:
            return [0, 1, 0]
        return [0, 0, 1]
    body = json.loads(request.content)
    return httpx.Response(200, json={"data": [{"index": i, "embedding": vector(text)} for i, text in enumerate(body["input"])]})


@pytest.fixture
def rig(tmp_path):
    settings = Settings(database_url=f"sqlite:///{(tmp_path / 'chat.db').as_posix()}", data_dir=tmp_path / 'data',
                        embedding_base_url='http://unit.test/v1', embedding_model='fixture-vectors',
                        llm_base_url='http://unit.test/v1', llm_model='fixture-llm',
                        chunk_size=1000, chunk_overlap=50, retrieval_min_score=0.5, _env_file=None)
    captured = []

    def llm_reply(request):
        body = json.loads(request.content)
        captured.append(body)
        answer = "资料中的结论来自 ALPHA。[1]\n\n```python\nprint('ALPHA')\n```"
        if body['stream']:
            data = ''.join('data: ' + json.dumps({'choices': [{'delta': {'content': token}, 'finish_reason': None}]}, ensure_ascii=False) + '\n\n' for token in [answer[:5], answer[5:18], answer[18:]])
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=data + 'data: [DONE]\n\n')
        return httpx.Response(200, json={'choices': [{'message': {'content': answer}, 'finish_reason': 'stop'}]})

    with TestClient(create_app(settings)) as client:
        client.app.state.documents.embeddings = CompatibleEmbeddingProvider(settings, httpx.MockTransport(embedding_response))
        client.app.state.chat.provider = CompatibleLLMProvider(settings, httpx.MockTransport(llm_reply))
        kb = client.post('/api/knowledge-bases', json={'name': '当前知识库'}).json()['id']
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((50, 50), 'ALPHA lesson: first page verified source.')
            pdf.new_page().insert_text((50, 50), 'BETA lesson: second page verified source.')
            content = pdf.tobytes()
        result = client.post(f'/api/knowledge-bases/{kb}/documents', files={'file': ('中文课程.pdf', content)})
        assert result.status_code == 201, result.text
        doc = result.json()
        yield client, settings, kb, doc, captured


def events(response):
    return [json.loads(frame[6:]) for frame in response.text.split('\n\n') if frame.startswith('data: ')]


def test_chat_citations_and_prompt(rig):
    client, settings, kb, doc, captured = rig
    result = client.post('/api/chat', json={'knowledge_base_id': kb, 'question': '解释 ALPHA'})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body['status'] == 'answered' and '[1]' in body['answer']
    citation = body['citations'][0]
    source = client.get(f"/api/documents/{doc['id']}").json()['chunks'][0]
    assert citation['knowledge_base_id'] == kb
    assert citation['document_id'] == doc['id']
    assert citation['document_name'] == '中文课程.pdf' and citation['page'] == 1
    assert citation['chunk_id'] == source['id'] and citation['content'] == source['content']
    assert citation['score'] == pytest.approx(1)
    payload = captured[0]
    assert payload['model'] == settings.llm_model
    assert len(payload['messages']) == 2  # No history/memory.
    assert '不可信数据' in payload['messages'][0]['content']
    context = json.loads(payload['messages'][1]['content'])
    assert context['question'] == '解释 ALPHA'
    assert context['reference_material'][0]['page'] == 1
    assert context['reference_material'][0]['content'] == source['content']


def test_current_kb_isolation(rig):
    client, settings, kb, doc, captured = rig
    other = client.post('/api/knowledge-bases', json={'name': '其他库'}).json()['id']
    client.post(f'/api/knowledge-bases/{other}/documents', files={'file': ('其他库秘密.txt', b'SECRET other knowledge base')})
    result = client.post('/api/chat', json={'knowledge_base_id': kb, 'question': 'ALPHA'})
    assert result.status_code == 200
    assert all(c['knowledge_base_id'] == kb for c in result.json()['citations'])
    assert 'SECRET' not in json.dumps(captured, ensure_ascii=False)


def test_top_k_global_order_and_page_two(rig):
    client, settings, kb, doc, captured = rig
    client.post(f'/api/knowledge-bases/{kb}/documents', files={'file': ('mix.txt', b'MIX material')})
    body = client.post('/api/chat', json={'knowledge_base_id': kb, 'question': 'ALPHA', 'top_k': 2}).json()
    assert len(body['citations']) == 2
    assert body['citations'][0]['score'] > body['citations'][1]['score']
    assert body['citations'][1]['document_name'] == 'mix.txt' and body['citations'][1]['page'] is None
    assert [c['id'] for c in body['citations']] == [1, 2]
    page_two = client.post('/api/chat', json={'knowledge_base_id': kb, 'question': 'BETA', 'top_k': 1}).json()
    assert page_two['citations'][0]['page'] == 2
    assert 'BETA' in page_two['citations'][0]['content']


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('empty', [False, True])
def test_empty_and_no_relevant_do_not_call_llm(rig, streaming, empty):
    client, settings, kb, doc, captured = rig
    if empty:
        client.delete(f"/api/documents/{doc['id']}")
    settings.llm_base_url = ''
    response = client.post('/api/chat' + ('/stream' if streaming else ''), json={'knowledge_base_id': kb, 'question': 'GAMMA'})
    assert response.status_code == 200
    body = events(response)[-1] if streaming else response.json()
    assert body['status'] == ('empty_knowledge_base' if empty else 'no_relevant_content')
    assert body['citations'] == []
    assert captured == []


@pytest.mark.parametrize('payload', [
    {'question': ''}, {'question': '   '}, {'question': 'x' * 4001}, {'question': None},
    {'question': 'ALPHA', 'top_k': 0}, {'question': 'ALPHA', 'top_k': 21},
    {'question': 'ALPHA', 'history': []},
])
def test_invalid_question(rig, payload):
    client, _, kb, _, captured = rig
    response = client.post('/api/chat', json={'knowledge_base_id': kb, **payload})
    assert response.status_code == 422
    assert captured == []


def test_unknown_kb_and_stream_validation(rig):
    client, _, kb, _, _ = rig
    assert client.post('/api/chat', json={'knowledge_base_id': 9999, 'question': 'ALPHA'}).status_code == 404
    result = client.post('/api/chat/stream', json={'knowledge_base_id': kb, 'question': ' '})
    assert result.status_code == 422 and result.json()['code'] == 'VALIDATION_ERROR'


def test_sse_tokens_citations_and_done(rig):
    client, _, kb, _, _ = rig
    response = client.post('/api/chat/stream', json={'knowledge_base_id': kb, 'question': 'ALPHA'})
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/event-stream')
    sequence = events(response)
    assert sequence[0]['type'] == 'citation'
    assert sequence[-1]['type'] == 'done'
    assert len([e for e in sequence if e['type'] == 'token']) == 3
    assert ''.join(e['content'] for e in sequence if e['type'] == 'token') == sequence[-1]['answer']
    assert sequence[-1]['citations'][0] == sequence[0]['data']


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('status', [401, 429, 500])
def test_llm_http_failure(rig, streaming, status):
    client, settings, kb, _, _ = rig
    client.app.state.chat.provider = CompatibleLLMProvider(settings, httpx.MockTransport(lambda _: httpx.Response(status, text='PRIVATE-UPSTREAM-KEY')))
    response = client.post('/api/chat' + ('/stream' if streaming else ''), json={'knowledge_base_id': kb, 'question': 'ALPHA'})
    body = events(response)[-1] if streaming else response.json()
    assert body['code'] == ('LLM_RATE_LIMITED' if status == 429 else 'LLM_UPSTREAM_ERROR')
    assert 'PRIVATE' not in response.text
    if streaming:
        assert body['type'] == 'error' and not any(e['type'] == 'done' for e in events(response))
    else:
        assert response.status_code == (429 if status == 429 else 502)


@pytest.mark.parametrize('streaming', [False, True])
def test_llm_timeout(rig, streaming):
    client, settings, kb, _, _ = rig
    def timeout(request):
        raise httpx.ReadTimeout('private upstream URL')
    client.app.state.chat.provider = CompatibleLLMProvider(settings, httpx.MockTransport(timeout))
    response = client.post('/api/chat' + ('/stream' if streaming else ''), json={'knowledge_base_id': kb, 'question': 'ALPHA'})
    body = events(response)[-1] if streaming else response.json()
    assert body['code'] == 'LLM_TIMEOUT'
    assert 'private' not in response.text


@pytest.mark.parametrize('answer', ['答案 [999]', '没有引用的普通回答'])
def test_fabricated_or_missing_citation_rejected(rig, answer):
    client, settings, kb, _, _ = rig
    client.app.state.chat.provider = CompatibleLLMProvider(settings, httpx.MockTransport(lambda _: httpx.Response(200, json={'choices': [{'message': {'content': answer}}]})))
    response = client.post('/api/chat', json={'knowledge_base_id': kb, 'question': 'ALPHA'})
    assert response.status_code == 502 and response.json()['code'] == 'LLM_INVALID_CITATION'


def test_insufficient_evidence_abstention(rig):
    from app.rag.prompts import ABSTAIN
    client, settings, kb, _, _ = rig
    client.app.state.chat.provider = CompatibleLLMProvider(settings, httpx.MockTransport(lambda _: httpx.Response(200, json={'choices': [{'message': {'content': ABSTAIN}}]})))
    result = client.post('/api/chat', json={'knowledge_base_id': kb, 'question': 'ALPHA'}).json()
    assert result['status'] == 'insufficient_evidence' and result['citations'] == []


@pytest.mark.parametrize('response_body', [{'choices': []}, {'error': 'private'}, {'choices': [{'message': {'content': ''}}]}, {'choices': [{'message': {'content': 'partial [1]'}, 'finish_reason': 'length'}]}])
def test_invalid_llm_response(rig, response_body):
    client, settings, kb, _, _ = rig
    client.app.state.chat.provider = CompatibleLLMProvider(settings, httpx.MockTransport(lambda _: httpx.Response(200, json=response_body)))
    result = client.post('/api/chat', json={'knowledge_base_id': kb, 'question': 'ALPHA'})
    assert result.status_code == 502
    assert 'private' not in result.text


@pytest.mark.parametrize('data', [
    'data: broken-json\n\n',
    'data: {"choices":[{"delta":{"content":"partial [1]"}}]}\n\n',
    'data: {"choices":[{"delta":{"content":"fake [999]"}}]}\n\ndata: [DONE]\n\n',
])
def test_stream_invalid_truncated_or_fake_citation(rig, data):
    client, settings, kb, _, _ = rig
    client.app.state.chat.provider = CompatibleLLMProvider(settings, httpx.MockTransport(lambda _: httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=data)))
    sequence = events(client.post('/api/chat/stream', json={'knowledge_base_id': kb, 'question': 'ALPHA'}))
    assert sequence[-1]['type'] == 'error'
    assert not any(e['type'] == 'done' for e in sequence)


def test_model_mismatch_and_missing_index(rig):
    client, settings, kb, doc, captured = rig
    settings.embedding_model = 'other-model'
    result = client.post('/api/chat', json={'knowledge_base_id': kb, 'question': 'ALPHA'})
    assert result.status_code == 409 and result.json()['code'] == 'EMBEDDING_MODEL_MISMATCH'
    settings.embedding_model = 'fixture-vectors'
    client.app.state.documents.vectors.delete(doc['id'])
    result = client.post('/api/chat', json={'knowledge_base_id': kb, 'question': 'ALPHA'})
    assert result.status_code == 409 and result.json()['code'] == 'VECTOR_INDEX_UNAVAILABLE'
    assert captured == []


def test_context_budget_and_unconfigured_llm(rig):
    client, settings, kb, _, captured = rig
    settings.rag_max_context_chars = 1
    result = client.post('/api/chat', json={'knowledge_base_id': kb, 'question': 'ALPHA'})
    assert result.status_code == 413 and captured == []
    settings.rag_max_context_chars = 16000
    settings.llm_base_url = ''
    result = client.post('/api/chat', json={'knowledge_base_id': kb, 'question': 'ALPHA'})
    assert result.status_code == 503 and result.json()['code'] == 'LLM_NOT_CONFIGURED'
