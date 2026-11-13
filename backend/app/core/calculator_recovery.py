"""Incident-specific, explicitly approved recovery. No provider calls."""
import hashlib,json,time,uuid
from pathlib import Path
from app.core.live_test import Ledger,Blocked
from app.core.development_budget import _begin_locked,policy,_validate_history
from app.core.personal_learning import saved,fingerprint

TARGET='8e4bb9a4-075c-486a-b843-71dc47ff8393'
ROOT=Path(__file__).resolve().parents[2]
FILES=['app/agent/agent.py','app/agent/calculator.py','app/agent/long_summary.py','app/agent/tools.py','app/core/live_test.py','app/core/web_demo.py','app/core/personal_learning.py','app/core/calculator_recovery.py','app/core/development_budget.py','app/core/runtime_safety.py','tests/calculator_retest_guard.py','tests/calculator_retest_once.py','tests/calculator_recovery_entry.py']
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=True).encode()).hexdigest()
def code_hash():return digest({f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in FILES})
def db_hash(c,skip_receipts=False):
    return digest({t:[list(r) for r in c.execute('SELECT * FROM "'+t+'" ORDER BY rowid')] for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'") if not (skip_receipts and t=="calculator_recovery_receipts")})
def seal_hash(path):return hashlib.sha256(Path(path).with_suffix(Path(path).suffix+'.seal').read_bytes()).hexdigest()
def checks(c,target,new_run=None):
    if target!=TARGET:raise Blocked('WRONG_RECOVERY_INCIDENT')
    r=c.execute('SELECT * FROM run WHERE id=?',(target,)).fetchone()
    if not r or r['state']!='HALTED':raise Blocked('EXPECTED_HALTED')
    if c.execute("SELECT 1 FROM run WHERE state='READY' AND id<>?",(new_run or target,)).fetchone() or c.execute("SELECT 1 FROM requests WHERE status='PENDING'").fetchone():raise Blocked('ACTIVE_OR_PENDING_REQUEST')
    from app.core.development_budget import risk
    risk(c)  # Validate bounded reservations even in personal monitoring mode.
    _validate_history(c,monitor=True)
    rows=c.execute('SELECT * FROM requests WHERE run_id=?',(target,)).fetchall()
    if len(rows)!=18 or any(q['status']!='SUCCESS' or q['usage_status']!='KNOWN' or q['cost_nano'] is None or q['cost_nano']>q['reserved_nano'] or q['input_tokens']>q['input_bound'] or q['output_tokens']>q['output_bound'] for q in rows):raise Blocked('INCIDENT_REQUESTS_CHANGED')
    if c.execute("SELECT count(*) FROM requests WHERE usage_status='UNKNOWN'").fetchone()[0]!=3:raise Blocked('HISTORICAL_UNKNOWN_CHANGED')
    reasons={q[0] for q in c.execute("SELECT reason FROM runtime_events WHERE run_id=? AND kind='SAFETY_HALT'",(target,))}
    if 'AGENT_FAILED' not in reasons or not reasons<={'AGENT_FAILED','UNSPECIFIED_HALT'}:raise Blocked('INCIDENT_REASON_CHANGED')
    if not saved(c):raise Blocked('PERSONAL_PRICES_REQUIRED')
    return r

def prepare(path,target):
    import sqlite3
    path=Path(path).resolve()
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as c:
        c.row_factory=sqlite3.Row
        if path.with_suffix(Path(path).suffix+'.seal').read_text()!=c.execute('SELECT id FROM project').fetchone()[0]:raise Blocked('SEAL_IDENTITY_MISMATCH')
        c.execute('BEGIN')
        checks(c,target)
        return {'version':'calculator-incident-v1','ticket_id':str(uuid.uuid4()),'target':target,'database_hash':db_hash(c),'seal_hash':seal_hash(path),'code_hash':code_hash(),'created':time.time()}

def create(path,ticket,approved=False):
    if approved is not True:raise Blocked('EXPLICIT_RECOVERY_APPROVAL_REQUIRED')
    if ticket.get('version')!='calculator-incident-v1' or ticket.get('code_hash')!=code_hash():raise Blocked('REPAIR_CODE_CHANGED')
    ledger=Ledger(path,ticket.get('target'))
    with ledger.transaction() as (c,r):
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='calculator_recovery_receipts'").fetchone():
            if c.execute('SELECT 1 FROM calculator_recovery_receipts WHERE ticket_id=?',(ticket['ticket_id'],)).fetchone():raise Blocked('RECOVERY_TICKET_ALREADY_USED')
        checks(c,ticket['target'])
        if db_hash(c)!=ticket['database_hash'] or seal_hash(path)!=ticket['seal_hash']:raise Blocked('RECOVERY_EVIDENCE_CHANGED')
        profile=saved(c)
        ident=_begin_locked(ledger,c,r,'calculator-review-'+ticket['ticket_id'],{'calculator'},False,2,monitor=True)
        c.execute('INSERT INTO personal_runs VALUES (?,?)',(ident,profile['id']))
        c.execute('CREATE TABLE IF NOT EXISTS calculator_recovery_receipts (ticket_id TEXT PRIMARY KEY,old_run TEXT,new_run TEXT UNIQUE,evidence TEXT,created REAL,authorized REAL,snapshot TEXT)')
        c.execute('INSERT INTO calculator_recovery_receipts VALUES (?,?,?,?,?,NULL,?)',(ticket['ticket_id'],ledger.run_id,ident,digest(ticket),time.time(),db_hash(c,True)))
    return Ledger(path,ident)

def authorize(ledger,ticket,settings,approved=False):
    if approved is not True or not settings.personal_learning or ticket.get('code_hash')!=code_hash():raise Blocked('EXPLICIT_AUTHORIZATION_REQUIRED')
    with ledger.transaction() as (c,r):
        receipt=c.execute('SELECT * FROM calculator_recovery_receipts WHERE ticket_id=? AND new_run=?',(ticket['ticket_id'],ledger.run_id)).fetchone()
        if not receipt or receipt['evidence']!=digest(ticket) or receipt['authorized'] is not None:raise Blocked('INVALID_OR_USED_RECOVERY_RECEIPT')
        if db_hash(c,True)!=receipt['snapshot']:raise Blocked('RECOVERY_EVIDENCE_CHANGED')
        checks(c,ticket['target'],ledger.run_id)
        p=policy(c,ledger.run_id)
        if not p or json.loads(p['tools'])!=['calculator'] or p['embedding'] or p['max_requests']!=2 or time.time()>=p['expires']:raise Blocked('FINITE_CALCULATOR_POLICY_REQUIRED')
        if r['state']!='READY' or r['authorized'] or c.execute('SELECT 1 FROM requests WHERE run_id=?',(ledger.run_id,)).fetchone():raise Blocked('NEW_RUN_NOT_EMPTY_UNAUTHORIZED')
        if c.execute("SELECT 1 FROM requests WHERE status='PENDING'").fetchone() or c.execute("SELECT 1 FROM run WHERE state='READY' AND id<>?",(ledger.run_id,)).fetchone():raise Blocked('ACTIVE_OR_PENDING_REQUEST')
        if seal_hash(ledger.path)!=ticket['seal_hash']:raise Blocked('SEAL_CHANGED')
        profile=c.execute('SELECT p.* FROM personal_prices p JOIN personal_runs s ON s.price_id=p.id WHERE s.run_id=?',(ledger.run_id,)).fetchone()
        if profile['binding']!=fingerprint(settings) or (profile['input_rate'],profile['output_rate'],profile['embedding_rate'])!=(150,1500,500):raise Blocked('PRICE_OR_MODEL_REAPPROVAL_REQUIRED')
        c.execute('UPDATE run SET authorized=1,input_rate=?,output_rate=?,embedding_rate=?,verified_until=?,evidence_hash=? WHERE id=?',(150,1500,500,time.time()+3600,receipt['evidence'],ledger.run_id))
        c.execute('UPDATE calculator_recovery_receipts SET authorized=? WHERE ticket_id=?',(time.time(),ticket['ticket_id']))
