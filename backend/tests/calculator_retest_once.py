"""Explicit 2-attempt incident retest. Trace lives in ignored local test-results."""
import argparse,asyncio,json,sys,logging
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from calculator_recovery_entry import settings
from calculator_retest_guard import CalculatorRetestLedger
from app.core.calculator_recovery import code_hash

def require(condition):
    if not condition:raise RuntimeError('CALCULATOR_RETEST_VALIDATION_FAILED')

async def execute(app,ledger,out,*,offline=False,knowledge_base_id=2):
    from app.core.live_test import Stage
    from app.schemas.conversation import ConversationCreate
    from app.schemas.agent import AgentRequest
    from uuid import uuid4
    trace={'turns':[],'executions':[],'state':'running'}
    def save():out.write_text(json.dumps(trace,ensure_ascii=False,indent=2),encoding='utf8')
    agent=app.state.agent;provider=agent.provider;prepare=agent.prepare
    class Audited:
        async def tool_turn(self,messages,tools):
            if len(trace['turns'])>=2:raise RuntimeError('THIRD_DECISION_FORBIDDEN')
            if trace['turns']:
                require(messages[-1]['role']=='tool' and messages[-1]['tool_call_id']==trace['turns'][0]['tool_calls'][0]['id'])
                trace['return_message']=messages[-1];save()
            turn=await provider.tool_turn(messages,[t for t in tools if t['function']['name']=='calculator'])
            trace['turns'].append(turn.model_dump());save()
            if len(trace['turns'])==1:require(len(turn.tool_calls)==1 and turn.tool_calls[0].function.name=='calculator')
            else:require(not turn.tool_calls)
            return turn
    async def audited_prepare(body):
        context,messages=await prepare(body);real=context.execute
        async def tool(name,args):
            require(name=='calculator' and not trace['executions'])
            value=await real(name,args);trace['executions'].append({'name':name,'arguments':json.loads(args),'result':value});save()
            require(value['value']==256)
            return value
        context.execute=tool;return context,messages
    cid=app.state.conversations.create(ConversationCreate(knowledge_base_id=knowledge_base_id,title='Calculator第二轮受控复测',mode='agent')).id
    agent.provider=Audited();agent.prepare=audited_prepare
    try:
        with Stage(ledger,'agent',offline=offline,allow_application_failure=True) as scope:
            scope.history_enabled=True;scope.output_limit=2048
            result=await app.state.execution.run(app,AgentRequest(knowledge_base_id=knowledge_base_id,task='请使用calculator计算（128 × 8）÷ 4，并告诉我结果。',conversation_id=cid,request_id=uuid4()),'agent')
            trace['final']=result
            from sqlalchemy.orm import Session
            from app.database.models import Message
            with Session(app.state.conversations.engine) as db:
                row=db.get(Message,result['assistant_message_id'])
                require(row.status=='completed' and row.content==result['answer'])
                trace['history_status']=row.status
            require(len(trace['turns'])==2 and len(trace['executions'])==1 and result['tool_calls']==1 and '256' in result['answer'])
            trace['state']='completed';scope.verify(True)
    except BaseException as exc:
        trace['state']='failed';trace['error_type']=type(exc).__name__
        trace['error_code']=getattr(exc,'code',None)
        raise
    finally:
        agent.provider=provider;agent.prepare=prepare;trace['conversation_id']=str(cid)
        from sqlalchemy import select
        from sqlalchemy.orm import Session
        from app.database.models import Message
        try:
            with Session(app.state.conversations.engine) as db:
                row=db.scalar(select(Message).where(Message.conversation_id==str(cid),Message.role=='assistant'))
                trace['history_status']=row.status if row else None
                trace['history_error_code']=row.error_code if row else None
        except Exception as exc:
            trace['history_read_error']=type(exc).__name__
        trace['billing']=ledger.report();save()

def main():
    p=argparse.ArgumentParser();p.add_argument('--execute-approved-two',action='store_true',required=True);p.add_argument('--run-id',required=True);p.add_argument('--ticket',type=Path,required=True);a=p.parse_args()
    import socket
    listener=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    listener.bind(('127.0.0.1',8010))
    cfg=settings();path=cfg.live_test_ledger;ledger=CalculatorRetestLedger(path,a.run_id)
    ticket=json.loads(a.ticket.read_text(encoding='utf8'))
    with ledger.transaction() as (c,r):
        receipt=c.execute('SELECT * FROM calculator_recovery_receipts WHERE ticket_id=? AND new_run=?',(ticket['ticket_id'],a.run_id)).fetchone()
        from app.core.calculator_recovery import digest, TARGET
        from app.core.personal_learning import fingerprint, saved
        import time
        require(ticket['target']==TARGET and ticket['code_hash']==code_hash())
        require(receipt and receipt['authorized'] is not None and receipt['evidence']==digest(ticket))
        profile=saved(c)
        require(profile and profile['binding']==fingerprint(cfg))
        require(r['verified_until']>time.time())
        require(r['authorized'] and r['state']=='READY' and (r['input_rate'],r['output_rate'],r['embedding_rate'])==(150,1500,500))
        require(not c.execute("SELECT 1 FROM requests WHERE status='PENDING' OR run_id=?",(a.run_id,)).fetchone())
    # In-process only; no public HTTP listener. Existing startup recovery must not be used.
    from app.database.session import build_engine
    from app.services.documents import DocumentService
    from app.rag.embeddings import CompatibleEmbeddingProvider
    from app.rag.retriever import Retriever
    from app.llm.provider import CompatibleLLMProvider
    from app.services.preferences import PreferenceService
    from app.services.conversations import ConversationService
    from app.services.conversation_memory import ConversationMemoryService
    from app.services.conversation_execution import ConversationExecutionService
    from app.agent.agent import AgentService
    engine=build_engine(cfg.database_url);docs=DocumentService(cfg,CompatibleEmbeddingProvider(cfg));prefs=PreferenceService(engine);conv=ConversationService(engine,docs.lock)
    state=SimpleNamespace(conversations=conv,agent=AgentService(cfg,engine,docs,Retriever(cfg,docs),CompatibleLLMProvider(cfg),prefs),execution=ConversationExecutionService(cfg,conv,ConversationMemoryService(cfg,conv,prefs)))
    out=ROOT/'test-results/calculator-second-private/trace.json';out.parent.mkdir(parents=True,exist_ok=True)
    if out.exists():raise RuntimeError('DO_NOT_OVERWRITE_PREVIOUS_TRACE')
    try:asyncio.run(execute(SimpleNamespace(state=state),ledger,out))
    finally:engine.dispose()
if __name__=='__main__':
    for name in ('httpx','httpcore','uvicorn.error'):logging.getLogger(name).setLevel(logging.CRITICAL)
    main()
