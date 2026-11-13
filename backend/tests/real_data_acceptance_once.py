"""One explicit approved batch; no port opened, no restart, no retries outside Guard."""
import ast,os,sys,json,asyncio,logging,sqlite3,time
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from acceptance_batch import AcceptanceLedger

async def run():
    if sys.argv[1:]!=['--execute-approved-20']:raise RuntimeError('EXPLICIT_BATCH_AUTHORIZATION_REQUIRED')
    tree=ast.parse((ROOT/'app/core/config.py').read_text(encoding='utf8'))
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Settings')
    for n in cls.body:
        if isinstance(n,ast.AnnAssign):os.environ.pop(n.target.id.upper(),None)
    from app.core.config import Settings
    cfg=Settings(_env_file=ROOT/'.env',live_test_mode=True,personal_learning=True,web_demo_enabled=False,llm_max_tokens=2048,agent_max_rounds=2,agent_max_tool_calls=1,memory_window_turns=4,memory_max_context_chars=2500,memory_summary_max_chars=600,memory_summary_input_chars=1800)
    path=ROOT/'test-results/bailian-first-live/budget.sqlite'
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as c:
        ident=c.execute('SELECT id FROM run ORDER BY rowid DESC LIMIT 1').fetchone()[0]
        assert c.execute("SELECT count(*) FROM requests WHERE status='PENDING'").fetchone()[0]==0
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='acceptance_attempts'").fetchone() or not c.execute('SELECT 1 FROM acceptance_attempts WHERE batch=?',(AcceptanceLedger.batch,)).fetchone()
    ledger=AcceptanceLedger(path,ident)
    from app.core.personal_learning import ensure
    current=ensure(ledger,cfg);ledger=AcceptanceLedger(path,current.run_id)
    with ledger.transaction() as (c,r):assert (r['input_rate'],r['output_rate'],r['embedding_rate'])==(150,1500,500),'PRICE_CHANGED_REAPPROVAL_REQUIRED'
    assert cfg.enable_reranker is False
    assert cfg.database_url=='sqlite:///'+(ROOT/'studyagent-bailian-20261109.db').as_posix()
    from app.database.session import build_engine
    from app.services.documents import DocumentService
    from app.rag.embeddings import CompatibleEmbeddingProvider
    from app.llm.provider import CompatibleLLMProvider
    from app.services.preferences import PreferenceService
    from app.services.conversations import ConversationService
    from app.services.conversation_memory import ConversationMemoryService
    from app.services.conversation_execution import ConversationExecutionService
    from app.services.chat import ChatService
    from app.rag.retriever import Retriever
    from app.agent.agent import AgentService
    from app.core.live_test import Stage
    from app.schemas.conversation import ConversationCreate
    from app.schemas.chat import ChatRequest
    from uuid import uuid4
    from sqlalchemy.orm import Session
    engine=build_engine(cfg.database_url);docs=DocumentService(cfg,CompatibleEmbeddingProvider(cfg));prefs=PreferenceService(engine)
    conversations=ConversationService(engine,docs.lock);memory=ConversationMemoryService(cfg,conversations,prefs)
    provider=CompatibleLLMProvider(cfg);retriever=Retriever(cfg,docs)
    app=SimpleNamespace(state=SimpleNamespace(engine=engine,chat=ChatService(cfg,retriever,provider,prefs),conversations=conversations,documents=docs))
    execution=ConversationExecutionService(cfg,conversations,memory)
    out=ROOT/'test-results/real-data-stage1-private';out.mkdir(exist_ok=True)
    def save_trace(value):
        (out/'calculator-trace.json').write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')
    results=[];original=prefs.get();kb=3
    questions=['什么是进程？','进程和线程有什么区别？','进程有哪些基本状态？','什么是死锁？产生死锁需要满足哪些条件？','常见的CPU调度算法有哪些？','请根据这份操作系统资料说明2027年某款尚未发布CPU的具体性能参数。','这份资料中有没有介绍StudyAgent_ZXQ_不存在算法_2026？']
    stage_name='preflight';cid2=None
    try:
        for i,q in enumerate(questions,1):
            stage_name='Q'+str(i)
            prefs.set('answer_style','detailed' if i in (4,5) else 'concise')
            cid=conversations.create(ConversationCreate(knowledge_base_id=kb,title=f'真实资料验收 Q{i}',mode='chat')).id
            if i==2:cid2=cid
            body=ChatRequest(knowledge_base_id=kb,question=q,conversation_id=cid,request_id=uuid4())
            with Stage(ledger,'stream',offline=False) as scope:
                scope.history_enabled=True;scope.learning_output_policy=True;scope.output_limit=1024
                events=[event async for event in execution.events(app,body,'chat')]
                final=next((x for x in events if x['type']=='done'),None)
                results.append({'case':stage_name,'question':q,'events':events,'conversation_id':str(cid),'status':'GENERATED_REQUIRES_FACT_REVIEW' if final else 'FAIL'})
                (out/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf8')
                if not final:raise RuntimeError('ANSWER_NOT_COMPLETED')
                assert all(x['knowledge_base_id']==kb for x in final['citations'])
                scope.verify(True)
        stage_name='followup';prefs.set('answer_style','concise')
        body=ChatRequest(knowledge_base_id=kb,question='它们在资源共享方面还有什么区别？请简洁回答。',conversation_id=cid2,request_id=uuid4())
        with Stage(ledger,'stream',offline=False) as scope:
            scope.history_enabled=True;scope.learning_output_policy=True;scope.output_limit=1024
            events=[x async for x in execution.events(app,body,'chat')]
            assert any(x['type']=='done' for x in events)
            results.append({'case':stage_name,'events':events});scope.verify(True)
        stage_name='calculator'
        agent=AgentService(cfg,engine,docs,retriever,provider,prefs)
        audit_trace={'turns':[],'executions':[]}
        class CalculatorOnly:
            async def tool_turn(self,messages,tools):
                turn=await provider.tool_turn(messages,[t for t in tools if t['function']['name']=='calculator'])
                audit_trace['turns'].append({'tool_calls':[x.model_dump() for x in turn.tool_calls],'content':turn.content,'last_message_role':messages[-1]['role']});save_trace(audit_trace)
                return turn
        agent.provider=CalculatorOnly()
        with Stage(ledger,'agent',offline=False) as scope:
            scope.learning_output_policy=True;scope.output_limit=1024
            context,messages=await agent.prepare(SimpleNamespace(knowledge_base_id=kb,task='请使用calculator工具计算(128*8)/4，并告诉我结果。'))
            real=context.execute;trace=[]
            async def execute(name,args):
                assert name=='calculator'
                output=await real(name,args);trace.append({'tool':name,'arguments':json.loads(args),'output':output});audit_trace['executions']=trace;save_trace(audit_trace);return output
            context.execute=execute
            final=await agent.run(context,messages)
            assert len(trace)==1 and trace[0]['output']['value']==256 and final['tool_calls']==1
            results.append({'case':stage_name,'trace':trace,'final':final});scope.verify(True)
        stage_name='basic'
        for k in (1,5):
            with Stage(ledger,'rag',offline=False) as scope:
                with Session(engine) as db:found=retriever.retrieve(db,kb,'进程和线程有什么区别',k,rerank=False)
                assert found.citations and all(x.knowledge_base_id==kb for x in found.citations)
                results.append({'case':'basic-'+str(k),'citations':[x.model_dump() for x in found.citations]});scope.verify(True)
        (out/'outcome.json').write_text(json.dumps({'status':'COMPLETED_REQUIRES_REVIEW'}),encoding='utf8')
    except BaseException as exc:
        from app.core.runtime_safety import code
        chain=[];current=exc
        while current is not None and len(chain)<8:
            chain.append({'type':type(current).__name__,'reason':code(getattr(current,'message',None)),'code':code(getattr(current,'code',None))});current=current.__context__
        (out/'outcome.json').write_text(json.dumps({'status':'STOPPED','stage':stage_name,'error_type':type(exc).__name__,'error_chain':chain}),encoding='utf8')
    finally:
        (out/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf8')
        for key in ('answer_style','learning_goal','preferred_language'):
            if key in original.overrides:prefs.set(key,original.overrides[key])
            else:prefs.delete(key)
        (out/'ledger.json').write_text(json.dumps(ledger.report(),indent=2),encoding='utf8');engine.dispose()
    print((out/'outcome.json').read_text())

if __name__=='__main__':
    for name in ('httpx','httpcore','uvicorn.error'):logging.getLogger(name).setLevel(logging.CRITICAL)
    asyncio.run(run())
