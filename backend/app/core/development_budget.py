"""Explicit development sessions; append-only policy extension to the sealed ledger."""
import hashlib
import json
import logging
import time
import uuid

ALLOWED_TOOLS = {'calculator','search_documents','summarize_document','compare_documents','generate_quiz'}
NETWORK_ERRORS = {'ConnectError','ConnectTimeout','ReadTimeout','WriteTimeout','ReadError','WriteError','RemoteProtocolError'}

def policy(c, run_id):
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='development_sessions' AND type='table'").fetchone():return None
    return c.execute('SELECT * FROM development_sessions WHERE run_id=?',(run_id,)).fetchone()

def risk(c):
    # Legacy rows keep their full reservation. Only new successful, known requests
    # settle to actual usage; failed/unknown/pending requests retain their reserve.
    exists=c.execute("SELECT 1 FROM sqlite_master WHERE name='development_requests' AND type='table'").fetchone()
    rows=c.execute('SELECT * FROM requests').fetchall()
    new={r[0] for r in c.execute('SELECT seq FROM development_requests')} if exists else set()
    total=0
    for r in rows:
        held=r['reserved_nano']
        cost=r['cost_nano']
        if type(held) is not int or held<=0 or (cost is not None and (type(cost) is not int or cost<0)):
            from app.core.live_test import Blocked
            raise Blocked('UNBOUNDED_HISTORICAL_CHARGE')
        total += cost if r['seq'] in new and r['status']=='SUCCESS' and r['usage_status']=='KNOWN' and cost is not None else max(held,cost or 0)
    return total

def begin(ledger, grant_id, allowed_tools, allow_embedding, max_requests=100):
    from app.core.live_test import Ledger,Blocked
    tools=set(allowed_tools)
    if not grant_id or not tools<=ALLOWED_TOOLS or type(max_requests) is not int or not 1<=max_requests<=200:
        raise Blocked('INVALID_DEVELOPMENT_GRANT')
    with ledger.transaction() as (c,r):
        ident=_begin_locked(ledger,c,r,grant_id,tools,allow_embedding,max_requests)
    return Ledger(ledger.path,ident)


def _begin_locked(ledger,c,r,grant_id,tools,allow_embedding,max_requests,monitor=False):
    from app.core.live_test import Blocked
    if c.execute("SELECT count(*) FROM run WHERE state='READY'").fetchone()[0]:raise Blocked('ACTIVE_SESSION_EXISTS')
    if c.execute("SELECT count(*) FROM requests WHERE status='PENDING'").fetchone()[0]:raise Blocked('PENDING_REQUEST_REQUIRES_RECONCILIATION')
    _validate_history(c,monitor=monitor)
    c.execute('CREATE TABLE IF NOT EXISTS development_sessions (run_id TEXT PRIMARY KEY,grant_hash TEXT UNIQUE,tools TEXT,embedding INTEGER,max_requests INTEGER,expires REAL,retry_pending INTEGER)')
    c.execute('CREATE TABLE IF NOT EXISTS development_requests (seq INTEGER PRIMARY KEY,payload_hash TEXT,attempt INTEGER,retry_of INTEGER)')
    c.execute('CREATE TABLE IF NOT EXISTS budget_alerts (threshold_nano INTEGER PRIMARY KEY,created REAL)')
    digest=hashlib.sha256(grant_id.encode()).hexdigest()
    if c.execute('SELECT 1 FROM development_sessions WHERE grant_hash=?',(digest,)).fetchone():raise Blocked('DEVELOPMENT_GRANT_ALREADY_USED')
    ident=str(uuid.uuid4())
    # Compatibility field required by the old schema, deliberately NOT a session cap.
    c.execute("INSERT INTO run VALUES (?,?,0,'READY',0,NULL,NULL,NULL,NULL,NULL,5000000000)",(ident,r['offline']))
    c.execute('INSERT INTO development_sessions VALUES (?,?,?,?,?,?,NULL)',(ident,digest,json.dumps(sorted(tools)),int(allow_embedding),max_requests,time.time()+7200))
    return ident


def _validate_history(c,monitor=False):
    from app.core.live_test import Blocked
    if not monitor and risk(c)>=50_000_000_000:raise Blocked('PROJECT_MONEY_LIMIT')
    # A prior unknown is admissible only if it has a bounded, attested model charge.
    for old in c.execute('SELECT q.*,r.input_rate,r.output_rate,r.embedding_rate FROM requests q JOIN run r ON q.run_id=r.id'):
        dev=policy(c,old['run_id'])
        rate=old['embedding_rate'] if old['kind']=='embedding' else (old['input_rate'] if dev and old['input_bound']<=64000 else max((old['input_rate'] or 0)*8,1200))
        out=(old['output_rate'] if dev else max((old['output_rate'] or 0)*8,12000)) if old['kind']=='llm' else 0
        if old['cost_nano'] is not None and old['cost_nano']>old['reserved_nano']:raise Blocked('HISTORICAL_BOUND_BREACH')
        if old['usage_status']=='UNKNOWN' and (old['status']!='FAILED' or old['model'] not in ('qwen-flash','text-embedding-v4')
                or not rate or not old['input_bound'] or old['reserved_nano']<old['input_bound']*rate+old['output_bound']*out):
            raise Blocked('UNBOUNDED_HISTORICAL_CHARGE')


def renew_web_session(ledger,expected,*,input_rate,output_rate,embedding_rate,confirmed):
    """One atomic state transition; human rechecks prices, never revives HALTED."""
    from app.core.live_test import Ledger,Blocked
    if confirmed is not True or not all(type(x) is int and 0<x<=10_000_000 for x in (input_rate,output_rate,embedding_rate)):
        raise Blocked('CURRENT_PRICE_CONFIRMATION_REQUIRED')
    with ledger.transaction() as (c,r):
        p=policy(c,ledger.run_id);now=time.time()
        if expected!=ledger.run_id:raise Blocked('SESSION_VERSION_CONFLICT')
        if not p or r['state'] not in ('READY','COMPLETE'):raise Blocked('HALTED_REQUIRES_REVIEW')
        if c.execute("SELECT 1 FROM requests WHERE status='PENDING'").fetchone():raise Blocked('PENDING_REQUEST_REQUIRES_RECONCILIATION')
        used=c.execute('SELECT count(*) FROM requests WHERE run_id=?',(ledger.run_id,)).fetchone()[0]
        expired=(now>=p['expires'] or not r['authorized'] or not r['verified_until'] or now>=r['verified_until'] or used>=p['max_requests'])
        if r['state']=='READY' and not expired:raise Blocked('SESSION_STILL_VALID')
        if c.execute("SELECT 1 FROM run WHERE state='READY' AND id<>?",(ledger.run_id,)).fetchone():raise Blocked('SESSION_VERSION_CONFLICT')
        if r['state']=='READY':c.execute("UPDATE run SET state='COMPLETE' WHERE id=?",(ledger.run_id,))
        ident=_begin_locked(ledger,c,r,'web-confirm-'+str(uuid.uuid4()),set(json.loads(p['tools'])),bool(p['embedding']),p['max_requests'])
        evidence=hashlib.sha256(('operator-confirmed-current-Beijing-text-prices:'+str(now)).encode()).hexdigest()
        c.execute('UPDATE run SET authorized=1,input_rate=?,output_rate=?,embedding_rate=?,verified_until=?,evidence_hash=? WHERE id=?',
                  (input_rate,output_rate,embedding_rate,now+3600,evidence,ident))
    return Ledger(ledger.path,ident)


def reserve(ledger,c,r,stage,kind,payload,offline):
    from app.core.live_test import MODELS,STAGES
    p=policy(c,ledger.run_id)
    from app.core.live_test import CURRENT
    scope=CURRENT.get()
    if scope and getattr(scope,'summary_active',False):
        attempts=getattr(scope,'summary_attempts',0)
        if attempts>=24:
            from app.core.errors import DomainError
            raise DomainError(413,'SUMMARY_WORK_LIMIT','总结HTTP尝试达到24次上限')
        scope.summary_attempts=attempts+1
    def deny(reason):ledger.deny(c,reason)
    if r['state']!='READY' or not r['authorized'] or bool(r['offline'])!=offline:deny('RUN_NOT_AUTHORIZED')
    from app.core.personal_learning import is_personal,check_daily,alerts
    personal=is_personal(c,ledger.run_id)
    if not personal and (time.time()>p['expires'] or not r['verified_until'] or time.time()>r['verified_until']):
        # A deadline expiring between middleware and transport is not a safety fault.
        # Still deny before sending; only the trusted scope can classify this case.
        from app.core.live_test import CURRENT,Blocked
        scope=CURRENT.get()
        if scope and scope.ledger.run_id==ledger.run_id:scope.authorization_expired=True
        raise Blocked('SESSION_OR_PRICE_EXPIRED')
    if stage not in STAGES or kind not in MODELS or payload.get('model')!=MODELS[kind]:deny('UNEXPECTED_MODEL_CALL')
    if kind=='embedding' and not p['embedding']:deny('EMBEDDING_NOT_AUTHORIZED')
    count=c.execute('SELECT count(*) FROM requests WHERE run_id=?',(ledger.run_id,)).fetchone()[0]
    if count>=p['max_requests']:deny('SESSION_WORK_LIMIT')
    if c.execute("SELECT count(*) FROM requests WHERE status='PENDING'").fetchone()[0]:deny('PENDING_REQUEST')
    if not all(type(r[k]) is int and r[k]>0 for k in ('input_rate','output_rate','embedding_rate')):deny('PRICE_MISSING')
    allowed={'model','messages','temperature','max_tokens','enable_thinking','stream','stream_options','tools','tool_choice'} if kind=='llm' else {'model','input','encoding_format'}
    if set(payload)-allowed:deny('UNBUDGETED_FEATURE_PARAMETER')
    wire=json.dumps(payload,ensure_ascii=False,sort_keys=True).encode('utf-8')
    if kind=='llm':
        messages=payload.get('messages')
        if not isinstance(messages,list) or not 1<=len(messages)<=32 or any(not isinstance(m,dict) or not isinstance(m.get('content'),(str,type(None))) for m in messages):deny('TEXT_ONLY_MESSAGES_REQUIRED')
        tools=payload.get('tools',[])
        names=[t.get('function',{}).get('name') for t in tools if isinstance(t,dict)] if isinstance(tools,list) else [None]
        if len(names)!=len(tools) or not set(names)<=set(json.loads(p['tools'])):deny('UNAUTHORIZED_TOOL')
        for m in messages:
            for call in m.get('tool_calls',[]) or []:
                if call.get('function',{}).get('name') not in json.loads(p['tools']):deny('UNAUTHORIZED_TOOL')
        ob=payload.get('max_tokens')
        if type(ob) is not int or not 1<=ob<=2048 or payload.get('enable_thinking') is not False:deny('OUTPUT_BOUND_UNVERIFIED')
        # Qwen text byte-BPE: whole JSON bytes exceed plain text bytes; 4x plus
        # 8192 covers chat/template/tool-schema overhead. Not an exact tokenizer.
        ib=len(wire)*4+8192
        if ib>64000:deny('INPUT_TOO_LONG_OR_TIER_UNKNOWN')
        quote=ib*r['input_rate']+ob*r['output_rate']
    else:
        inputs=payload.get('input')
        if not isinstance(inputs,list) or not 1<=len(inputs)<=16 or any(not isinstance(x,str) or not x for x in inputs):deny('BOUNDED_TEXT_BATCH_REQUIRED')
        bounds=[len(x.encode('utf-8'))*4+256 for x in inputs]
        if max(bounds)>8192:deny('EMBEDDING_INPUT_TOO_LONG')
        ib=sum(bounds);ob=0;quote=ib*r['embedding_rate']
    digest=hashlib.sha256(wire).hexdigest()
    retry=p['retry_pending'];attempt=0
    if retry is not None:
        prev=c.execute('SELECT * FROM development_requests WHERE seq=?',(retry,)).fetchone()
        if not prev or prev['attempt']!=0 or prev['payload_hash']!=digest:deny('RETRY_PAYLOAD_MISMATCH')
        attempt=1
    occupied=risk(c)
    if personal:check_daily(c,ledger)
    if not personal and occupied+quote>50_000_000_000:deny('PROJECT_MONEY_LIMIT')
    seq=c.execute('SELECT coalesce(max(seq),0)+1 FROM requests').fetchone()[0]
    c.execute("INSERT INTO requests VALUES (?,?,?,?,?,1,'PENDING',NULL,NULL,?,NULL,?,?,?,NULL,NULL,'UNKNOWN',NULL,?)",
        (seq,stage,kind,MODELS[kind],'chat/completions' if kind=='llm' else 'embeddings',time.time(),ib,ob,quote,ledger.run_id))
    c.execute('INSERT INTO development_requests VALUES (?,?,?,?)',(seq,digest,attempt,retry))
    c.execute('UPDATE development_sessions SET retry_pending=NULL WHERE run_id=?',(ledger.run_id,))
    if personal:alerts(c,ledger.run_id,occupied+quote)
    if occupied+quote>=30_000_000_000:
        c.execute('INSERT OR IGNORE INTO budget_alerts VALUES (30000000000,?)',(time.time(),))
        logging.getLogger('studyagent.budget').warning('PROJECT_BUDGET_ALERT_30_CNY')
    return seq

def preflight_document(settings,texts):
    from app.core.live_test import CURRENT
    from app.core.errors import DomainError
    scope=CURRENT.get()
    if not scope or not getattr(scope,'document_writes',False):return
    bounds=[len(t.encode('utf-8'))*4+256 for t in texts]
    if not bounds or len(bounds)>min(settings.max_chunks,128) or max(bounds)>8192 or settings.embedding_batch_size>16:
        raise DomainError(413,'UPLOAD_MODEL_LIMIT','资料分片超过本机模型限制，请拆分文件后上传')
    with scope.ledger.transaction() as (c,r):
        p=policy(c,scope.ledger.run_id)
        count=c.execute('SELECT count(*) FROM requests WHERE run_id=?',(scope.ledger.run_id,)).fetchone()[0]
        attempts=2*((len(bounds)+settings.embedding_batch_size-1)//settings.embedding_batch_size)
        if count+attempts>p['max_requests']:raise DomainError(413,'UPLOAD_SESSION_LIMIT','当前会话剩余请求不足以处理全部分片及一次受控重试，请启动新会话')
        from app.core.personal_learning import is_personal,check_daily
        if is_personal(c,scope.ledger.run_id):check_daily(c,scope.ledger,attempts)
        elif risk(c)+sum(bounds)*r['embedding_rate']*2>50_000_000_000:scope.ledger.deny(c,'PROJECT_MONEY_LIMIT')

def prepare_retry(ledger,seq):
    """Only the transport owning the failed send may grant one retry. No reset."""
    with ledger.transaction() as (c,r):
        if not policy(c,ledger.run_id):return False
        row=c.execute('SELECT q.*,d.attempt FROM requests q JOIN development_requests d ON q.seq=d.seq WHERE q.seq=? AND q.run_id=?',(seq,ledger.run_id)).fetchone()
        if not row or row['status']!='FAILED' or row['attempt']!=0:return False
        from app.core.personal_learning import is_personal
        if is_personal(c,ledger.run_id) and row['usage_status']=='UNKNOWN':return False
        eligible=row['http_status']==429 or (row['http_status'] is not None and 500<=row['http_status']<=599) or (row['http_status'] is None and row['error_type'] in NETWORK_ERRORS)
        if not eligible:return False
        if c.execute('SELECT 1 FROM development_requests WHERE retry_of=?',(seq,)).fetchone():return False
        c.execute("UPDATE run SET state='READY' WHERE id=?",(ledger.run_id,))
        c.execute('UPDATE development_sessions SET retry_pending=? WHERE run_id=?',(seq,ledger.run_id))
        return True


def reviewed_replacement_candidate(c,r,expected):
    """Technical preflight is not approval of an unexplained historic halt."""
    from app.core.live_test import Blocked,MODELS
    if r is None or r['id']!=expected or r['state']!='HALTED' or not policy(c,expected):raise Blocked('REVIEW_REQUIRES_HALTED_DEVELOPMENT_SESSION')
    if c.execute("SELECT 1 FROM run WHERE state='READY'").fetchone():raise Blocked('ACTIVE_SESSION_EXISTS')
    if c.execute("SELECT 1 FROM requests WHERE status='PENDING'").fetchone():raise Blocked('PENDING_REQUEST_REQUIRES_RECONCILIATION')
    _validate_history(c)
    rows=c.execute('SELECT * FROM requests WHERE run_id=?',(expected,)).fetchall()
    if not rows or any(q['status']!='SUCCESS' or q['usage_status']!='KNOWN' or q['model']!=MODELS.get(q['kind']) or
                       q['input_tokens']>q['input_bound'] or q['output_tokens']>q['output_bound'] or q['cost_nano'] is None or q['cost_nano']>q['reserved_nano'] for q in rows):
        raise Blocked('CURRENT_RUN_CHARGE_OR_FAILURE_REQUIRES_REVIEW')
    if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runtime_events'").fetchone():
        if c.execute("SELECT 1 FROM runtime_events WHERE run_id=? AND kind='SAFETY_HALT'",(expected,)).fetchone():
            raise Blocked('KNOWN_HALT_REQUIRES_SPECIFIC_REPAIR')
    return {'technical_checks_passed':True,'historical_cause':'UNKNOWN','requires_manual_approval':True,'project_risk_yuan':risk(c)/1e9,'current_requests':len(rows)}


def create_reviewed_replacement(ledger,expected,*,grant_id,reviewed=False,personal_settings=None,application_fix_reviewed=False):
    from app.core.live_test import Ledger,Blocked
    from app.core.runtime_safety import event
    if reviewed is not True or not grant_id or len(grant_id)>100:raise Blocked('INDEPENDENT_REVIEW_APPROVAL_REQUIRED')
    with ledger.transaction() as (c,r):
        if personal_settings is not None:
            from app.core.personal_learning import review_settled_application_halt, saved
            if application_fix_reviewed is not True:raise Blocked('INDEPENDENT_REVIEW_APPROVAL_REQUIRED')
            review = review_settled_application_halt(c,r,expected,personal_settings)
            ident=_begin_locked(ledger,c,r,'reviewed-'+grant_id,ALLOWED_TOOLS,True,100,monitor=True)
            c.execute('INSERT INTO personal_runs VALUES (?,?)',(ident,saved(c)['id']))
            event(c,ident,'MANUAL_REVIEW_NEW_SESSION','LLM_INVALID_CITATION',review['last_seq'])
            return Ledger(ledger.path,ident)
        reviewed_replacement_candidate(c,r,expected)
        p=policy(c,expected)
        ident=_begin_locked(ledger,c,r,'reviewed-'+grant_id,set(json.loads(p['tools'])),bool(p['embedding']),p['max_requests'])
        event(c,ident,'MANUAL_REVIEW_NEW_SESSION','UNKNOWN_LEGACY_HALT_REVIEWED')
    # Deliberately no prices or paid authorization: operator must freshly confirm in UI.
    return Ledger(ledger.path,ident)
