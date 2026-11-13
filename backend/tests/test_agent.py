"""Phase 4: Mock only the remote model; real API, DB, tools and FAISS execute."""
import asyncio
import json
import httpx
import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session
from app.agent.calculator import calculate
from app.core.errors import DomainError
from app.database.models import Document, Chunk
from app.llm.provider import CompatibleLLMProvider
from app.schemas.agent import AgentRequest
from test_chat import rig, events


def call(name, args, id="call-1"):
    return {"id": id, "type": "function", "function": {"name": name, "arguments": args if isinstance(args, str) else json.dumps(args)}}


def turn(calls=None, answer="计算结果为 14。", **extra):
    return {"choices": [{"message": {"role": "assistant", "content": answer if calls is None else "PRIVATE_REASONING_NOT_FOR_UI",
                                   **({"tool_calls": calls} if calls is not None else {}), **extra},
                         "finish_reason": "stop" if calls is None else "tool_calls"}]}


def install(rig, steps, generate=None):
    client, settings, kb, doc, _ = rig
    captured = []
    index = 0
    def respond(request):
        nonlocal index
        body = json.loads(request.content)
        captured.append(body)
        assert str(request.url) == 'http://unit.test/v1/chat/completions'
        if 'tools' not in body:
            assert generate is not None
            return httpx.Response(200, json=turn(answer=generate(body)))
        step = steps[min(index, len(steps) - 1)]
        index += 1
        if callable(step):
            step = step(body)
        return step if isinstance(step, httpx.Response) else httpx.Response(200, json=step)
    client.app.state.agent.provider = CompatibleLLMProvider(settings, httpx.MockTransport(respond))
    return captured


def run(rig, stream=False, task='执行测试任务'):
    return rig[0].post('/api/agent/' + ('stream' if stream else 'run'), json={'knowledge_base_id': rig[2], 'task': task})


def results(body):
    return [json.loads(m['content']) for m in body['messages'] if m['role'] == 'tool']


def test_consecutive_and_multi_call_protocol(rig):
    captured = install(rig, [turn([call('calculator', {'expression': '2+3*4'})]),
        turn([call('calculator', {'expression': '14/2'}, 'call-2'), call('calculator', {'expression': '2**3'}, 'call-3')]), turn(answer='结果为 14、7、8。')])
    response = run(rig)
    assert response.status_code == 200, response.text
    assert response.json()['tool_calls'] == 3 and response.json()['citations'] == []
    assert [x['result']['value'] for x in results(captured[-1])] == [14, 7, 8]
    assert [m['tool_call_id'] for m in captured[-1]['messages'] if m['role'] == 'tool'] == ['call-1', 'call-2', 'call-3']
    assert len(captured[0]['tools']) == 5 and captured[0]['tool_choice'] == 'auto'
    assert 'PRIVATE_REASONING' not in json.dumps(captured[-1])


def test_search_real_faiss_and_citation_identity(rig):
    captured = install(rig, [turn([call('search_documents', {'query': 'ALPHA', 'top_k': 1})]), turn(answer='ALPHA 的资料 [1]')])
    response = run(rig)
    assert response.status_code == 200, response.text
    citation = response.json()['citations'][0]
    assert citation['document_id'] == rig[3]['id'] and citation['page'] == 1
    assert citation['content'] == results(captured[-1])[0]['result']['citations'][0]['content']
    assert 'ALPHA lesson' in citation['content']


@pytest.mark.parametrize('tool', ['summarize_document', 'compare_documents'])
def test_generation_reads_real_documents(rig, tool):
    client, settings, kb, doc, _ = rig
    ids = [doc['id']]
    if tool == 'compare_documents':
        ids.append(client.post(f'/api/knowledge-bases/{kb}/documents', files={'file': ('第二份.txt', b'ALPHA second document')}).json()['id'])
    def generate(body):
        material = json.loads(body['messages'][-1]['content'])['reference_material']
        assert any('ALPHA lesson' in c['content'] for c in material)
        assert any('BETA lesson' in c['content'] for c in material)
        if tool == 'compare_documents':
            assert any('second document' in c['content'] for c in material)
        return '总结或比较结果 ' + ' '.join(f"[{c['id']}]" for c in material)
    args = {'document_id': ids[0]} if tool == 'summarize_document' else {'document_ids': ids}
    captured = install(rig, [turn([call(tool, args)]), turn(answer='依据原文的结果 [1]')], generate)
    response = run(rig)
    assert response.status_code == 200, response.text
    assert {c['document_id'] for c in response.json()['citations']} == set(ids)
    assert results(captured[-1])[0]['ok']


@pytest.mark.parametrize('kind', ['multiple_choice', 'short_answer', 'true_false'])
def test_quiz_validated_structure_and_sources(rig, kind):
    def generate(body):
        source = json.loads(body['messages'][-1]['content'])['reference_material'][0]
        assert 'ALPHA' in source['content']
        return json.dumps({'questions': [{'question': 'ALPHA 是什么？', 'options': ['A','B','C','D'] if kind == 'multiple_choice' else [],
            'answer': 'A' if kind == 'multiple_choice' else 'true' if kind == 'true_false' else '课程内容',
            'explanation': '依据原文', 'citation_ids': [source['id']]}]})
    captured = install(rig, [turn([call('generate_quiz', {'topic': 'ALPHA', 'count': 1, 'question_type': kind})]), turn(answer='练习题及答案 [1]')], generate)
    response = run(rig)
    assert response.status_code == 200, response.text
    quiz = results(captured[-1])[0]['result']
    assert quiz['question_type'] == kind and len(quiz['questions']) == 1


@pytest.mark.parametrize('tool,args,code', [
    ('not_a_tool', {}, 'UNKNOWN_TOOL'), ('calculator', '{bad', 'INVALID_TOOL_ARGUMENTS'),
    ('calculator', {'expression': '1/0'}, 'CALCULATOR_INVALID'),
    ('calculator', {'expression': '__import__("os").system("whoami")'}, 'CALCULATOR_INVALID'),
    ('search_documents', {'query': 'ALPHA', 'knowledge_base_id': 999}, 'INVALID_TOOL_ARGUMENTS'),
    ('search_documents', {'query': 'ALPHA', 'top_k': '5'}, 'INVALID_TOOL_ARGUMENTS'),
    ('compare_documents', {'document_ids': ['a','a']}, 'INVALID_TOOL_ARGUMENTS'),
    ('generate_quiz', {'topic': 'ALPHA', 'count': 100}, 'INVALID_TOOL_ARGUMENTS'),
])
def test_tool_errors_return_to_llm_and_can_recover(rig, tool, args, code):
    captured = install(rig, [turn([call(tool, args)]), turn([call('calculator', {'expression': '7*2'}, 'fixed')]), turn()])
    response = run(rig, stream=True)
    data = events(response)
    assert data[-1]['type'] == 'done'
    assert results(captured[-1])[0]['error']['code'] == code
    assert [e['phase'] for e in data if e['type']=='status'] == ['model','tool_started','tool_failed','model','tool_started','tool_succeeded','model']
    assert 'PRIVATE_REASONING' not in response.text and '__import__' not in response.text


@pytest.mark.parametrize('tool', ['summarize_document', 'compare_documents', 'search_documents', 'generate_quiz'])
def test_cross_kb_isolation_for_every_document_tool(rig, tool):
    client, settings, kb, doc, _ = rig
    other = client.post('/api/knowledge-bases', json={'name': '秘密库'}).json()['id']
    hidden = client.post(f'/api/knowledge-bases/{other}/documents', files={'file': ('SECRET_FILE.txt', b'SECRET other knowledge base')}).json()['id']
    args = {'document_id': hidden} if tool == 'summarize_document' else {'document_ids': [doc['id'], hidden]} if tool == 'compare_documents' else {'query': 'SECRET'} if tool == 'search_documents' else {'topic': 'SECRET', 'count': 1}
    def generate(body):
        assert 'SECRET other knowledge base' not in json.dumps(body)
        return json.dumps({'questions':[{'question':'题目','answer':'答案','explanation':'解释','citation_ids':[1]}]})
    captured = install(rig, [turn([call(tool,args)]), turn([call('calculator',{'expression':'1+1'},'safe')]), turn(answer='结果 [1]' if tool in {'search_documents','generate_quiz'} else '文档不可用；计算结果 2。')], generate)
    response = run(rig)
    assert response.status_code == 200, response.text
    first = results(captured[-1])[0]
    if tool in {'summarize_document','compare_documents'}:
        assert first['error']['code'] == 'DOCUMENT_UNAVAILABLE'
    assert 'SECRET other knowledge base' not in json.dumps(captured)
    assert 'SECRET_FILE' not in json.dumps(captured)
    assert all(c['knowledge_base_id'] == kb for c in response.json()['citations'])


@pytest.mark.parametrize('bad', ['not-json', '{"questions":[]}', '{"questions":[{"question":"q","answer":"a","explanation":"e","citation_ids":[999]}]}'])
def test_bad_quiz_cannot_commit_citations(rig,bad):
    captured = install(rig,[turn([call('generate_quiz',{'topic':'ALPHA','count':1})]),turn([call('calculator',{'expression':'1'},'fixed')]),turn(answer='1')],lambda _:bad)
    response=run(rig)
    assert response.status_code == 200
    assert response.json()['citations'] == []
    assert results(captured[-1])[0]['error']['code'] == 'QUIZ_INVALID_RESPONSE'


@pytest.mark.parametrize('answer', ['没有引用', '伪造 [999]'])
def test_invalid_final_citations_rejected(rig,answer):
    install(rig,[turn([call('search_documents',{'query':'ALPHA'})]),turn(answer=answer)])
    assert run(rig).json()['code']=='LLM_INVALID_CITATION'


def test_no_source_citation_rejected(rig):
    install(rig,[turn([call('calculator',{'expression':'1'})]),turn(answer='1 [1]')])
    assert run(rig).json()['code']=='LLM_INVALID_CITATION'


@pytest.mark.parametrize('limit', ['calls','rounds','duplicate'])
def test_limits_and_duplicate_ids(rig,limit):
    if limit=='calls': rig[1].agent_max_tool_calls=1
    if limit=='rounds': rig[1].agent_max_rounds=1
    install(rig,[turn([call('calculator',{'expression':'1'})]),turn([call('calculator',{'expression':'2'},'call-1' if limit=='duplicate' else 'call-2')])])
    response=run(rig,stream=True)
    data=events(response)
    assert data[-1]['type']=='error'
    assert data[-1]['code']=={'calls':'AGENT_CALL_LIMIT','rounds':'AGENT_ROUND_LIMIT','duplicate':'AGENT_DUPLICATE_CALL'}[limit]
    assert not any(e['type']=='done' for e in data)


def test_context_limit(rig):
    rig[1].agent_max_context_chars=1000
    install(rig,[turn([call('calculator',{'expression':'1'})])])
    assert run(rig,task='长'*2000).json()['code']=='AGENT_CONTEXT_TOO_LARGE'


def test_document_context_limit_does_not_silently_truncate(rig):
    rig[1].rag_max_context_chars=5
    captured=install(rig,[turn([call('summarize_document',{'document_id':rig[3]['id']})]),turn([call('calculator',{'expression':'1'},'fixed')]),turn(answer='1')])
    assert run(rig).status_code==200
    assert results(captured[-1])[0]['error']['code']=='AGENT_CONTEXT_TOO_LARGE'


@pytest.mark.parametrize('body', [{}, {'knowledge_base_id':1,'task':' '}, {'knowledge_base_id':1,'task':'x','history':[]}, {'knowledge_base_id':0,'task':'x'}])
def test_request_validation(rig,body):
    assert rig[0].post('/api/agent/run',json=body).status_code==422


def test_unknown_kb_and_no_tool_result(rig):
    assert rig[0].post('/api/agent/run',json={'knowledge_base_id':99999,'task':'x'}).status_code==404
    install(rig,[turn()])
    assert run(rig).json()['code']=='AGENT_NO_TOOL_RESULT'


@pytest.mark.parametrize('status,code', [(401,'LLM_UPSTREAM_ERROR'),(429,'LLM_RATE_LIMITED'),(500,'LLM_UPSTREAM_ERROR')])
def test_upstream_errors_sanitized(rig,status,code):
    install(rig,[httpx.Response(status,text='PRIVATE_KEY_DO_NOT_LEAK')])
    response=run(rig)
    assert response.json()['code']==code and 'PRIVATE_KEY' not in response.text


@pytest.mark.parametrize('bad', [{}, {'choices':[]}, {'choices':[{'message':{'tool_calls':[]},'finish_reason':'length'}]}, turn([call('calculator',{},'same'),call('calculator',{},'same')])])
def test_malformed_or_truncated_protocol(rig,bad):
    install(rig,[bad])
    expected='LLM_OUTPUT_LIMIT' if bad.get('choices') and bad['choices'][0].get('finish_reason')=='length' else 'LLM_INVALID_TOOL_RESPONSE'
    assert run(rig).json()['code']==expected


def test_provider_timeout(rig):
    def timeout(_): raise httpx.ReadTimeout('PRIVATE_KEY')
    install(rig,[timeout])
    assert run(rig).json()['code']=='LLM_TIMEOUT'


def test_total_timeout_and_cancellation(rig):
    service=rig[0].app.state.agent
    class Slow:
        async def tool_turn(self,*args): await asyncio.sleep(10)
    service.provider=Slow()
    rig[1].agent_timeout_seconds=0.02
    assert run(rig).json()['code']=='AGENT_TIMEOUT'
    async def cancel():
        rig[1].agent_timeout_seconds=10
        context,messages=await service.prepare(AgentRequest(knowledge_base_id=rig[2],task='x'))
        task=asyncio.create_task(service.run(context,messages))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    asyncio.run(cancel())


@pytest.mark.parametrize('expression,value',[('2+3*4',14),('-(2+3)/2',-2.5),('10%3',1),('2**10',1024)])
def test_calculator_values(expression,value): assert calculate(expression)==value


@pytest.mark.parametrize('expression',['1/0','True','x+1','[1][0]','2**1000000','1e309','(-1)**0.5','2**(2**100)','1+'*100+'1','abs(-1)'])
def test_calculator_rejects_unsafe_or_unbounded(expression):
    with pytest.raises(DomainError): calculate(expression)


def test_citation_numbers_stable_across_tools(rig):
    captured=install(rig,[turn([call('search_documents',{'query':'ALPHA','top_k':1})]),turn([call('search_documents',{'query':'BETA','top_k':1},'b'),call('search_documents',{'query':'ALPHA','top_k':1},'a')]),turn(answer='两个来源 [1] [2]')])
    response=run(rig)
    assert response.status_code==200
    assert [c['id'] for c in response.json()['citations']]==[1,2]
    assert [x['result']['citations'][0]['id'] for x in results(captured[-1])]==[1,2,1]


def test_empty_search_can_report_insufficient_evidence(rig):
    captured=install(rig,[turn([call('search_documents',{'query':'NO_MATCH'})]),turn(answer='当前资料不足。')])
    assert run(rig).status_code==200
    assert results(captured[-1])[0]['result']['status']=='no_relevant_content'


def test_missing_configuration(rig):
    rig[1].llm_base_url=''
    assert run(rig).json()['code']=='LLM_NOT_CONFIGURED'


def test_environment_agent_bounds():
    from app.core.config import Settings
    from pydantic import ValidationError
    with pytest.raises(ValidationError): Settings(agent_max_tool_calls=0,_env_file=None)
