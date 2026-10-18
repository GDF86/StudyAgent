import asyncio
import json
from uuid import uuid4
import httpx
import pytest
from sqlalchemy.orm import Session
from app.core.errors import DomainError
from app.rag.reranker import Reranker
from app.agent.tools import ToolContext
from test_chat import rig


def install(rig, handler=None):
    client, settings, *_ = rig
    settings.reranker_base_url = 'http://unit.test/v1'
    settings.reranker_model = 'fixture-reranker'
    captured = []
    def reply(request):
        body = json.loads(request.content); captured.append(body)
        if handler:
            return handler(request, body)
        return httpx.Response(200, json={'results': [{'index': i, 'relevance_score': i} for i in range(len(body['documents']))]})
    client.app.state.chat.retriever.reranker = Reranker(settings, httpx.MockTransport(reply))
    return captured


def chat(rig, **extra):
    return rig[0].post('/api/chat', json={'knowledge_base_id': rig[2], 'question': 'ALPHA', **extra})


def sources(rig):
    with Session(rig[0].app.state.engine) as session:
        return rig[0].app.state.chat.retriever.retrieve(session, rig[2], 'ALPHA', 5, rerank=False).citations


def test_default_disabled_never_calls_reranker(rig):
    calls = install(rig)
    assert rig[1].enable_reranker is False
    assert chat(rig).status_code == 200
    assert calls == []


def test_expanded_candidates_reorder_preserve_citations_and_vector_score(rig):
    calls = install(rig); rig[1].enable_reranker = True
    response = rig[0].post(f'/api/knowledge-bases/{rig[2]}/documents', files={'file': ('mix.txt', b'MIX target')})
    assert response.status_code == 201
    result = chat(rig, top_k=1)
    assert result.status_code == 200
    citation = result.json()['citations'][0]
    assert citation['document_name'] == 'mix.txt' and citation['score'] == pytest.approx(.8)
    assert citation['content'] == 'MIX target' and citation['id'] == 1 and citation['knowledge_base_id'] == rig[2]
    assert len(calls[0]['documents']) == 2 and all('BETA' not in d for d in calls[0]['documents'])
    assert 'MIX target' in json.dumps(rig[4][0])


def test_global_candidate_cap_20(rig):
    calls = install(rig); rig[1].enable_reranker = True
    for i in range(23):
        response = rig[0].post(f'/api/knowledge-bases/{rig[2]}/documents', files={'file': (f'{i}.txt', f'ALPHA {i}'.encode())})
        assert response.status_code == 201
    assert len(chat(rig).json()['citations']) == 5
    assert len(calls[0]['documents']) == calls[0]['top_n'] == 20


def test_equal_rerank_scores_keep_vector_order(rig):
    install(rig, lambda req, body: httpx.Response(200, json={'results': [{'index': i, 'relevance_score': 1} for i in reversed(range(len(body['documents'])))]}))
    rig[0].post(f'/api/knowledge-bases/{rig[2]}/documents', files={'file': ('mix.txt', b'MIX target')})
    basic = chat(rig).json()['citations']; rig[1].enable_reranker = True
    assert chat(rig).json()['citations'] == basic


@pytest.mark.parametrize('payload', [{}, {'results': []}, {'results': [{'index': False, 'relevance_score': 1}]},
    {'results': [{'index': 0.0, 'relevance_score': 1}]}, {'results': [{'index': 1, 'relevance_score': 1}]},
    {'results': [{'index': 0, 'relevance_score': True}]}, {'results': [{'index': 0, 'relevance_score': '1'}]},
    {'results': [{'index': 0, 'relevance_score': None}]}])
def test_invalid_responses_fail_closed(rig, payload):
    install(rig, lambda req, body: httpx.Response(200, json=payload)); rig[1].enable_reranker = True
    response = chat(rig)
    assert response.status_code == 502 and response.json()['code'] == 'RERANKER_INVALID_RESPONSE'
    assert rig[4] == []


@pytest.mark.parametrize('raw', ['{"results":[{"index":0,"relevance_score":NaN}]}', '{"results":[{"index":0,"relevance_score":Infinity}]}', 'not json'])
def test_nonfinite_or_non_json(rig, raw):
    install(rig, lambda req, body: httpx.Response(200, content=raw)); rig[1].enable_reranker = True
    assert chat(rig).json()['code'] == 'RERANKER_INVALID_RESPONSE'


def test_duplicate_indices_rejected(rig):
    install(rig, lambda req, body: httpx.Response(200, json={'results': [{'index': 0, 'relevance_score': 1}]*2}))
    candidate = sources(rig)[0]
    with pytest.raises(DomainError) as error:
        rig[0].app.state.chat.retriever.reranker.rank('ALPHA', [candidate, candidate], 1)
    assert error.value.code == 'RERANKER_INVALID_RESPONSE'


@pytest.mark.parametrize('status,code', [(429,'RERANKER_RATE_LIMITED'),(500,'RERANKER_FAILED'),(401,'RERANKER_FAILED'),(307,'RERANKER_FAILED')])
def test_http_failures_and_secret_redaction(rig, caplog, status, code):
    from pydantic import SecretStr
    rig[1].reranker_api_key = SecretStr('SENTINEL-SECRET')
    def handler(request, body):
        assert request.headers['authorization'] == 'Bearer SENTINEL-SECRET'
        return httpx.Response(status, text='SENTINEL-SECRET private document', headers={'location':'https://not-followed.test'})
    install(rig, handler); rig[1].enable_reranker = True
    response = chat(rig)
    assert response.json()['code'] == code
    assert 'SENTINEL-SECRET' not in response.text + caplog.text
    assert 'private document' not in response.text + caplog.text


@pytest.mark.parametrize('exc,code', [(httpx.ReadTimeout, 'RERANKER_TIMEOUT'), (httpx.ConnectError,'RERANKER_INVALID_RESPONSE')])
def test_transport_failures(rig, exc, code):
    def handler(req, body): raise exc('secret upstream detail', request=req)
    install(rig, handler); rig[1].enable_reranker = True
    response = chat(rig)
    assert response.json()['code'] == code and 'secret' not in response.text


def test_input_budget_and_missing_configuration(rig):
    rig[1].enable_reranker = True
    assert chat(rig).json()['code'] == 'RERANKER_NOT_CONFIGURED'
    calls = install(rig); rig[1].reranker_max_input_chars = 1
    assert chat(rig).json()['code'] == 'RERANKER_INPUT_TOO_LARGE'
    assert calls == []


def test_empty_knowledge_base_never_calls_reranker(rig):
    calls = install(rig); rig[1].enable_reranker = True
    kb = rig[0].post('/api/knowledge-bases', json={'name':'empty-rerank'}).json()['id']
    result = rig[0].post('/api/chat', json={'knowledge_base_id':kb,'question':'ALPHA'})
    assert result.json()['status'] == 'empty_knowledge_base' and calls == []


def test_comparison_forces_modes_without_mutating_global_flag(rig):
    calls = install(rig)
    body = {'knowledge_base_id':rig[2], 'question':'ALPHA', 'include_answer':True}
    result = rig[0].post('/api/rag/compare', json=body)
    assert result.status_code == 200
    runs = result.json()['runs']
    assert [r['mode'] for r in runs] == ['basic','reranked']
    assert all(r['elapsed_ms'] >= 0 and r['answer']['status'] == 'answered' for r in runs)
    assert rig[1].enable_reranker is False and len(calls) == 1
    assert result.json()['score_kind'] == 'vector_cosine_similarity'


def test_comparison_keeps_basic_success_when_reranker_unconfigured(rig):
    result = rig[0].post('/api/rag/compare', json={'knowledge_base_id':rig[2], 'question':'ALPHA'}).json()
    assert result['runs'][0]['citations'] and result['runs'][0]['error_code'] is None
    assert result['runs'][1]['error_code'] == 'RERANKER_NOT_CONFIGURED'
    assert not rig[4]


@pytest.mark.parametrize('extra', [{'top_k':0},{'top_k':21},{'question':' '},{'conversation_id':str(uuid4())}])
def test_comparison_validation(rig, extra):
    assert rig[0].post('/api/rag/compare', json={'knowledge_base_id':rig[2], 'question':'ALPHA', **extra}).status_code == 422


def test_enabled_reranker_agent_search_and_persistent_chat(rig):
    install(rig); rig[1].enable_reranker = True
    rig[0].post(f'/api/knowledge-bases/{rig[2]}/documents', files={'file':('mix.txt', b'MIX target')})
    state = rig[0].app.state
    context = ToolContext(rig[1], state.engine, state.documents, state.chat.retriever, state.chat.provider, rig[2])
    result = asyncio.run(context.execute('search_documents', json.dumps({'query':'ALPHA','top_k':1})))
    assert 'mix.txt' in json.dumps(result)
    cid = rig[0].post('/api/conversations', json={'knowledge_base_id':rig[2], 'title':'rerank', 'mode':'chat'}).json()['id']
    response = chat(rig, top_k=1, conversation_id=cid, request_id=str(uuid4()))
    assert response.status_code == 200, response.text
    saved = rig[0].get(f'/api/conversations/{cid}/messages').json()['items'][1]
    assert saved['citations'] == response.json()['citations'] and saved['citations'][0]['document_name'] == 'mix.txt'
