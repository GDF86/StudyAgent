"""One explicitly authorized Embedding diagnostic. No retry and no business DB access."""
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

BACKEND=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BACKEND))
GRANT='20261109-user-confirmed-one-embedding-diagnostic-v1'

def main():
    if sys.argv[1:]!=['--execute-confirmed-one-request']:
        raise RuntimeError('EXPLICIT_AUTHORIZATION_FLAG_REQUIRED')
    from app.core.live_test import Ledger,Stage,Blocked
    tree=ast.parse((BACKEND/'app/core/config.py').read_text(encoding='utf-8'))
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Settings')
    for node in cls.body:
        if isinstance(node,ast.AnnAssign):os.environ.pop(node.target.id.upper(),None)
    from app.core.config import Settings
    from app.rag.embeddings import CompatibleEmbeddingProvider
    path=BACKEND/'test-results/bailian-first-live/budget.sqlite'
    # Never initialize or replace this historical ledger.
    parent=Ledger(path)
    original=parent.report()
    if original['state']!='HALTED' or original['used']!=1:
        raise RuntimeError('HISTORICAL_STATE_UNEXPECTED')
    protected=[path.with_suffix('.sqlite.seal'),path.parent/'first-round-result.json',path.parent/'first-round-summary.json']
    hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as c:
        history_run=c.execute('SELECT * FROM run WHERE id=?',(parent.run_id,)).fetchone()
        history_requests=c.execute('SELECT * FROM requests WHERE run_id=? ORDER BY seq',(parent.run_id,)).fetchall()
    cfg=Settings(_env_file=BACKEND/'.env',live_test_mode=True,live_test_ledger=path)
    parsed=urlsplit(cfg.embedding_base_url)
    if not (cfg.embedding_model=='text-embedding-v4' and not cfg.enable_reranker and parsed.scheme=='https'
            and parsed.hostname and parsed.hostname.endswith('.cn-beijing.maas.aliyuncs.com')
            and parsed.path.rstrip('/')=='/compatible-mode/v1' and not parsed.query and not parsed.fragment
            and not parsed.username and not parsed.password):raise RuntimeError('CONFIGURATION_PRECHECK_FAILED')
    for name in ('httpx','httpcore'):logging.getLogger(name).setLevel(logging.CRITICAL)
    diagnostic=parent.begin_diagnostic(grant_id=GRANT,console_permissions_confirmed=True)
    diagnostic.authorize(input_rate=150,output_rate=1500,embedding_rate=500,
        console_evidence='https://help.aliyun.com/zh/model-studio/embedding-interfaces-compatible-with-openai ; user confirmed workspace, model permission and pay-as-you-go',
        token_bound_attested=True,valid_until=time.time()+3600)
    result={'status':'NOT_EXECUTED','historical_requests':original['used'],'new_requests_limit':1,
            'console_prerequisites_user_confirmed':True,'embedding_dimensions':None}
    try:
        with Stage(diagnostic,'ingestion',offline=False) as scope:
            vectors=CompatibleEmbeddingProvider(cfg).embed_texts(['StudyAgent 单次向量接口诊断。'])
            valid=len(vectors)==1 and len(vectors[0])>0
            scope.verify(valid)
            result['embedding_dimensions']=len(vectors[0])
        result['status']='PASS'
    except BaseException as exc:
        result['status']='FAILED'
        result['error_type']=exc.code if isinstance(exc,Blocked) else type(exc).__name__
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as c:
        intact=(history_run==c.execute('SELECT * FROM run WHERE id=?',(parent.run_id,)).fetchone()
                and history_requests==c.execute('SELECT * FROM requests WHERE run_id=? ORDER BY seq',(parent.run_id,)).fetchall())
    result['original_history_unchanged']=intact and all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in hashes.items())
    result['ledger']=diagnostic.report()
    result['project_total_requests']=original['used']+result['ledger']['used']
    with (path.parent/'embedding-diagnostic-20261109.json').open('x',encoding='utf-8') as f:
        json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':
    try:main()
    except BaseException as exc:
        print(json.dumps({'stopped':type(exc).__name__}))
        raise SystemExit(1)
