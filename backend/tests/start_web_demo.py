"""Explicit loopback launcher; never changes .env or resets historical ledgers."""
import argparse,ast,os,re,sys,sqlite3,socket,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
def parse_options(argv):
    parser=argparse.ArgumentParser(description='Loopback-only guarded web demo; cumulative ledger is never reset.')
    parser.add_argument('--enable-authorized-web-demo',action='store_true',required=True)
    parser.add_argument('--personal-learning',action='store_true',help='Explicit opt-in persistent personal monitoring policy; first price confirmation required.')
    parser.add_argument('--enable-local-writes',action='store_true',help='Allow loopback knowledge-base and document writes; deletes require confirmation.')
    parser.add_argument('--enable-session-management',action='store_true',help='Allow operator-confirmed price reauthorization on loopback; never auto-renew.')
    group=parser.add_mutually_exclusive_group()
    group.add_argument('--verified-output-fix',action='store_true')
    group.add_argument('--session-name',help='Unique explicit session name; reusing a consumed name is refused.')
    args=parser.parse_args(argv)
    if args.session_name and not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',args.session_name):parser.error('session-name requires 1-80 ASCII letters, digits, underscores or hyphens')
    return args

def main():
    args=parse_options(sys.argv[1:])
    tree=ast.parse((ROOT/'app/core/config.py').read_text(encoding='utf-8'))
    cls=next(x for x in tree.body if isinstance(x,ast.ClassDef) and x.name=='Settings')
    for n in cls.body:
        if isinstance(n,ast.AnnAssign):os.environ.pop(n.target.id.upper(),None)
    from app.core.config import Settings
    from app.core.live_test import Ledger
    path=ROOT/'test-results/bailian-first-live/budget.sqlite'
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as c:parent=c.execute('SELECT run_id FROM requests ORDER BY seq DESC LIMIT 1').fetchone()[0]
    cfg=Settings(_env_file=ROOT/'.env',live_test_mode=True,llm_max_tokens=2048,agent_max_rounds=2,agent_max_tool_calls=1,llm_timeout_seconds=40,agent_timeout_seconds=120,
        cors_origins=['http://127.0.0.1:5189'])
    if cfg.database_url!='sqlite:///'+(ROOT/'studyagent-bailian-20261109.db').as_posix() or cfg.data_dir.resolve()!=(ROOT/'data/bailian-real-20261109').resolve() or cfg.enable_reranker:raise RuntimeError('ISOLATION_REQUIRED')
    # Own the listening socket before touching session state; duplicate launches fail safely.
    listener=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    listener.bind(('127.0.0.1',8010));listener.listen(128)
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as c:
        selected=c.execute("SELECT r.id FROM run r JOIN development_sessions d ON d.run_id=r.id ORDER BY r.rowid DESC LIMIT 1").fetchone()
    ledger=Ledger(path,selected[0]) if selected else Ledger(path,parent).begin_development_session(grant_id='local-start-'+str(uuid.uuid4()),allowed_tools=['calculator','search_documents','summarize_document','compare_documents','generate_quiz'],allow_embedding=True)
    # Startup is not a price confirmation. Expired/COMPLETE/HALTED stay unchanged.
    cfg=cfg.model_copy(update={'personal_learning':args.personal_learning,'web_demo_enabled':True,'web_demo_allow_writes':args.enable_local_writes,'web_demo_allow_session_management':args.enable_session_management,'web_demo_round_id':ledger.run_id,
        'max_upload_bytes':2*1024*1024,'max_text_chars':120000,'max_chunks':128,'chunk_size':640,'chunk_overlap':80,'embedding_batch_size':8,
        'paragraph_aware_chunks':True,'chunk_max_utf8_bytes':1984})
    cfg=cfg.model_copy(update={'memory_window_turns':4,'memory_max_context_chars':2500,'memory_summary_max_chars':600,'memory_summary_input_chars':1800})
    if args.personal_learning:
        from app.core.personal_learning import ensure
        from app.core.live_test import Blocked
        try:
            ledger=ensure(ledger,cfg);cfg.web_demo_round_id=ledger.run_id
        except Blocked:pass  # Local UI remains available; status/next call explains refusal.
    from app.main import create_app
    import uvicorn
    try:uvicorn.Server(uvicorn.Config(create_app(cfg),host='127.0.0.1',port=8010,log_level='warning',access_log=False,proxy_headers=False)).run(sockets=[listener])
    finally:
        listener.close()
        active=Ledger(path,cfg.web_demo_round_id)
        if active.report()['state']=='READY':active.complete_development_session()
if __name__=='__main__':main()
