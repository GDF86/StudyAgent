"""One explicitly authorized RAG-only round. Import is inert; no retries/reset."""
import ast
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
import time
from urllib.parse import urlsplit

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from app.core.live_test import Ledger, Stage, Blocked

GRANT = '20261109-user-authorized-rag-five-requests-five-yuan-v1'
DOCUMENT = ('标题：StudyAgent 测试资料\n\n'
    'StudyAgent 是一个面向学习场景的 AI 知识库系统。本次测试资料规定：项目代号为 SA-2026，测试城市为深圳，测试编号为 RAG-1024。'
    '项目的主要功能包括文档检索、基于知识库的问答和学习辅助。以上信息仅用于验证真实 RAG 检索与回答，不代表任何外部事实。')

def require(condition, reason):
    if not condition: raise Blocked(reason)

def execute(settings, ledger, *, offline=False, embedding_transport=None, llm_transport=None):
    from app.main import create_app
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session
    from sqlalchemy import select
    from app.database.models import Chunk, Document
    from app.rag.embeddings import CompatibleEmbeddingProvider
    from app.llm.provider import CompatibleLLMProvider
    result = {'tests':dict.fromkeys(('ingestion','rag','stream'),'NOT_EXECUTED'), 'answers':{},
              'database':settings.database_url,'data_directory':str(settings.data_dir)}
    active = None
    try:
        with TestClient(create_app(settings)) as client:
            if offline:
                client.app.state.documents.embeddings = CompatibleEmbeddingProvider(settings, embedding_transport)
                client.app.state.chat.provider = CompatibleLLMProvider(settings, llm_transport)
            retrieved = []
            original = client.app.state.chat.retriever.retrieve
            def audited_retrieve(*args, **kwargs):
                found = original(*args, **kwargs)
                retrieved.append([c.model_dump() for c in found.citations])
                return found
            client.app.state.chat.retriever.retrieve = audited_retrieve
            response = client.post('/api/knowledge-bases',json={'name':'Bailian-RAG-Live-Test'})
            require(response.status_code == 201, 'KB_CREATION_FAILED')
            kb = response.json()['id']; result['knowledge_base_id'] = kb
            active = 'ingestion'
            with Stage(ledger, active, offline=offline) as scope:
                response = client.post(f'/api/knowledge-bases/{kb}/documents',files={'file':('bailian-rag-fixture.txt',DOCUMENT.encode('utf-8'),'text/plain')})
                require(response.status_code == 201, 'INGESTION_HTTP_FAILED')
                doc = response.json(); result['document_id'] = doc['id']
                index = client.app.state.documents.vectors.read(doc['id'])
                with Session(client.app.state.engine) as session:
                    saved = session.get(Document,doc['id'])
                    chunks = session.scalars(select(Chunk).where(Chunk.document_id==doc['id'])).all()
                    evidence = {c.id:c.content for c in chunks}
                    valid = (saved.status == 'ready' and saved.embedding_dimension == index.d
                             and len(chunks) == 1 and all(x in chunks[0].content for x in ('SA-2026','深圳','RAG-1024')))
                result['vector_dimension'] = index.d
                result['chunk_ids'] = list(evidence)
                scope.verify(valid and doc['chunk_count'] == index.ntotal == 1 and index.d == 1024)
            result['tests'][active] = 'PASS'
            for stage, question, expected in (
                ('rag','StudyAgent 测试资料中的项目代号是什么？请根据文档回答，并给出来源。',('SA-2026',)),
                ('stream','StudyAgent 测试资料中的测试城市和测试编号分别是什么？请引用来源。',('深圳','RAG-1024'))):
                active = stage
                with Stage(ledger,stage,offline=offline) as scope:
                    response = client.post('/api/chat'+('/stream' if stage=='stream' else ''),
                        json={'knowledge_base_id':kb,'question':question})
                    require(response.status_code == 200, 'RAG_HTTP_FAILED')
                    if stage == 'stream':
                        require('text/event-stream' in response.headers.get('content-type',''), 'SSE_TYPE_INVALID')
                        frames = [json.loads(line[5:].strip()) for line in response.text.splitlines() if line.startswith('data:')]
                        done = [f for f in frames if f['type']=='done']
                        require(len(done)==1 and not any(f['type']=='error' for f in frames)
                            and any(f['type']=='citation' for f in frames) and any(f['type']=='token' for f in frames), 'SSE_INCOMPLETE')
                        answer = done[0]
                        require(''.join(f['content'] for f in frames if f['type']=='token') == answer['answer'], 'SSE_TOKEN_MISMATCH')
                        result['sse_event_types'] = [f['type'] for f in frames]
                    else: answer = response.json()
                    citations = answer.get('citations',[])
                    valid = (answer.get('status')=='answered' and all(x in answer['answer'] for x in expected)
                        and bool(citations) and len(retrieved)==(1 if stage=='rag' else 2)
                        and citations == retrieved[-1] and all(
                            c['document_id']==doc['id'] and c['knowledge_base_id']==kb
                            and evidence.get(c['chunk_id'])==c['content']
                            and all(x in c['content'] for x in expected) for c in citations))
                    result['answers'][stage] = {'answer':answer.get('answer'), 'citations':citations}
                    scope.verify(valid)
                result['tests'][stage] = 'PASS'
    except BaseException as exc:
        if active: result['tests'][active] = 'FAILED'
        result['stopped_reason'] = exc.message if isinstance(exc,Blocked) else type(exc).__name__
        ledger.halt()
    result['ledger'] = ledger.report()
    return result

def history(path):
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as c:
        return {table:c.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall()
                for table in ('project','run','requests','response_errors','diagnostic_grants')}

def main():
    require(sys.argv[1:]==['--execute-authorized-five-request-rag'], 'EXPLICIT_FLAG_REQUIRED')
    tree=ast.parse((BACKEND/'app/core/config.py').read_text(encoding='utf-8'))
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Settings')
    for node in cls.body:
        if isinstance(node,ast.AnnAssign): os.environ.pop(node.target.id.upper(),None)
    from app.core.config import Settings
    from app.rag.loaders import TextPage
    from app.rag.splitter import split_pages
    cfg=Settings(_env_file=BACKEND/'.env',live_test_mode=True,llm_max_tokens=512)
    require(cfg.llm_model=='qwen-flash' and cfg.embedding_model=='text-embedding-v4' and not cfg.enable_reranker,'MODEL_CONFIG_INVALID')
    a=cfg.llm_api_key.get_secret_value(); b=cfg.embedding_api_key.get_secret_value()
    require(a and b and a==b,'CONFIRM_CURRENT_LLM_KEY_REQUIRED')
    del a,b
    for base in (cfg.llm_base_url,cfg.embedding_base_url):
        p=urlsplit(base)
        require(p.scheme=='https' and p.hostname and p.hostname.endswith('.cn-beijing.maas.aliyuncs.com')
            and p.path.rstrip('/')=='/compatible-mode/v1' and not p.query and not p.fragment and not p.username and not p.password,'BEIJING_URL_INVALID')
    require(cfg.data_dir.resolve()==(BACKEND/'data/bailian-real-20261109').resolve()
        and cfg.database_url=='sqlite:///'+(BACKEND/'studyagent-bailian-20261109.db').as_posix(),'ISOLATED_STORAGE_REQUIRED')
    path=BACKEND/'test-results/bailian-first-live/budget.sqlite'
    require(Path(cfg.live_test_ledger).resolve()==path.resolve(),'LEDGER_PATH_INVALID')
    output=path.parent/'rag-five-request-result-20261109.json'
    require(not output.exists(),'REPORT_ALREADY_EXISTS')
    old=history(path)
    require(len(old['requests'])==4 and sum(x[3]=='HALTED' for x in old['run'])==3,'HISTORY_UNEXPECTED')
    # Pre-parse exactly the fixture. This performs no network access.
    require(len(DOCUMENT)<300 and len(split_pages([TextPage(DOCUMENT,None)],cfg.chunk_size,cfg.chunk_overlap,cfg.max_chunks))==1,'SINGLE_CHUNK_REQUIRED')
    protected={p:hashlib.sha256(p.read_bytes()).digest() for p in path.parent.iterdir() if p.suffix in ('.seal','.json')}
    parent=Ledger(path,old['requests'][-1][-1])
    ledger=parent.begin_rag_round(grant_id=GRANT)
    ledger.authorize(input_rate=150,output_rate=1500,embedding_rate=500,token_bound_attested=True,
        console_evidence='2026-11-09 official Beijing pricing: https://help.aliyun.com/zh/model-studio/qwen-flash and https://help.aliyun.com/zh/model-studio/text-embedding-v4; model ceilings, highest-tier reserve; user confirmed all-model default-workspace key',valid_until=time.time()+3600)
    for name in ('httpx','httpcore'): logging.getLogger(name).setLevel(logging.CRITICAL)
    result=execute(cfg,ledger)
    now=history(path)
    result['history_preserved']=all(all(row in now[t] for row in rows) for t,rows in old.items()) and all(hashlib.sha256(p.read_bytes()).digest()==h for p,h in protected.items())
    result['project_total_requests']=len(now['requests'])
    with output.open('x',encoding='utf-8') as f: json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':
    try: main()
    except BaseException as exc:
        print(json.dumps({'stopped':exc.message if isinstance(exc,Blocked) else type(exc).__name__}))
        raise SystemExit(1)
