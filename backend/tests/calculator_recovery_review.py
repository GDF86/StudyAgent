"""READ ONLY. This command has no create/approve/recover mode."""
import argparse,json,sqlite3,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
TARGET='8e4bb9a4-075c-486a-b843-71dc47ff8393'

def inspect(c,run_id):
    from app.core.development_budget import risk,_validate_history
    from app.core.live_test import Blocked
    r=c.execute('SELECT * FROM run WHERE id=?',(run_id,)).fetchone()
    if not r:raise ValueError('RUN_NOT_FOUND')
    rows=c.execute('SELECT * FROM requests WHERE run_id=?',(run_id,)).fetchall()
    pending=c.execute("SELECT count(*) FROM requests WHERE status='PENDING'").fetchone()[0]
    history_valid=False
    if not pending:
        try:_validate_history(c,monitor=True);history_valid=True
        except Blocked:pass  # Invalid history is reported blocked, never silently ignored.
    ready=c.execute("SELECT count(*) FROM run WHERE state='READY'").fetchone()[0]
    events=[dict(x) for x in c.execute("SELECT kind,reason,seq FROM runtime_events WHERE run_id=? ORDER BY id",(run_id,))]
    known=bool(rows) and all(x['status']=='SUCCESS' and x['usage_status']=='KNOWN' and x['cost_nano'] is not None and x['input_tokens']<=x['input_bound'] and x['output_tokens']<=x['output_bound'] and x['cost_nano']<=x['reserved_nano'] for x in rows)
    halt_reasons={x['reason'] for x in events if x['kind']=='SAFETY_HALT'}
    signature=bool(halt_reasons) and halt_reasons<={'AGENT_FAILED','UNSPECIFIED_HALT'}
    profile=c.execute('SELECT * FROM personal_prices ORDER BY rowid DESC LIMIT 1').fetchone()
    quote=2*(64000*profile['input_rate']+2048*profile['output_rate']) if profile else None
    return {'run_id':run_id,'state':r['state'],'financial_checks':'PASS' if r['state']=='HALTED' and known and history_valid and not pending and not ready else 'BLOCKED',
            'recorded_signature_matches':signature,'current_usage_known':known,'global_pending':pending,'other_ready':ready,
            'historical_unknown':c.execute("SELECT count(*) FROM requests WHERE usage_status='UNKNOWN'").fetchone()[0],
            'project_risk_yuan':risk(c)/1e9,'quote_yuan':quote/1e9 if quote is not None else None,
            'manual_approval_required':True,'recovery_execution':'BLOCKED_PENDING_LOADED_CODE_CHECK_AND_APPROVAL','events':events}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--ledger',type=Path,default=ROOT/'test-results/bailian-first-live/budget.sqlite');p.add_argument('--run-id',default=TARGET)
    args=p.parse_args();path=args.ledger.resolve()
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as c:
        c.row_factory=sqlite3.Row
        if path.with_suffix('.sqlite.seal').read_text()!=c.execute('SELECT id FROM project').fetchone()[0]:raise ValueError('SEAL_IDENTITY_MISMATCH')
        result=inspect(c,args.run_id)
    result['seal_matches']=True
    print(json.dumps(result,ensure_ascii=True))
if __name__=='__main__':main()
