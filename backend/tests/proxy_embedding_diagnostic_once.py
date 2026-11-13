"""One authorized proxy Embedding request; no business API, no retry, no reset."""
import ast
import hashlib
import inspect
import json
import logging
import math
import os
from pathlib import Path
import sqlite3
import sys
import time
from urllib.parse import urlsplit

BACKEND=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BACKEND))
PROXY="http://127.0.0.1:7897"
GRANT="20261109-authorized-one-proxy-embedding-7897-v1"
TEXT="StudyAgent embedding connectivity test."

def require(condition,reason):
    if not condition:raise RuntimeError(reason)

def main():
    require(sys.argv[1:]==['--execute-authorized-one-proxy-request'],'EXPLICIT_FLAG_REQUIRED')
    import httpx
    from app.core.live_test import Ledger,Stage,Blocked
    from app.core.model_proxy import explicit_proxy,transport,ProxyObservation
    # Clear this child process' overrides by name only. Never print any setting values.
    tree=ast.parse((BACKEND/'app/core/config.py').read_text(encoding='utf-8'))
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Settings')
    for node in cls.body:
        if isinstance(node,ast.AnnAssign):os.environ.pop(node.target.id.upper(),None)
    from app.core.config import Settings
    from app.rag.embeddings import CompatibleEmbeddingProvider
    folder=BACKEND/'test-results/bailian-first-live';path=folder/'budget.sqlite'
    output=folder/'proxy-embedding-diagnostic-20261109.json'
    require(not output.exists(),'DIAGNOSTIC_REPORT_ALREADY_EXISTS')
    cfg=Settings(_env_file=BACKEND/'.env',live_test_mode=True,live_test_ledger=path,model_http_proxy=PROXY)
    require(httpx.__version__=='0.28.1' and 'proxy' in inspect.signature(httpx.HTTPTransport).parameters,'HTTPX_PRECHECK_FAILED')
    parsed=urlsplit(cfg.embedding_base_url)
    require(cfg.embedding_model=='text-embedding-v4' and bool(cfg.embedding_api_key) and not cfg.enable_reranker,'MODEL_PRECHECK_FAILED')
    require(parsed.scheme=='https' and parsed.hostname and parsed.hostname.endswith('.cn-beijing.maas.aliyuncs.com')
            and parsed.path.rstrip('/')=='/compatible-mode/v1' and not parsed.query and not parsed.fragment
            and not parsed.username and not parsed.password,'BASE_URL_PRECHECK_FAILED')
    require(explicit_proxy(cfg,'embedding')==PROXY,'PROXY_PRECHECK_FAILED')
    check=transport(cfg,'embedding')
    try:
        proxy_url=check._pool._proxy_url
        require(proxy_url.host==b'127.0.0.1' and proxy_url.port==7897 and proxy_url.scheme==b'http','PROXY_TRANSPORT_NOT_EXPLICIT')
    finally:check.close()
    protected={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir() if p.is_file() and p.suffix in ('.seal','.json')}
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as c:
        old_runs=c.execute('SELECT * FROM run ORDER BY id').fetchall()
        old_requests=c.execute('SELECT * FROM requests ORDER BY seq').fetchall()
        old_errors=c.execute('SELECT * FROM response_errors ORDER BY seq').fetchall()
        require(len(old_requests)==2 and all(row[3]=='embedding' or row[2]=='embedding' for row in old_requests),'HISTORICAL_COUNT_UNEXPECTED')
        require(c.execute("SELECT count(*) FROM run WHERE state='READY'").fetchone()[0]==0,'ANOTHER_ACTIVE_ROUND')
        require(c.execute("SELECT count(*) FROM requests WHERE status='PENDING'").fetchone()[0]==0,'ANOTHER_PENDING_REQUEST')
        reserved=c.execute('SELECT sum(reserved_nano) FROM requests').fetchone()[0]
        cap=c.execute('SELECT budget_nano FROM project').fetchone()[0]
        require(reserved==8192000 and reserved+4096000<=cap,'PROJECT_BUDGET_PRECHECK_FAILED')
        parent_id=c.execute('SELECT run_id FROM requests ORDER BY seq DESC LIMIT 1').fetchone()[0]
    parent=Ledger(path,parent_id)
    require(parent.report()['state']=='HALTED','PARENT_NOT_HALTED')
    # Existing legal grant creation appends a 1-request / 1-CNY round; no old row is updated.
    diagnostic=parent.begin_diagnostic(grant_id=GRANT,console_permissions_confirmed=True)
    diagnostic.authorize(input_rate=150,output_rate=1500,embedding_rate=500,
       console_evidence='Official Beijing Embedding pricing 0.50 CNY/million; user authorized exactly one proxy request',
       token_bound_attested=True,valid_until=time.time()+3600)
    for name in ('httpx','httpcore'):logging.getLogger(name).setLevel(logging.CRITICAL)
    result={'status':'NOT_EXECUTED','historical_requests':2,'proxy':PROXY,'embedding_dimensions':None,
            'vectors_valid':False,'sensitive_payload_persisted':False}
    with ProxyObservation(PROXY) as observer:
        try:
            with Stage(diagnostic,'ingestion',offline=False) as scope:
                vectors=CompatibleEmbeddingProvider(cfg).embed_texts([TEXT])
                valid=len(vectors)==1 and bool(vectors[0]) and all(isinstance(x,float) and math.isfinite(x) for x in vectors[0]) and any(vectors[0])
                result['vectors_valid']=valid;result['embedding_dimensions']=len(vectors[0]) if vectors else None
                scope.verify(valid and observer.report()['proxy_path_confirmed'] and observer.report()['http_status']==200)
            result['status']='PASS'
        except BaseException as exc:
            result['status']='FAILED'
            result['error_type']=exc.code if isinstance(exc,Blocked) else type(exc).__name__
    result['network']=observer.report()
    result['ledger']=diagnostic.report()
    result['project_total_requests']=2+result['ledger']['used']
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as c:
        intact=(old_runs==c.execute('SELECT * FROM run WHERE id!=? ORDER BY id',(diagnostic.run_id,)).fetchall()
                and old_requests==c.execute('SELECT * FROM requests WHERE seq<=2 ORDER BY seq').fetchall()
                and old_errors==c.execute('SELECT * FROM response_errors WHERE seq<=2 ORDER BY seq').fetchall())
    result['history_preserved']=intact and all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in protected.items())
    with output.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':
    try:main()
    except BaseException as exc:
        # Do not print raw exception messages: they can contain URLs or credentials.
        print(json.dumps({'stopped':type(exc).__name__}))
        raise SystemExit(1)
