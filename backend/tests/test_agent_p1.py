import asyncio,json
from types import SimpleNamespace
import httpx,pytest
from app.agent.long_summary import summarize
from app.schemas.chat import Citation
from test_local_learning import local_client
from test_halt_diagnosis import kb
from app.llm.provider import CompatibleLLMProvider

@pytest.mark.parametrize('persistent',[False,True])
@pytest.mark.parametrize('stream',[False,True])
@pytest.mark.parametrize('bad',[None,'args','execution','citation','protocol'])
def test_calculator_closed_loop_and_errors(local_client,stream,bad,persistent):
    c,ledger,_=local_client;ident=kb(c);seen=[]
    def reply(req):
        p=json.loads(req.content);seen.append(p)
        if len(seen)==1:
            args='{' if bad=='args' else json.dumps({'expression':'1/0' if bad=='execution' else '（128 × 8）÷ 4'})
            message={'role':'assistant','content':None,'tool_calls':[{'id':'calc-1','type':'function','function':{'name':'calculator','arguments':args}}]};reason='tool_calls'
        else:
            assert p['messages'][-1]['tool_call_id']=='calc-1'
            assert json.loads(p['messages'][-1]['content'])['result']['value']==256
            if bad=='protocol':return httpx.Response(200,json={'choices':[],'usage':{'prompt_tokens':100,'completion_tokens':10}})
            message={'role':'assistant','content':'256 [1]' if bad=='citation' else '256'};reason='stop'
        return httpx.Response(200,json={'choices':[{'message':message,'finish_reason':reason}],'usage':{'prompt_tokens':100,'completion_tokens':10}})
    c.app.state.agent.provider=CompatibleLLMProvider(c.app.state.settings,httpx.MockTransport(reply))
    body={'knowledge_base_id':ident,'task':'计算（128 × 8）÷ 4'}
    if persistent:
        from uuid import uuid4
        cid=c.post('/api/conversations',json={'knowledge_base_id':ident,'title':'calc','mode':'agent'}).json()['id']
        body.update(conversation_id=cid,request_id=str(uuid4()))
    r=c.post('/api/agent/stream' if stream else '/api/agent/run',json=body)
    if persistent:
        row=c.get(f'/api/conversations/{cid}/messages').json()['items'][-1]
        assert row['status']==('failed' if bad else 'completed')
    if bad:
        assert '"type":"done"' not in r.text.replace(' ','')
        assert ('LLM_INVALID_CITATION' if bad=='citation' else 'INVALID_TOOL_ARGUMENTS' if bad=='args' else 'CALCULATOR_INVALID' if bad=='execution' else 'LLM_INVALID_TOOL_RESPONSE') in r.text
    else:assert '256' in r.text and ledger.report()['state']=='READY'
    assert ledger.report()['state']==('HALTED' if bad=='protocol' else 'READY')

@pytest.mark.parametrize('fail',[False,True])
def test_49_chunks_bounded_complete_or_fail(fail):
    seen=[]
    class Provider:
        async def complete(self,msgs):
            seen.append(msgs)
            if fail and len(seen)==2:
                from app.core.errors import DomainError
                raise DomainError(502,'LLM_UPSTREAM_ERROR','offline')
            assert len(json.dumps(msgs,ensure_ascii=False).encode())<12000
            batch=json.loads(msgs[-1]['content']);first=batch[0].get('id') or batch[0]['source_ids'][0]
            return f'覆盖章节与术语的摘要。[{first}]'
    context=SimpleNamespace(settings=SimpleNamespace(llm_model='qwen-flash',llm_temperature=0.2),provider=Provider())
    context.register=lambda xs:[s.model_copy(update={'id':i+1}) for i,s in enumerate(xs)]
    sources=[Citation(id=0,knowledge_base_id=1,document_id='doc',page=None,document_name='课程',chunk_id=str(i),content=(f'第{i}章：进程、线程、调度。'+('知识内容'*120)),score=1.) for i in range(49)]
    if fail:
        from app.core.errors import DomainError
        with pytest.raises(DomainError) as e:asyncio.run(summarize(context,sources))
        assert e.value.detail['complete'] is False and len(seen)==2
    else:
        result=asyncio.run(summarize(context,sources))
        assert result['coverage']['complete'] and result['coverage']['covered_chunks']==49
        assert 1<len(seen)<=24 and len(context.summary_coverage['chunk_ids'])==49
        assert any('第48章' in x[-1]['content'] for x in seen)

def test_summary_real_provider_guard_boundaries(local_client):
    from app.core.live_test import Stage
    c,ledger,_=local_client;seen=[]
    def reply(req):
        p=json.loads(req.content);seen.append(p)
        batch=json.loads(p['messages'][-1]['content']);first=batch[0].get('id') or batch[0]['source_ids'][0]
        assert p['max_tokens']==512
        assert len(json.dumps(p,ensure_ascii=False,sort_keys=True).encode())*4+8192<=64000
        return httpx.Response(200,json={'choices':[{'message':{'content':f'阶段摘要。[{first}]'},'finish_reason':'stop'}],'usage':{'prompt_tokens':100,'completion_tokens':20}})
    context=SimpleNamespace(settings=c.app.state.settings,provider=CompatibleLLMProvider(c.app.state.settings,httpx.MockTransport(reply)))
    context.register=lambda xs:[s.model_copy(update={'id':i+1}) for i,s in enumerate(xs)]
    sources=[Citation(id=0,knowledge_base_id=1,document_id='doc',page=None,document_name='OS',chunk_id=str(i),content=f'第{i}章'+('进程线程调度'*90),score=1.) for i in range(49)]
    async def run():
        with Stage(ledger,'agent') as scope:
            scope.output_limit=2048
            result=await summarize(context,sources)
            assert result['coverage']['complete'] and scope.output_limit==2048
            scope.verify(True)
    asyncio.run(run())
    assert len(seen)<=24 and all(q['output_bound']==512 and q['usage_status']=='KNOWN' for q in ledger.report()['requests'])

def test_summary_cancellation_stops_remaining_batches():
    seen=[]
    class Provider:
        async def complete(self,messages):
            seen.append(1)
            raise asyncio.CancelledError()
    ctx=SimpleNamespace(settings=SimpleNamespace(llm_model='qwen-flash',llm_temperature=0.2),provider=Provider())
    sources=[Citation(id=0,knowledge_base_id=1,document_id='doc',page=None,document_name='OS',chunk_id=str(i),content=f'{i}'+('学习'*300),score=1.) for i in range(49)]
    with pytest.raises(asyncio.CancelledError):asyncio.run(summarize(ctx,sources))
    assert len(seen)==1

def test_summary_overlap_and_oversize_limits():
    seen=[]
    class Provider:
        async def complete(self,msgs):
            seen.append(msgs);batch=json.loads(msgs[-1]['content']);ident=batch[0].get('id') or batch[0]['source_ids'][0]
            return f'摘要。[{ident}]'
    ctx=SimpleNamespace(settings=SimpleNamespace(llm_model='qwen-flash',llm_temperature=0.2),provider=Provider())
    ctx.register=lambda xs:[s.model_copy(update={'id':i+1}) for i,s in enumerate(xs)]
    def source(i,text):return Citation(id=0,knowledge_base_id=1,document_id='doc',page=None,document_name='OS',chunk_id=str(i),content=text,score=1.)
    result=asyncio.run(summarize(ctx,[source(0,'A'*100+'重叠'*40),source(1,'重叠'*40+'B'*100)]))
    assert result['coverage']['overlap_chars_removed']==80 and result['coverage']['covered_chunks']==2
    before=len(seen)
    from app.core.errors import DomainError
    with pytest.raises(DomainError):asyncio.run(summarize(ctx,[source(i,'x') for i in range(129)]))
    assert len(seen)==before
