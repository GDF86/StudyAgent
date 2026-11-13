"""Explicit, bounded four-tool validation. No HTTP at import."""
import asyncio,ast,hashlib,json,logging,os,sqlite3,sys,time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
BACKEND=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BACKEND))
from calculator_development_once import require,snapshot
from app.core.live_test import Ledger,Stage,Blocked
TOOLS=['search_documents','summarize_document','compare_documents','generate_quiz']
SECOND='标题：StudyAgent 第二份测试资料\n\n项目代号为 SA-2027，测试城市为广州，测试编号为 RAG-2048。系统同样支持文档检索和知识库问答，同时新增学习进度分析。本资料只用于文档对比测试。'
QUESTIONS=['请使用 search_documents 工具查找 StudyAgent 测试资料中的项目代号。','请使用 summarize_document 工具总结 StudyAgent 测试资料的主要内容。','请使用 compare_documents 工具比较这两篇测试资料的项目代号、城市、编号、共同功能和新增功能。','请使用 generate_quiz 工具，根据 StudyAgent 测试资料生成两道选择题，并给出答案。']

async def check(agent,kb,ledger,name,question,documents,offline=False):
    context,messages=await agent.prepare(SimpleNamespace(knowledge_base_id=kb,task=question))
    original=context.execute;provider=agent.provider;turns=[];result={'tool':name,'status':'FAILED'}
    async def execute(tool,raw):
        require(tool==name,'UNEXPECTED_TOOL')
        args=json.loads(raw)
        if name=='summarize_document':require(args.get('document_id')==documents[0],'WRONG_DOCUMENT')
        if name=='compare_documents':require(set(args.get('document_ids',[]))==set(documents),'WRONG_DOCUMENTS')
        if name=='generate_quiz':require(args.get('count')==2 and args.get('question_type')=='multiple_choice','WRONG_QUIZ_PARAMETERS')
        output=await original(tool,raw)
        citations=output.get('citations',[])
        require(bool(citations) and all(c['document_id'] in documents for c in citations),'TOOL_EVIDENCE_MISSING')
        evidence=' '.join(c['content'] for c in citations)
        if name=='search_documents':require('SA-2026' in evidence,'SEARCH_EVIDENCE_INVALID')
        if name in ('summarize_document','compare_documents'):
            expected=('SA-2026','深圳','RAG-1024') if name=='summarize_document' else ('SA-2026','深圳','RAG-1024','SA-2027','广州','RAG-2048','学习进度分析')
            require(all(x in output.get('text','') for x in expected),'TOOL_TEXT_INCOMPLETE')
        if name=='generate_quiz':
            require(len(output.get('questions',[]))==2,'QUIZ_COUNT_INVALID')
            for q in output['questions']:
                require(len(q['options'])==4 and q['answer'] in q['options'] and bool(q['citation_ids']),'QUIZ_INVALID')
                require(any(x in q['answer'] for x in ('SA-2026','SA-2027','深圳','广州','RAG-1024','RAG-2048','文档检索','知识库问答','学习进度分析')),'QUIZ_GROUNDING_REQUIRES_REVIEW')
        result.update(arguments=args,tool_result=output,executed=True)
        return output
    context.execute=execute
    class Audit:
        async def tool_turn(self,msgs,tools):
            require(len(turns)<2 and len(tools)==1 and tools[0]['function']['name']==name,'EXTRA_DECISION_OR_TOOL')
            if turns:
                require(msgs[-1].get('role')=='tool' and msgs[-1].get('tool_call_id')==turns[0].tool_calls[0].id,'MISSING_TOOL_RETURN')
                data=json.loads(msgs[-1]['content'])
                require(data.get('ok') is True and data['result']==result['tool_result'],'TOOL_RETURN_MISMATCH')
                result['returned']=True
            turn=await provider.tool_turn(msgs,tools);turns.append(turn)
            if len(turns)==1:require(len(turn.tool_calls)==1 and turn.tool_calls[0].function.name==name,'MISSING_REAL_TOOL_CALL')
            else:require(not turn.tool_calls,'EXTRA_TOOL_CALL')
            return turn
    agent.provider=Audit()
    try:
        with Stage(ledger,'agent',offline=offline) as scope:
            final=await agent.run(context,messages);result['final']=final
            expected=('SA-2026',) if name=='search_documents' else (('SA-2026','深圳','RAG-1024') if name=='summarize_document' else (('SA-2026','深圳','RAG-1024','SA-2027','广州','RAG-2048','学习进度分析','文档检索','问答') if name=='compare_documents' else tuple(q['answer'] for q in result['tool_result']['questions'])))
            require(all(x in final['answer'] for x in expected) and bool(final['citations']),'FINAL_ANSWER_OR_CITATION_INVALID')
            scope.verify(result.get('executed') and result.get('returned') and len(turns)==2 and final['tool_calls']==1)
        ledger.complete_development_session();result['status']='PASS'
    except BaseException as exc:
        ledger.halt();result['error']=exc.message if isinstance(exc,Blocked) else type(exc).__name__
    finally:agent.provider=provider
    result['ledger']=ledger.report()
    return result

def main():
    require(sys.argv[1:]==['--execute-authorized-four-tools'],'EXPLICIT_FLAG_REQUIRED')
    cls=next(n for n in ast.parse((BACKEND/'app/core/config.py').read_text(encoding='utf-8')).body if isinstance(n,ast.ClassDef) and n.name=='Settings')
    for n in cls.body:
        if isinstance(n,ast.AnnAssign):os.environ.pop(n.target.id.upper(),None)
    from app.core.config import Settings
    from app.main import create_app
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session
    from sqlalchemy import select
    from app.database.models import KnowledgeBase,Document,Chunk
    path=BACKEND/'test-results/bailian-first-live/budget.sqlite';out=path.parent/'remaining-tools-20261110.json'
    require(not out.exists(),'ALREADY_EXECUTED')
    cfg=Settings(_env_file=BACKEND/'.env',live_test_mode=True,live_test_ledger=path,llm_max_tokens=512,agent_max_rounds=2,agent_max_tool_calls=1,llm_timeout_seconds=40,agent_timeout_seconds=120)
    require(cfg.llm_model=='qwen-flash' and cfg.embedding_model=='text-embedding-v4' and bool(cfg.llm_api_key) and bool(cfg.embedding_api_key) and not cfg.enable_reranker,'MODEL_CONFIG_INVALID')
    for url in (cfg.llm_base_url,cfg.embedding_base_url):
        u=urlsplit(url);require(u.scheme=='https' and u.hostname.endswith('.cn-beijing.maas.aliyuncs.com') and u.path.rstrip('/')=='/compatible-mode/v1' and not u.query and not u.fragment and not u.username,'URL_INVALID')
    require(cfg.data_dir.resolve()==(BACKEND/'data/bailian-real-20261109').resolve() and cfg.database_url=='sqlite:///'+(BACKEND/'studyagent-bailian-20261109.db').as_posix(),'STORAGE_NOT_ISOLATED')
    old=snapshot(path);require(len(old['requests'])==11,'HISTORY_COUNT_INVALID');seal=path.with_suffix('.sqlite.seal').read_bytes()
    parent=Ledger(path,old['requests'][-1][-1]);results=[]
    for name in ('httpx','httpcore'):logging.getLogger(name).setLevel(logging.CRITICAL)
    with TestClient(create_app(cfg)) as client:
        with Session(client.app.state.engine) as s:
            kb=s.scalar(select(KnowledgeBase).where(KnowledgeBase.name=='Bailian-RAG-Live-Test'));require(kb is not None,'KB_MISSING');kb_id=kb.id
            docs=s.scalars(select(Document).where(Document.knowledge_base_id==kb_id,Document.status=='ready')).all()
            require(len(docs)==1 and docs[0].filename=='bailian-rag-fixture.txt','UNEXPECTED_TEST_DATA')
            chunks=s.scalars(select(Chunk).where(Chunk.document_id==docs[0].id)).all()
            from rag_live_round import DOCUMENT
            require(len(chunks)==1 and chunks[0].content==DOCUMENT,'TEST_DATA_NOT_PUBLIC_FIXTURE')
            ids=[docs[0].id]
        for name,question in zip(TOOLS,QUESTIONS):
            ledger=parent.begin_development_session(grant_id='20261110-four-tools-'+name,allowed_tools=[name],allow_embedding=name in ('search_documents','compare_documents','generate_quiz'),max_requests=8)
            ledger.authorize(input_rate=150,output_rate=1500,embedding_rate=500,token_bound_attested=True,console_evidence='20261110 official Beijing qwen-flash 0.15/1.5 and text-embedding-v4 0.50 per million; development bounded text policy',valid_until=time.time()+3600)
            try:
                if name=='compare_documents':
                    with Stage(ledger,'ingestion',offline=False) as scope:
                        r=client.post(f'/api/knowledge-bases/{kb_id}/documents',files={'file':('bailian-second-fixture.txt',SECOND.encode(),'text/plain')})
                        require(r.status_code==201,'SECOND_DOCUMENT_FAILED');d=r.json();ids.append(d['id']);index=client.app.state.documents.vectors.read(d['id'])
                        scope.verify(d['status']=='ready' and d['chunk_count']==index.ntotal==1 and index.d==1024)
                result=asyncio.run(check(client.app.state.agent,kb_id,ledger,name,question,ids[:1] if name in ('search_documents','summarize_document') else ids))
            except BaseException as exc:
                ledger.halt();result={'tool':name,'status':'FAILED','error':exc.message if isinstance(exc,Blocked) else type(exc).__name__,'ledger':ledger.report()}
            results.append(result);parent=ledger
            if result['status']!='PASS':break
    now=snapshot(path)
    report={'tools':results,'history_preserved':all(all(row in now[t] for row in rows) for t,rows in old.items()) and seal==path.with_suffix('.sqlite.seal').read_bytes(),'new_requests':len(now['requests'])-11,'project_requests':len(now['requests']),'project_known_cost_nano':sum(r[17] or 0 for r in now['requests']),'risk_nano':parent.report()['project_risk_nano']}
    with out.open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps(report,ensure_ascii=True,indent=2))
if __name__=='__main__':
    try:main()
    except BaseException as exc:
        print(json.dumps({'stopped':exc.message if isinstance(exc,Blocked) else type(exc).__name__}));raise SystemExit(1)
