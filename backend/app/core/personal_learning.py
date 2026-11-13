"""Explicit v2 policy: remembered human prices, monitoring rather than a money cap.
No credentials, no provider calls, no changes to historic run/request rows or seal.
"""
import hashlib,json,time,uuid
from datetime import datetime,timezone,timedelta
from urllib.parse import urlsplit

VERSION='personal-monitor-v2'

def is_personal(c,ident):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE name='personal_runs'").fetchone() and
                c.execute('SELECT 1 FROM personal_runs WHERE run_id=?',(ident,)).fetchone())

def fingerprint(settings):
    from app.core.live_test import Blocked
    urls=[settings.llm_base_url,settings.embedding_base_url]
    for url in urls:
        p=urlsplit(url)
        if p.scheme!='https' or not p.hostname or not p.hostname.endswith('.cn-beijing.maas.aliyuncs.com') or p.username or p.password or p.query or p.fragment:
            raise Blocked('PERSONAL_BEIJING_CONFIGURATION_REQUIRED')
    if settings.llm_model!='qwen-flash' or settings.embedding_model!='text-embedding-v4' or settings.enable_reranker:
        raise Blocked('PERSONAL_MODEL_RECONFIRMATION_REQUIRED')
    return hashlib.sha256(json.dumps([*urls,settings.llm_model,settings.embedding_model,'beijing','<=128K'],sort_keys=True).encode()).hexdigest()

def schema(c):
    c.execute('CREATE TABLE IF NOT EXISTS personal_prices (id TEXT PRIMARY KEY,version TEXT,binding TEXT,confirmed REAL,input_rate INTEGER,output_rate INTEGER,embedding_rate INTEGER,thresholds TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS personal_runs (run_id TEXT PRIMARY KEY,price_id TEXT NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS personal_alerts (price_id TEXT,threshold INTEGER,created REAL,PRIMARY KEY(price_id,threshold))')

def saved(c):
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='personal_prices'").fetchone():return None
    return c.execute('SELECT * FROM personal_prices ORDER BY rowid DESC LIMIT 1').fetchone()


def review_settled_application_halt(c, old, expected, settings):
    """Read-only reuse of existing history checks for a reviewed personal restart."""
    from app.core.live_test import Blocked
    from app.core.development_budget import _validate_history, risk
    latest = c.execute('SELECT id FROM run ORDER BY rowid DESC LIMIT 1').fetchone()
    if (not settings.personal_learning or not old or old['id'] != expected
            or latest[0] != expected or old['state'] != 'HALTED' or not is_personal(c, expected)):
        raise Blocked('REVIEW_REQUIRES_HALTED_DEVELOPMENT_SESSION')
    if c.execute("SELECT 1 FROM run WHERE state='READY'").fetchone():raise Blocked('ACTIVE_SESSION_EXISTS')
    if c.execute("SELECT 1 FROM requests WHERE status='PENDING'").fetchone():raise Blocked('PENDING_REQUEST_REQUIRES_RECONCILIATION')
    occupied = risk(c)
    _validate_history(c, monitor=True)
    profile = saved(c)
    if not profile or profile['version'] != VERSION or profile['binding'] != fingerprint(settings):
        raise Blocked('PERSONAL_PRICE_CONFIRMATION_REQUIRED')
    rows = c.execute('SELECT * FROM requests WHERE run_id=? ORDER BY seq', (expected,)).fetchall()
    if not rows or any(q['status'] != 'SUCCESS' or q['usage_status'] != 'KNOWN'
            or not all(type(q[k]) is int and q[k]>=0 for k in ('input_tokens','output_tokens','input_bound','output_bound','cost_nano','reserved_nano'))
            or q['cost_nano'] is None or q['cost_nano'] > q['reserved_nano']
            or q['input_tokens'] > q['input_bound'] or q['output_tokens'] > q['output_bound'] for q in rows):
        raise Blocked('CURRENT_REQUESTS_NOT_FULLY_SETTLED')
    events = c.execute('SELECT kind,reason FROM runtime_events WHERE run_id=?', (expected,)).fetchall()
    if ({e['reason'] for e in events if e['kind'] == 'APPLICATION_FAILURE'} != {'LLM_INVALID_CITATION'}
            or {e['reason'] for e in events if e['kind'] == 'SAFETY_HALT'} != {'UNSPECIFIED_HALT'}):
        raise Blocked('KNOWN_HALT_REQUIRES_SPECIFIC_REPAIR')
    return {'technical_checks_passed': True, 'historical_cause': 'LLM_INVALID_CITATION',
            'requires_manual_approval': True, 'project_risk_yuan': occupied / 1e9,
            'current_requests': len(rows), 'last_seq': rows[-1]['seq']}

def check_daily(c,ledger,attempts=1):
    from app.core.live_test import Blocked
    # Beijing calendar day; persistent across processes and automatic session rollover.
    tz=timezone(timedelta(hours=8));now=datetime.now(tz)
    start=now.replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
    count=c.execute('SELECT count(*) FROM requests WHERE started>=?',(start,)).fetchone()[0]
    if count+attempts>200:ledger.deny(c,'PERSONAL_DAILY_REQUEST_LIMIT')

def alerts(c,run_id,occupied):
    p=c.execute('SELECT p.* FROM personal_prices p JOIN personal_runs r ON r.price_id=p.id WHERE r.run_id=?',(run_id,)).fetchone()
    for amount in json.loads(p['thresholds']):
        if occupied>=amount*1_000_000_000:
            c.execute('INSERT OR IGNORE INTO personal_alerts VALUES (?,?,?)',(p['id'],amount,time.time()))

def ensure(ledger,settings,confirmation=None):
    """One transaction handles reuse/rollover and (only when explicit) price migration."""
    from app.core.live_test import Ledger,Blocked
    from app.core.development_budget import policy,_begin_locked
    binding=fingerprint(settings)
    with ledger.transaction() as (c,old):
        # A stale process adopts the latest safe session, never revives a halted one.
        latest=c.execute('SELECT * FROM run ORDER BY rowid DESC LIMIT 1').fetchone()
        if latest['state']=='HALTED' or old['state']=='HALTED':raise Blocked('HALTED_REQUIRES_REVIEW')
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='calculator_recovery_receipts'").fetchone() and c.execute('SELECT 1 FROM calculator_recovery_receipts WHERE new_run=?',(latest['id'],)).fetchone():
            if not latest['authorized']:raise Blocked('INDEPENDENT_CALCULATOR_AUTHORIZATION_REQUIRED')
            profile=saved(c);p=policy(c,latest['id'])
            if latest['state']!='READY' or not p or time.time()>=p['expires'] or time.time()>=latest['verified_until']:raise Blocked('FINITE_CALCULATOR_GRANT_EXPIRED')
            if not profile or profile['binding']!=binding:raise Blocked('PERSONAL_PRICE_CONFIRMATION_REQUIRED')
            if c.execute("SELECT 1 FROM requests WHERE status='PENDING'").fetchone():raise Blocked('PENDING_REQUEST_REQUIRES_RECONCILIATION')
            return Ledger(ledger.path,latest['id'])  # Never auto-roll a finite incident retest grant.
        if c.execute("SELECT 1 FROM requests WHERE status='PENDING'").fetchone():raise Blocked('PENDING_REQUEST_REQUIRES_RECONCILIATION')
        reviewed_personal = (is_personal(c, latest['id']) and c.execute(
            "SELECT 1 FROM runtime_events WHERE run_id=? AND kind='MANUAL_REVIEW_NEW_SESSION' AND reason='LLM_INVALID_CITATION'",
            (latest['id'],)).fetchone()) if c.execute("SELECT 1 FROM sqlite_master WHERE name='runtime_events'").fetchone() else False
        if reviewed_personal and not latest['authorized']:
            if confirmation is None:raise Blocked('EXPLICIT_RUN_AUTHORIZATION_REQUIRED')
            if c.execute('SELECT 1 FROM requests WHERE run_id=?', (latest['id'],)).fetchone():raise Blocked('EXPLICIT_RUN_AUTHORIZATION_REQUIRED')
        profile=saved(c)
        if confirmation is not None:
            if confirmation.get('expected',old['id'])!=old['id'] or latest['id']!=old['id']:raise Blocked('SESSION_VERSION_CONFLICT')
            if confirmation.get('confirmed') is not True:raise Blocked('CURRENT_PRICE_CONFIRMATION_REQUIRED')
            rates=[confirmation.get(k) for k in ('input_rate','output_rate','embedding_rate')]
            if not all(type(x) is int and 0<x<=10_000_000 for x in rates):raise Blocked('PRICE_OR_TOKEN_BOUND_UNVERIFIED')
            thresholds=sorted(set([30,50,*settings.personal_alert_thresholds]))
            if any(type(x) is not int or not 1<=x<=1000000 for x in thresholds):raise Blocked('INVALID_ALERT_THRESHOLDS')
            schema(c)
            ident=str(uuid.uuid4())
            c.execute('INSERT INTO personal_prices VALUES (?,?,?,?,?,?,?,?)',(ident,VERSION,binding,time.time(),*rates,json.dumps(thresholds)))
            profile=saved(c)
        if profile is None or profile['version']!=VERSION or profile['binding']!=binding:raise Blocked('PERSONAL_PRICE_CONFIRMATION_REQUIRED')
        thresholds=sorted(set([30,50,*settings.personal_alert_thresholds]))
        if any(type(x) is not int or not 1<=x<=1000000 for x in thresholds):raise Blocked('INVALID_ALERT_THRESHOLDS')
        if json.loads(profile['thresholds'])!=thresholds:
            c.execute('INSERT INTO personal_prices VALUES (?,?,?,?,?,?,?,?)',(str(uuid.uuid4()),VERSION,binding,profile['confirmed'],profile['input_rate'],profile['output_rate'],profile['embedding_rate'],json.dumps(thresholds)))
            profile=saved(c)
        p=policy(c,latest['id'])
        if not p:raise Blocked('EXPLICIT_RUN_AUTHORIZATION_REQUIRED')
        if reviewed_personal and not latest['authorized']:
            if latest['state']!='READY' or time.time()>=p['expires']:raise Blocked('SESSION_OR_PRICE_EXPIRED')
            c.execute('UPDATE personal_runs SET price_id=? WHERE run_id=?',(profile['id'],latest['id']))
            c.execute('UPDATE run SET authorized=1,input_rate=?,output_rate=?,embedding_rate=?,verified_until=?,evidence_hash=? WHERE id=?',
                      (profile['input_rate'],profile['output_rate'],profile['embedding_rate'],time.time()+3600,binding,latest['id']))
            return Ledger(ledger.path,latest['id'])
        used=c.execute('SELECT count(*) FROM requests WHERE run_id=?',(latest['id'],)).fetchone()[0]
        linked=c.execute('SELECT price_id FROM personal_runs WHERE run_id=?',(latest['id'],)).fetchone()
        if latest['state']=='READY' and linked and linked[0]==profile['id'] and used<64 and time.time()<p['expires']:
            return Ledger(ledger.path,latest['id'])
        # Do not change an old development READY authorization. Operator must end it normally.
        if latest['state']=='READY' and not linked:raise Blocked('END_DEVELOPMENT_SESSION_BEFORE_MIGRATION')
        if latest['state']=='READY':c.execute("UPDATE run SET state='COMPLETE' WHERE id=?",(latest['id'],))
        new=_begin_locked(ledger,c,latest,'personal-'+str(uuid.uuid4()),set(json.loads(p['tools'])),bool(p['embedding']),100,monitor=True)
        c.execute('INSERT INTO personal_runs VALUES (?,?)',(new,profile['id']))
        c.execute('UPDATE run SET authorized=1,input_rate=?,output_rate=?,embedding_rate=?,verified_until=?,evidence_hash=? WHERE id=?',
                  (profile['input_rate'],profile['output_rate'],profile['embedding_rate'],time.time()+3600,binding,new))
        return Ledger(ledger.path,new)
