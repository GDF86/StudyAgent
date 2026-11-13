"""Single first-round runner. Import is inert; no retries, no automatic new round."""
import argparse
import json
import logging
import os
from pathlib import Path
import sys
import time
import uuid
from urllib.parse import urlsplit

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BACKEND))
from app.core.live_test import Ledger, Stage, provision, Blocked

DOCUMENT = "StudyAgent 首次验证资料。课程代号是晨光。学习活动固定在每周三20:00开始，每次持续90分钟。资料仅用于验证检索、引用与模型接口。"
SOURCES = "https://help.aliyun.com/zh/model-studio/qwen-flash ; https://help.aliyun.com/zh/model-studio/embedding-interfaces-compatible-with-openai"

def require(condition, reason):
    if not condition: raise Blocked(reason)

def execute(settings, ledger, *, offline=False, embedding_transport=None, llm_transport=None):
    # Importing app here cannot send HTTP; tests disable dotenv source before import.
    from app.main import create_app
    from fastapi.testclient import TestClient
    from app.rag.embeddings import CompatibleEmbeddingProvider
    from app.llm.provider import CompatibleLLMProvider
    result={"tests":{s:"NOT_EXECUTED" for s in ("ingestion","rag","stream","agent")},
            "calculator_executed":False,"tool_result_returned":False,"business_database":settings.database_url,
            "data_directory":str(settings.data_dir)}
    active=None
    try:
        with TestClient(create_app(settings)) as client:
            if offline:
                client.app.state.documents.embeddings=CompatibleEmbeddingProvider(settings,embedding_transport)
                client.app.state.chat.provider=CompatibleLLMProvider(settings,llm_transport)
                client.app.state.agent.provider=client.app.state.chat.provider
            kb=client.post('/api/knowledge-bases',json={"name":"百炼首轮验证-"+uuid.uuid4().hex[:10]})
            require(kb.status_code==201,"KB_CREATION_FAILED")
            kb_id=kb.json()['id'];result['knowledge_base_id']=kb_id
            active='ingestion'
            with Stage(ledger,active,offline=offline) as scope:
                response=client.post(f'/api/knowledge-bases/{kb_id}/documents',files={'file':('first-live-fixture.txt',DOCUMENT.encode('utf-8'),'text/plain')})
                require(response.status_code==201,"INGESTION_HTTP_FAILED")
                doc=response.json();result['document_id']=doc['id']
                index=client.app.state.documents.vectors.read(doc['id'])
                scope.verify(doc['status']=='ready' and doc['chunk_count']==1 and index.ntotal==1 and index.d>0)
            result['tests'][active]='PASS'
            active='rag'
            with Stage(ledger,active,offline=offline) as scope:
                response=client.post('/api/chat',json={'knowledge_base_id':kb_id,'question':'资料中学习活动固定在星期几、几点开始？请简短回答并引用来源。'})
                require(response.status_code==200,"RAG_HTTP_FAILED")
                answer=response.json();text=answer.get('answer','')
                scope.verify(answer.get('status')=='answered' and '三' in text and any(x in text for x in ('20:00','20：00','20点','八点','8点'))
                    and bool(answer.get('citations')) and all(c['document_id']==doc['id'] and c['content'] in DOCUMENT for c in answer['citations']))
            result['tests'][active]='PASS'
            active='stream'
            with Stage(ledger,active,offline=offline) as scope:
                response=client.post('/api/chat/stream',json={'knowledge_base_id':kb_id,'question':'资料中每次学习活动持续多少分钟？请简短回答并引用来源。'})
                require(response.status_code==200,"STREAM_HTTP_FAILED")
                frames=[json.loads(frame[5:].strip()) for frame in response.text.splitlines() if frame.startswith('data:')]
                require(not any(f['type']=='error' for f in frames),"STREAM_ERROR_EVENT")
                done=[f for f in frames if f['type']=='done']
                scope.verify(bool(done) and '90' in done[-1]['answer'] and done[-1]['status']=='answered'
                    and bool(done[-1]['citations']) and all(c['document_id']==doc['id'] for c in done[-1]['citations'])
                    and any(f['type']=='token' for f in frames) and any(f['type']=='citation' for f in frames))
            result['tests'][active]='PASS'
            active='agent'
            context,messages=__import__('asyncio').run(client.app.state.agent.prepare(type('Task',(),{'knowledge_base_id':kb_id,'task':'请计算 (125+375)/4，并说明结果。'})()))
            original=context.execute
            async def audited_execute(name,raw):
                require(name=='calculator',"UNEXPECTED_TOOL")
                output=await original(name,raw)
                require(output.get('value')==125,"CALCULATOR_INCORRECT")
                result['calculator_executed']=True
                return output
            context.execute=audited_execute
            # The second LLM request must carry the successful backend tool result.
            # Guard validation additionally checks this payload before pre-debit.
            with Stage(ledger,active,offline=offline) as scope:
                final=__import__('asyncio').run(client.app.state.agent.run(context,messages))
                returned=[m for m in messages if m.get('role')=='tool' and json.loads(m['content']).get('ok') is True]
                result['tool_result_returned']=bool(returned)
                scope.verify(result['calculator_executed'] and result['tool_result_returned'] and final['tool_calls']==1 and '125' in final['answer'])
            result['tests'][active]='PASS'
    except BaseException as exc:
        if active:result['tests'][active]='FAILED'
        # Never dump upstream text, request URLs, keys, headers or exception repr.
        result['stopped_reason']=exc.code if isinstance(exc,Blocked) else type(exc).__name__
        try:ledger.halt()
        except Blocked:pass
    result['ledger']=ledger.report()
    return result

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--execute-authorized-first-round',action='store_true')
    args=parser.parse_args()
    require(args.execute_authorized_first_round,"EXPLICIT_EXECUTION_FLAG_REQUIRED")
    # Clear this child process' settings overrides by NAME only; never print values.
    import ast
    tree=ast.parse((BACKEND/'app/core/config.py').read_text(encoding='utf-8'))
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Settings')
    for node in cls.body:
        if isinstance(node,ast.AnnAssign):os.environ.pop(node.target.id.upper(),None)
    from app.core.config import Settings
    settings=Settings(_env_file=BACKEND/'.env',live_test_mode=True,
                      live_test_ledger=BACKEND/'test-results/bailian-first-live/budget.sqlite',
                      llm_max_tokens=512,agent_max_rounds=2,agent_max_tool_calls=1)
    require(settings.llm_model=='qwen-flash' and settings.embedding_model=='text-embedding-v4',"MODELS_MISMATCH")
    require(not settings.enable_reranker,"RERANKER_MUST_BE_DISABLED")
    for url in (settings.llm_base_url,settings.embedding_base_url):
        parsed=urlsplit(url)
        require(parsed.scheme=='https' and parsed.hostname.endswith('.cn-beijing.maas.aliyuncs.com')
                and parsed.path.rstrip('/')=='/compatible-mode/v1' and not parsed.query and not parsed.fragment
                and not parsed.username and not parsed.password,"BEIJING_ENDPOINT_REQUIRED")
    require(settings.llm_api_key.get_secret_value() and settings.embedding_api_key.get_secret_value(),"KEY_MISSING")
    require(settings.data_dir.resolve()==(BACKEND/'data/bailian-real-20261109').resolve(),"ISOLATED_DATA_REQUIRED")
    require(settings.database_url=='sqlite:///'+(BACKEND/'studyagent-bailian-20261109.db').as_posix(),"ISOLATED_DATABASE_REQUIRED")
    path=settings.live_test_ledger
    ledger=Ledger(path) if path.exists() else provision(path,offline=False)
    before=ledger.report()
    require(before['used']==0 and before['state']=='READY',"FIRST_ROUND_ALREADY_ATTEMPTED")
    ledger.authorize(input_rate=150,output_rate=1500,embedding_rate=500,
                     console_evidence=SOURCES,token_bound_attested=True,valid_until=time.time()+3600)
    for logger in ('httpx','httpcore'):logging.getLogger(logger).setLevel(logging.CRITICAL)
    report=execute(settings,ledger)
    output=BACKEND/'test-results/bailian-first-live/first-round-result.json'
    with output.open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    # Contains only fixture resource IDs, local paths and sanitized ledger metadata.
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':
    try:main()
    except BaseException as exc:
        print(json.dumps({'stopped_before_or_during_run':exc.code if isinstance(exc,Blocked) else type(exc).__name__}))
        raise SystemExit(1)
