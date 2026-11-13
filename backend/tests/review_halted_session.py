"""Read-only by default. Explicit human approval creates an UNAUTHORIZED session only."""
import argparse,json,socket,sqlite3,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.core.live_test import Ledger,Blocked
from app.core.development_budget import reviewed_replacement_candidate,create_reviewed_replacement

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ledger',type=Path,default=ROOT/'test-results/bailian-first-live/budget.sqlite')
    p.add_argument('--run-id')
    p.add_argument('--create-unauthorized-session',action='store_true')
    p.add_argument('--acknowledge-unknown-halt',action='store_true')
    p.add_argument('--grant-id')
    p.add_argument('--personal-learning',action='store_true',help='Review settled citation application failure for ordinary personal mode; no paid authorization.')
    p.add_argument('--acknowledge-application-fix',action='store_true',help='Explicit approval that citation/Stage fixes and personal-mode scope have been reviewed.')
    args=p.parse_args();path=args.ledger.resolve()
    cfg=None
    if args.personal_learning:
        import ast,os
        tree=ast.parse((ROOT/'app/core/config.py').read_text(encoding='utf8'))
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Settings')
        for node in cls.body:
            if isinstance(node,ast.AnnAssign):os.environ.pop(node.target.id.upper(),None)
        from app.core.config import Settings
        cfg=Settings(_env_file=ROOT/'.env',personal_learning=True,live_test_mode=True)
        if path.resolve()!=cfg.live_test_ledger.resolve():raise Blocked('LEDGER_CONFIGURATION_MISMATCH')
    # Pure inspection never loads Settings/.env, connects to a provider or writes a table.
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as c:
        c.row_factory=sqlite3.Row
        c.execute('BEGIN')
        if path.with_suffix('.sqlite.seal').read_text()!=c.execute('SELECT id FROM project').fetchone()[0]:raise Blocked('LEDGER_IDENTITY_MISMATCH')
        row=c.execute('SELECT * FROM run WHERE id=?',(args.run_id,)).fetchone() if args.run_id else c.execute('SELECT * FROM run ORDER BY rowid DESC LIMIT 1').fetchone()
        if row is None:raise Blocked('LEDGER_IDENTITY_MISMATCH')
        expected=row['id']
        if cfg:
            from app.core.personal_learning import review_settled_application_halt
            report=review_settled_application_halt(c,row,expected,cfg)
        else:report=reviewed_replacement_candidate(c,row,expected)
    print(json.dumps({'run_id':expected,**report},ensure_ascii=False))
    if not args.create_unauthorized_session:return
    acknowledged=args.acknowledge_application_fix if cfg else args.acknowledge_unknown_halt
    if not args.run_id or not args.grant_id or not acknowledged:p.error('creation requires explicit run-id, unique grant-id and the matching independent review acknowledgement')
    # Prevent replacing a session while its web process is still serving; no outbound connection.
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1',8010))
        new=create_reviewed_replacement(Ledger(path,expected),expected,grant_id=args.grant_id,reviewed=True,
                                       personal_settings=cfg,application_fix_reviewed=args.acknowledge_application_fix)
    print(json.dumps({'new_session_id':new.run_id,'authorized':False,'old_halted_preserved':True,'next':'Start web server and manually reconfirm current prices; no model request was sent.'}))

if __name__=='__main__':
    try:main()
    except Blocked as e:print(json.dumps({'blocked':e.message}));sys.exit(2)
    except OSError:print('LOCAL_SERVICE_OR_LEDGER_UNAVAILABLE: stop the confirmed local StudyAgent backend before creation; no automatic cleanup.');sys.exit(2)
