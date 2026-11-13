import asyncio
import json
import multiprocessing
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr
from app.core.live_test import (Blocked, Ledger, Stage, provision, GuardSync, GuardAsync,
    controlled_payload, require_tool, forbid_extra, LIMITS, STAGES)
from app.llm.provider import CompatibleLLMProvider
from app.rag.embeddings import CompatibleEmbeddingProvider


def authorize(ledger):
    ledger.authorize(input_rate=150, output_rate=1500, embedding_rate=500,
                     console_evidence="OFFLINE_FIXTURE_ONLY", token_bound_attested=True,
                     valid_until=time.time()+600)
    return ledger

@pytest.fixture
def ledger(tmp_path):
    return authorize(provision(tmp_path/'budget.sqlite'))

def settings(ledger, **kwargs):
    values=dict(live_test_mode=True, live_test_ledger=ledger.path,
                llm_base_url='https://offline.invalid/v1', llm_model='qwen-flash',
                llm_api_key=SecretStr('OFFLINE_SENTINEL'), llm_max_tokens=512,
                llm_temperature=0.2, llm_timeout_seconds=3, llm_max_answer_chars=10000,
                embedding_base_url='https://offline.invalid/v1', embedding_model='text-embedding-v4',
                embedding_api_key=SecretStr('OFFLINE_SENTINEL'), embedding_batch_size=10,
                embedding_timeout_seconds=3)
    values.update(kwargs)
    return SimpleNamespace(**values)

def payload(kind,stage):
    if kind=='embedding': return dict(model='text-embedding-v4',input=['学习资料'],encoding_format='float')
    result=dict(model='qwen-flash',messages=[{'role':'user','content':'hello'}],
                max_tokens=128, enable_thinking=False,stream=stage=='stream')
    if stage=='agent': result['tools']=[{'type':'function','function':{'name':'calculator'}}]
    return result

def usage(kind):
    return {'prompt_tokens':100,'completion_tokens':16} if kind=='llm' else {'total_tokens':10}

def response(request):
    data=json.loads(request.content)
    if 'input' in data:
        return httpx.Response(200,json={'data':[{'index':0,'embedding':[1.0,0.0]}],'usage':usage('embedding')})
    if data.get('stream'):
        frames=[{'choices':[{'delta':{'content':'资料结论 [1]'},'finish_reason':None}]},
                {'choices':[{'delta':{},'finish_reason':'stop'}]},
                {'choices':[],'usage':usage('llm')}]
        body=''.join('data: '+json.dumps(x)+'\n\n' for x in frames)+'data: [DONE]\n\n'
        return httpx.Response(200,headers={'content-type':'text/event-stream'},content=body)
    return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':'125'},'finish_reason':'stop'}], 'usage':usage('llm')})

def send(ledger,stage,kind,transport=None):
    cfg=settings(ledger)
    endpoint='/chat/completions' if kind=='llm' else '/embeddings'
    with httpx.Client(transport=GuardSync(cfg,kind,transport or httpx.MockTransport(response)),trust_env=False,follow_redirects=False) as c:
        return c.post(cfg.llm_base_url+endpoint,json=payload(kind,stage))

def complete_until(ledger, target):
    for stage in STAGES:
        if stage==target: return
        with Stage(ledger,stage) as scope:
            for kind,count in LIMITS[stage].items():
                for _ in range(count):send(ledger,stage,kind)
            scope.verify(True)

def test_seven_then_eighth_and_persistent_cost(ledger):
    for stage in STAGES:
        with Stage(ledger,stage) as scope:
            for kind,count in LIMITS[stage].items():
                for _ in range(count): send(ledger,stage,kind)
            scope.verify(True)
    report=Ledger(ledger.path).report()
    assert report['used']==7 and report['state']=='COMPLETE'
    assert all(r['predebit']==1 for r in report['requests'])
    assert report['cost_nano']>0 and report['requests'][-1]['cumulative_cost_nano']==report['cost_nano']
    with pytest.raises(Blocked): ledger.reserve('agent','llm',payload('llm','agent'),offline=True)
    assert ledger.report()['used']==7

@pytest.mark.parametrize('error',[httpx.ReadTimeout,httpx.ConnectError])
def test_failure_or_timeout_consumes_and_halts(ledger,error):
    attempts=[]
    def fail(req): attempts.append(1); raise error('OFFLINE_SENTINEL')
    with pytest.raises(Blocked):
        with Stage(ledger,'ingestion'): send(ledger,'ingestion','embedding',httpx.MockTransport(fail))
    report=Ledger(ledger.path).report()
    assert len(attempts)==1 and report['used']==1 and report['state']=='HALTED'
    assert report['requests'][0]['usage_status']=='UNKNOWN'
    assert report['cost_nano'] is None
    assert 'OFFLINE_SENTINEL' not in json.dumps(report)
    with pytest.raises(Blocked): ledger.reserve('ingestion','embedding',payload('embedding','ingestion'),offline=True)

@pytest.mark.parametrize('status',[401,403,429,500,307])
def test_http_failure_no_retry(ledger,status):
    seen=[]
    def fail(req): seen.append(1); return httpx.Response(status,json={})
    with pytest.raises(Blocked):
        with Stage(ledger,'ingestion'): send(ledger,'ingestion','embedding',httpx.MockTransport(fail))
    assert seen==[1] and ledger.report()['requests'][0]['http_status']==status

def test_missing_usage_not_zero(ledger):
    with pytest.raises(Blocked):
        with Stage(ledger,'ingestion'):
            send(ledger,'ingestion','embedding',httpx.MockTransport(lambda r:httpx.Response(200,json={})))
    row=ledger.report()['requests'][0]
    assert row['input_tokens'] is None and row['cost_nano'] is None and row['usage_status']=='UNKNOWN'

@pytest.mark.parametrize('kind,stage',[('embedding','agent'),('llm','ingestion')])
def test_hidden_or_wrong_stage_call(ledger,kind,stage):
    complete_until(ledger,stage)
    before=ledger.report()['used']
    with pytest.raises(Blocked):
        with Stage(ledger,stage):send(ledger,stage,kind)
    assert ledger.report()['used']==before

def test_agent_third_call_blocked(ledger):
    complete_until(ledger,'agent')
    with pytest.raises(Blocked):
        with Stage(ledger,'agent'):
            send(ledger,'agent','llm');send(ledger,'agent','llm');send(ledger,'agent','llm')
    assert ledger.report()['used']==7

def test_no_borrowing_between_stages(ledger):
    with pytest.raises(Blocked):
        with Stage(ledger,'ingestion'):
            send(ledger,'ingestion','embedding');send(ledger,'ingestion','embedding')
    assert ledger.report()['used']==1

@pytest.mark.parametrize('name',['search_documents','summarize_document','generate_quiz'])
def test_noncalculator_tool_blocked(ledger,name):
    complete_until(ledger,'agent')
    with pytest.raises(Blocked):
        with Stage(ledger,'agent'): require_tool(name)
    assert ledger.report()['used']==5

def test_history_blocked(ledger):
    with pytest.raises(Blocked):
        with Stage(ledger,'ingestion'): forbid_extra('HISTORY_FORBIDDEN')
    assert ledger.report()['used']==0

def test_no_manual_functional_verification_no_next_stage(ledger):
    with pytest.raises(Blocked):
        with Stage(ledger,'ingestion'): send(ledger,'ingestion','embedding')
    assert ledger.report()['state']=='HALTED'

def test_pending_survives_restart_and_blocks(ledger):
    ledger.reserve('ingestion','embedding',payload('embedding','ingestion'),offline=True)
    with pytest.raises(Blocked): Ledger(ledger.path).reserve('ingestion','embedding',payload('embedding','ingestion'),offline=True)
    assert Ledger(ledger.path).report()['used']==1

def test_deleted_database_cannot_be_recreated(ledger):
    ledger.path.unlink()
    with pytest.raises(Blocked):ledger.report()
    with pytest.raises(FileExistsError):provision(ledger.path)
    assert not ledger.path.exists()

@pytest.mark.parametrize('field,value',[('input_rate',None),('token_bound_attested',False),('valid_until',0)])
def test_price_or_bound_missing(tmp_path,field,value):
    ledger=provision(tmp_path/'budget.sqlite')
    values=dict(input_rate=150,output_rate=1500,embedding_rate=500,console_evidence='fixture',token_bound_attested=True,valid_until=time.time()+600)
    values[field]=value
    with pytest.raises(Blocked):ledger.authorize(**values)
    with pytest.raises(Blocked):ledger.reserve('ingestion','embedding',payload('embedding','ingestion'),offline=True)
    assert ledger.report()['used']==0

def test_tier_and_money_limits(ledger):
    huge=payload('embedding','ingestion');huge['input']=['x'*40000]
    with pytest.raises(Blocked):ledger.reserve('ingestion','embedding',huge,offline=True)
    assert ledger.report()['used']==0

def test_pre_request_money_bound(tmp_path):
    ledger=provision(tmp_path/'money.sqlite')
    ledger.authorize(input_rate=999999999,output_rate=1500,embedding_rate=999999999,console_evidence='fixture',token_bound_attested=True,valid_until=time.time()+600)
    with pytest.raises(Blocked):ledger.reserve('ingestion','embedding',payload('embedding','ingestion'),offline=True)
    assert ledger.report()['used']==0

@pytest.mark.parametrize('controlled',[True,False])
def test_real_key_cannot_send_without_scope(ledger,monkeypatch,controlled):
    cfg=settings(ledger,live_test_mode=controlled)
    seen=[]
    monkeypatch.setattr(httpx.HTTPTransport,'handle_request',lambda *args:seen.append(1))
    with httpx.Client(transport=GuardSync(cfg,'embedding',None)) as c:
        with pytest.raises(Blocked):c.post(cfg.embedding_base_url+'/embeddings',json=payload('embedding','ingestion'))
    assert seen==[] and ledger.report()['used']==0

def test_offline_scope_cannot_use_real_transport(ledger):
    with pytest.raises(Blocked):
        with Stage(ledger,'ingestion'):
            with httpx.Client(transport=GuardSync(settings(ledger),'embedding',None)) as c:
                c.post('https://offline.invalid/v1/embeddings',json=payload('embedding','ingestion'))
    assert ledger.report()['used']==0

def test_embeddings_adapter_records_usage(ledger):
    with Stage(ledger,'ingestion') as scope:
        result=CompatibleEmbeddingProvider(settings(ledger),httpx.MockTransport(response)).embed_texts(['hello'])
        scope.verify(result==[[1.0,0.0]])
    assert ledger.report()['requests'][0]['input_tokens']==10

def test_normal_and_streaming_adapter_usage(ledger):
    complete_until(ledger,'rag')
    for stage in ('rag','stream'):
        with Stage(ledger,stage) as scope:
            send(ledger,stage,'embedding')
            provider=CompatibleLLMProvider(settings(ledger),httpx.MockTransport(response))
            if stage=='rag': result=asyncio.run(provider.complete([{'role':'user','content':'hello'}]))
            else:
                async def collect():return ''.join([x async for x in provider.stream([{'role':'user','content':'hello'}])])
                result=asyncio.run(collect())
            scope.verify(bool(result))
    assert all(x['usage_status']=='KNOWN' for x in ledger.report()['requests'])

def worker(path, barrier, queue):
    ledger=Ledger(path)
    barrier.wait()
    try:
        ledger.reserve('ingestion','embedding',payload('embedding','ingestion'),offline=True)
        queue.put('reserved')
    except Blocked:queue.put('blocked')

def test_two_independent_processes_share_atomic_reservation(ledger):
    ctx=multiprocessing.get_context('spawn')
    barrier=ctx.Barrier(2);queue=ctx.Queue()
    processes=[ctx.Process(target=worker,args=(str(ledger.path),barrier,queue)) for _ in range(2)]
    for p in processes:p.start()
    for p in processes:p.join(20);assert p.exitcode==0
    outcomes=[queue.get(timeout=2) for _ in processes]
    assert outcomes.count('reserved')==1
    assert Ledger(ledger.path).report()['used']==1 <= 7

@pytest.mark.parametrize('mutation',[{'model':'other-model'},{'max_tokens':513},{'enable_thinking':True}])
def test_model_and_output_bound_fail_closed(ledger,mutation):
    complete_until(ledger,'rag')
    data=payload('llm','rag');data.update(mutation)
    with pytest.raises(Blocked):ledger.reserve('rag','llm',data,offline=True)
    assert ledger.report()['used']==1

def test_ast_all_outbound_clients_are_guarded():
    import ast
    base=Path(__file__).parents[1]/'app'
    clients=[]
    for filename in base.rglob('*.py'):
        if filename.name=='live_test.py':continue
        tree=ast.parse(filename.read_text(encoding='utf-8'))
        for n in ast.walk(tree):
            if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and isinstance(n.func.value,ast.Name) and n.func.value.id=='httpx' and n.func.attr in ('Client','AsyncClient'):
                kwargs={k.arg:k.value for k in n.keywords}
                assert isinstance(kwargs['transport'],ast.Call)
                assert kwargs['transport'].func.id in ('GuardSync','GuardAsync')
                assert isinstance(kwargs['trust_env'],ast.Constant) and kwargs['trust_env'].value is False
                assert kwargs['follow_redirects'].value is False
                clients.append(filename)
    assert len(clients)==5


def test_agent_real_tool_protocol_roundtrip_offline(ledger):
    from app.agent.agent import AgentService
    complete_until(ledger,'agent')
    requests=[];executed=[]
    def reply(request):
        body=json.loads(request.content); requests.append(body)
        if len(requests)==1:
            message={'role':'assistant','content':None,'tool_calls':[{'id':'calc-1','type':'function',
                'function':{'name':'calculator','arguments':'{"expression":"(125+375)/4"}'}}]}
            reason='tool_calls'
        else:
            assert body['messages'][-1]['role']=='tool'
            assert json.loads(body['messages'][-1]['content'])['result']['value']==125
            message={'role':'assistant','content':'计算结果为 125。'};reason='stop'
        return httpx.Response(200,json={'choices':[{'message':message,'finish_reason':reason}],'usage':usage('llm')})
    cfg=settings(ledger,agent_timeout_seconds=5,agent_max_rounds=6,agent_max_tool_calls=8,agent_max_context_chars=100000)
    provider=CompatibleLLMProvider(cfg,httpx.MockTransport(reply))
    class Context:
        citations=[];kb_id=1
        async def execute(self,name,arguments):
            require_tool(name)
            from app.agent.calculator import calculate
            executed.append(name)
            return {'value':calculate(json.loads(arguments)['expression'])}
    with Stage(ledger,'agent') as scope:
        result=asyncio.run(AgentService(cfg,None,None,None,provider).run(Context(),[{'role':'user','content':'计算 (125+375)/4'}]))
        scope.verify(result['answer']=='计算结果为 125。' and executed==['calculator'])
    assert len(requests)==2 and ledger.report()['used']==7
    assert all([t['function']['name'] for t in r['tools']]==['calculator'] for r in requests)


def test_stream_without_usage_stops(ledger):
    complete_until(ledger,'stream')
    with pytest.raises(Blocked):
        with Stage(ledger,'stream'):
            send(ledger,'stream','embedding')
            def missing(req):return httpx.Response(200,headers={'content-type':'text/event-stream'},content='data: {"choices":[{"delta":{"content":"hello"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
            async def collect():
                provider=CompatibleLLMProvider(settings(ledger),httpx.MockTransport(missing))
                return [x async for x in provider.stream([{'role':'user','content':'hello'}])]
            asyncio.run(collect())
    assert ledger.report()['requests'][-1]['usage_status']=='UNKNOWN'
    assert ledger.report()['state']=='HALTED'


def test_interrupted_stream_consumes_reservation(ledger):
    complete_until(ledger,'stream')
    class Partial(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":null}]}\n\n'
            raise httpx.ReadTimeout('fixture')
    def partial(req):return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Partial())
    with pytest.raises(Blocked):
        with Stage(ledger,'stream'):
            send(ledger,'stream','embedding')
            async def collect():
                provider=CompatibleLLMProvider(settings(ledger),httpx.MockTransport(partial))
                return [x async for x in provider.stream([{'role':'user','content':'hello'}])]
            asyncio.run(collect())
    report=ledger.report()
    assert report['used']==5 and report['requests'][-1]['usage_status']=='UNKNOWN'
    assert report['state']=='HALTED'


def test_usage_exceeding_reserved_bound_halts(ledger):
    seq=ledger.reserve('ingestion','embedding',payload('embedding','ingestion'),offline=True)
    with pytest.raises(Blocked):ledger.settle(seq,200,{'total_tokens':200000})
    assert ledger.report()['state']=='HALTED'
    assert ledger.report()['requests'][0]['error_type']=='TOKEN_BOUND_EXCEEDED'


def test_missing_prices_in_ledger_halts(ledger):
    with ledger.transaction() as (c,r): c.execute('UPDATE run SET input_rate=NULL')
    with pytest.raises(Blocked):ledger.reserve('ingestion','embedding',payload('embedding','ingestion'),offline=True)
    assert ledger.report()['used']==0


def test_expired_prices_halt(ledger):
    with ledger.transaction() as (c,r):c.execute('UPDATE run SET verified_until=0')
    with pytest.raises(Blocked):ledger.reserve('ingestion','embedding',payload('embedding','ingestion'),offline=True)
    assert ledger.report()['used']==0


def test_reranker_forbidden_even_via_comparison(ledger):
    from app.rag.reranker import Reranker
    cfg=settings(ledger,reranker_base_url='https://offline.invalid/v1',reranker_model='forbidden',
                 reranker_api_key=SecretStr('OFFLINE_SENTINEL'),reranker_timeout_seconds=3,reranker_max_input_chars=10000)
    from app.schemas.chat import Citation
    source=Citation(id=1,knowledge_base_id=1,document_id='d',document_name='fixture',page=None,chunk_id='c',content='fixture',score=1)
    with pytest.raises(Blocked):
        with Stage(ledger,'ingestion'):Reranker(cfg,httpx.MockTransport(response)).rank('test',[source],1)
    assert ledger.report()['used']==0


def test_live_run_cannot_reuse_offline_ledger(ledger):
    with pytest.raises(Blocked):ledger.reserve('ingestion','embedding',payload('embedding','ingestion'),offline=False)
    assert ledger.report()['used']==0


def finish_round(ledger):
    for stage in STAGES:
        with Stage(ledger,stage) as scope:
            for kind,count in LIMITS[stage].items():
                for _ in range(count):send(ledger,stage,kind)
            scope.verify(True)

def test_round5_project50_and_next_round_persist(ledger):
    finish_round(ledger)
    first=ledger.report()
    assert first['manual_budget_yuan']==5 and first['project_budget_yuan']==50
    second=authorize(ledger.begin_round())
    assert second.report()['used']==0
    with Stage(second,'ingestion') as scope:
        send(second,'ingestion','embedding');scope.verify(True)
    report=Ledger(second.path,second.run_id).report()
    assert report['used']==1 and report['requests'][0]['seq']==8
    assert report['project_cost_nano']>first['cost_nano']
    assert report['project_reserved_nano']>first['reserved_nano']
    assert Ledger(ledger.path,ledger.run_id).report()['used']==7

def test_round_budget_cannot_exceed_five(ledger):
    with ledger.transaction() as (c,r):
        c.execute('UPDATE run SET embedding_rate=1000000 WHERE id=?',(ledger.run_id,))
    with pytest.raises(Blocked,match='ROUND_MONEY_LIMIT'):
        ledger.reserve('ingestion','embedding',payload('embedding','ingestion'),offline=True)
    assert ledger.report()['used']==0

def test_previous_round_reservations_enforce_project_cap(ledger):
    finish_round(ledger)
    with ledger.transaction() as (c,r):
        # Fixture simulates already-reserved historical project spend, never cloud spend.
        c.execute('UPDATE requests SET reserved_nano=49999999999 WHERE seq=1')
    second=authorize(ledger.begin_round())
    with pytest.raises(Blocked,match='PROJECT_MONEY_LIMIT'):
        second.reserve('ingestion','embedding',payload('embedding','ingestion'),offline=True)
    assert second.report()['used']==0

def test_unknown_old_charge_blocks_new_round(ledger):
    with pytest.raises(Blocked):
        with Stage(ledger,'ingestion'):
            send(ledger,'ingestion','embedding',httpx.MockTransport(lambda r:httpx.Response(500,json={})))
    with pytest.raises(Blocked,match='UNRECONCILED_PRIOR_REQUEST'):ledger.begin_round()

def test_live_model_ceiling_and_highest_tier_reservation(tmp_path):
    ledger=authorize(provision(tmp_path/'live-offline-fixture.sqlite',offline=False))
    # Direct ledger arithmetic only: no HTTP transport or live provider is executed.
    seq=ledger.reserve('ingestion','embedding',payload('embedding','ingestion'),offline=False)
    ledger.settle(seq,200,{'total_tokens':10});ledger.advance('ingestion',True)
    seq=ledger.reserve('rag','llm',payload('llm','rag'),offline=False)
    row=ledger.report()['requests'][-1]
    assert row['input_bound']==1000000
    assert row['reserved_nano']==1000000*1200+128*12000
    ledger.settle(seq,200,{'prompt_tokens':200000,'completion_tokens':10})
    assert ledger.report()['requests'][-1]['cost_nano']==200000*600+10*6000

def test_live_full_round_reservation_fits_five(tmp_path):
    ledger=authorize(provision(tmp_path/'live-arithmetic.sqlite',offline=False))
    for stage in STAGES:
        for kind,count in LIMITS[stage].items():
            for _ in range(count):
                data=payload(kind,stage)
                if kind=='llm':data['max_tokens']=512
                seq=ledger.reserve(stage,kind,data,offline=False)
                ledger.settle(seq,200,usage(kind))
        ledger.advance(stage,True)
    assert ledger.report()['reserved_nano']==4836864000
    assert ledger.report()['used']==7


def test_first_round_runner_all_business_stages_mock(tmp_path):
    from first_live_round import execute
    from app.core.config import Settings
    ledger=authorize(provision(tmp_path/'runner.sqlite'))
    cfg=Settings(_env_file=None,database_url='sqlite:///'+(tmp_path/'business.sqlite').as_posix(),data_dir=tmp_path/'data',
                 llm_base_url='https://offline.invalid/v1',llm_model='qwen-flash',llm_api_key='OFFLINE',
                 embedding_base_url='https://offline.invalid/v1',embedding_model='text-embedding-v4',embedding_api_key='OFFLINE',
                 live_test_mode=True,live_test_ledger=ledger.path,embedding_batch_size=10,retrieval_min_score=0,
                 chunk_size=800,chunk_overlap=150,llm_max_tokens=512,agent_max_rounds=2,agent_max_tool_calls=1)
    model_calls=[]
    def reply(req):
        body=json.loads(req.content);model_calls.append(body)
        if body.get('tools'):
            if body['messages'][-1]['role']=='tool':
                return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':'计算结果为125。'},'finish_reason':'stop'}],'usage':usage('llm')})
            return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':'calc-1','type':'function','function':{'name':'calculator','arguments':'{"expression":"(125+375)/4"}'}}]},'finish_reason':'tool_calls'}],'usage':usage('llm')})
        answer='每次持续90分钟。[1]' if body['stream'] else '每周三20:00开始。[1]'
        if body['stream']:
            events=[{'choices':[{'delta':{'content':answer},'finish_reason':None}]},{'choices':[{'delta':{},'finish_reason':'stop'}]},{'choices':[],'usage':usage('llm')}]
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content=''.join('data: '+json.dumps(x)+'\n\n' for x in events)+'data: [DONE]\n\n')
        return httpx.Response(200,json={'choices':[{'message':{'content':answer},'finish_reason':'stop'}],'usage':usage('llm')})
    result=execute(cfg,ledger,offline=True,embedding_transport=httpx.MockTransport(response),llm_transport=httpx.MockTransport(reply))
    assert result['tests']==dict.fromkeys(STAGES,'PASS'),result
    assert result['calculator_executed'] and result['tool_result_returned']
    assert result['ledger']['used']==7 and len(model_calls)==4


@pytest.mark.parametrize('body,code,message',[({'error':{'code':'AccessDenied.Unpurchased','message':'Account is overdue.'},'request_id':'req-fixture-403'},'AccessDenied.Unpurchased','Account is overdue.'),({'Code':'Forbidden','Message':'Permission denied.','RequestId':'req-fixture-403'},'Forbidden','Permission denied.')])
def test_error_response_records_provider_fields(ledger,body,code,message):
    with pytest.raises(Blocked):
        with Stage(ledger,'ingestion'):
            send(ledger,'ingestion','embedding',httpx.MockTransport(lambda r:httpx.Response(403,json=body)))
    row=ledger.report()['requests'][0]
    assert row['error_type']=='HTTP_403' and row['usage_status']=='UNKNOWN'
    assert row['server_error']=={'http_status':403,'code':code,'message':message,'request_id':'req-fixture-403'}


def test_nonjson_403_not_usage_parse_error(ledger):
    with pytest.raises(Blocked):
        with Stage(ledger,'ingestion'):
            send(ledger,'ingestion','embedding',httpx.MockTransport(lambda r:httpx.Response(403,text='<html>secret contents not persisted</html>',headers={'x-request-id':'req-from-header'})))
    row=ledger.report()['requests'][0]
    assert row['error_type']=='HTTP_403' and row['server_error']['request_id']=='req-from-header'
    assert 'secret contents' not in json.dumps(row)


def test_error_message_credentials_redacted(ledger):
    body={'error':{'code':'Forbidden','message':'api_key=sk-offline-shouldneverbeprinted Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345 https://private.invalid/secret'}}
    with pytest.raises(Blocked):
        with Stage(ledger,'ingestion'):
            send(ledger,'ingestion','embedding',httpx.MockTransport(lambda r:httpx.Response(403,json=body)))
    saved=json.dumps(ledger.report())
    for secret in ('sk-offline-shouldneverbeprinted','abcdefghijklmnopqrstuvwxyz012345','private.invalid'):
        assert secret not in saved
    assert 'REDACTED' in saved


def failed_parent(ledger):
    with pytest.raises(Blocked):
        with Stage(ledger,'ingestion'):
            send(ledger,'ingestion','embedding',httpx.MockTransport(lambda r:httpx.Response(403,text='fixture')))


def test_diagnostic_keeps_history_and_allows_only_one(ledger):
    failed_parent(ledger)
    original=ledger.report(); old_row=original['requests'][0].copy()
    diagnostic=authorize(ledger.begin_diagnostic(grant_id='single-user-grant',console_permissions_confirmed=True))
    assert diagnostic.report()['max_requests']==1 and diagnostic.report()['manual_budget_yuan']==1
    with Stage(diagnostic,'ingestion') as scope:
        send(diagnostic,'ingestion','embedding');scope.verify(True)
    assert diagnostic.report()['state']=='COMPLETE' and diagnostic.report()['used']==1
    assert ledger.report()['state']=='HALTED' and ledger.report()['requests'][0]==old_row
    assert diagnostic.report()['project_cost_nano'] is None
    assert diagnostic.report()['project_reserved_nano']>original['project_reserved_nano']
    with pytest.raises(Blocked):
        diagnostic.reserve('ingestion','embedding',payload('embedding','ingestion'),offline=True)
    assert diagnostic.report()['used']==1
    with pytest.raises(Blocked,match='DIAGNOSTIC_GRANT_ALREADY_USED'):
        ledger.begin_diagnostic(grant_id='single-user-grant',console_permissions_confirmed=True)


def test_diagnostic_requires_console_confirmation(ledger):
    failed_parent(ledger)
    with pytest.raises(Blocked,match='CONSOLE_CONFIRMATION_REQUIRED'):
        ledger.begin_diagnostic(grant_id='grant')
    assert ledger.report()['used']==1


def test_diagnostic_refuses_llm_and_stage_borrowing(ledger):
    failed_parent(ledger)
    diagnostic=authorize(ledger.begin_diagnostic(grant_id='grant-only-embed',console_permissions_confirmed=True))
    with pytest.raises(Blocked):
        diagnostic.reserve('ingestion','llm',payload('llm','rag'),offline=True)
    assert diagnostic.report()['used']==0


def test_diagnostic_one_yuan_cap_enforced(ledger):
    failed_parent(ledger)
    diagnostic=authorize(ledger.begin_diagnostic(grant_id='grant-money',console_permissions_confirmed=True))
    with diagnostic.transaction() as (c,r):c.execute('UPDATE run SET embedding_rate=1000000 WHERE id=?',(diagnostic.run_id,))
    with pytest.raises(Blocked,match='ROUND_MONEY_LIMIT'):
        diagnostic.reserve('ingestion','embedding',payload('embedding','ingestion'),offline=True)
    assert diagnostic.report()['used']==0
