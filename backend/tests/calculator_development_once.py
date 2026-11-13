"""Explicit calculator validation using the real Agent and sealed development guard."""
import ast
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

BACKEND=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BACKEND))
from app.core.live_test import Ledger,Stage,Blocked

QUESTION='请使用 calculator 工具计算 (125+375)/4，并告诉我结果。'

def require(value,reason):
    if not value:raise Blocked(reason)

async def validate(agent,kb_id,ledger,offline=False):
    report={'first_tool_calls':False,'tool_executed':False,'result_returned':False,'status':'NOT_EXECUTED'}
    context,messages=await agent.prepare(SimpleNamespace(knowledge_base_id=kb_id,task=QUESTION))
    original_execute=context.execute
    original_provider=agent.provider
    turns=[]
    async def execute(name,raw):
        require(name=='calculator','UNEXPECTED_TOOL')
        parsed=json.loads(raw)
        require(set(parsed)=={'expression'} and isinstance(parsed['expression'],str) and len(parsed['expression'])<=256,'INVALID_CALCULATOR_ARGUMENTS')
        output=await original_execute(name,raw)
        require(output.get('value')==125,'CALCULATOR_RESULT_INCORRECT')
        report.update(tool_executed=True,tool_name=name,tool_arguments=parsed,tool_result=output)
        return output
    context.execute=execute
    class AuditedProvider:
        async def tool_turn(self,msgs,tools):
            require(len(turns)<2,'EXTRA_AGENT_DECISION')
            require(len(tools)==1 and tools[0]['function']['name']=='calculator','TOOLS_NOT_RESTRICTED')
            if turns:
                last=msgs[-1]
                require(last.get('role')=='tool' and last.get('tool_call_id')==turns[0].tool_calls[0].id,'TOOL_RESULT_NOT_RETURNED')
                data=json.loads(last['content'])
                require(data.get('ok') is True and data['result']['value']==125,'INVALID_RETURNED_RESULT')
                report['result_returned']=True
            turn=await original_provider.tool_turn(msgs,tools)
            turns.append(turn)
            if len(turns)==1:
                require(len(turn.tool_calls)==1 and turn.tool_calls[0].function.name=='calculator','EXPECTED_CALCULATOR_TOOL_CALL')
                report['first_tool_calls']=True
            else:require(not turn.tool_calls,'UNEXPECTED_SECOND_TOOL_CALL')
            return turn
    agent.provider=AuditedProvider()
    try:
        with Stage(ledger,'agent',offline=offline) as scope:
            final=await agent.run(context,messages)
            report['answer']=final['answer']
            scope.verify(len(turns)==2 and report['tool_executed'] and report['result_returned'] and final['tool_calls']==1 and '125' in final['answer'])
        ledger.complete_development_session()
        report['status']='PASS'
    except BaseException as exc:
        report['status']='FAILED';report['error']=exc.message if isinstance(exc,Blocked) else type(exc).__name__
        ledger.halt()
    finally:agent.provider=original_provider
    report['ledger']=ledger.report()
    return report

def snapshot(path):
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as c:
        return {t:c.execute(f'SELECT * FROM {t} ORDER BY 1').fetchall() for t in ('project','run','requests','response_errors','diagnostic_grants','rag_grants')}

def main():
    require(sys.argv[1:]==['--execute-authorized-calculator'],'EXPLICIT_FLAG_REQUIRED')
    tree=ast.parse((BACKEND/'app/core/config.py').read_text(encoding='utf-8'))
    for cls in tree.body:
        if isinstance(cls,ast.ClassDef) and cls.name=='Settings':
            for node in cls.body:
                if isinstance(node,ast.AnnAssign):os.environ.pop(node.target.id.upper(),None)
    from app.core.config import Settings
    from app.main import create_app
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session
    from sqlalchemy import select
    from app.database.models import KnowledgeBase
    path=BACKEND/'test-results/bailian-first-live/budget.sqlite'
    output=path.parent/'calculator-development-20261110.json'
    require(not output.exists(),'ALREADY_EXECUTED')
    cfg=Settings(_env_file=BACKEND/'.env',live_test_mode=True,live_test_ledger=path,
        agent_max_rounds=2,agent_max_tool_calls=1,llm_max_tokens=512,llm_timeout_seconds=30,agent_timeout_seconds=90)
    p=urlsplit(cfg.llm_base_url)
    require(cfg.llm_model=='qwen-flash' and bool(cfg.llm_api_key) and not cfg.enable_reranker,'MODEL_CONFIG_INVALID')
    require(p.scheme=='https' and p.hostname and p.hostname.endswith('.cn-beijing.maas.aliyuncs.com') and p.path.rstrip('/')=='/compatible-mode/v1' and not p.query and not p.fragment and not p.username and not p.password,'BEIJING_URL_REQUIRED')
    require(cfg.data_dir.resolve()==(BACKEND/'data/bailian-real-20261109').resolve() and cfg.database_url=='sqlite:///'+(BACKEND/'studyagent-bailian-20261109.db').as_posix(),'ISOLATED_DATA_REQUIRED')
    old=snapshot(path)
    require(len(old['requests'])==9,'UNEXPECTED_HISTORY_COUNT')
    seal=path.with_suffix('.sqlite.seal').read_bytes()
    protected={f:hashlib.sha256(f.read_bytes()).digest() for f in path.parent.glob('*.json')}
    parent=Ledger(path,old['requests'][-1][-1])
    # No provisioning, no key manipulation, no paid probes, no RAG retrieval.
    ledger=parent.begin_development_session(grant_id='20261110-user-development-policy-calculator-v1',allowed_tools=['calculator'],allow_embedding=False)
    ledger.authorize(input_rate=150,output_rate=1500,embedding_rate=500,token_bound_attested=True,
        console_evidence='2026-11-10 verified official Beijing qwen-flash and text-embedding-v4 pricing; text-only JSON UTF8 4x plus8192 margin, input<=64000, output<=512, thinking disabled; user cumulative50 CNY development authorization',valid_until=time.time()+3600)
    for name in ('httpx','httpcore'):logging.getLogger(name).setLevel(logging.CRITICAL)
    with TestClient(create_app(cfg)) as client:
        with Session(client.app.state.engine) as s:
            kb=s.scalar(select(KnowledgeBase).where(KnowledgeBase.name=='Bailian-RAG-Live-Test'))
            require(kb is not None,'TEST_KB_MISSING')
            kb_id=kb.id
        result=asyncio.run(validate(client.app.state.agent,kb_id,ledger))
    now=snapshot(path)
    result['history_preserved']=all(all(row in now[t] for row in rows) for t,rows in old.items()) and path.with_suffix('.sqlite.seal').read_bytes()==seal and all(hashlib.sha256(f.read_bytes()).digest()==h for f,h in protected.items())
    result['project_total_requests']=len(now['requests'])
    result['project_known_cost_nano']=sum(row[17] or 0 for row in now['requests'])
    with output.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=True,indent=2))

if __name__=='__main__':
    try:main()
    except BaseException as exc:
        print(json.dumps({'stopped':exc.message if isinstance(exc,Blocked) else type(exc).__name__}))
        raise SystemExit(1)
