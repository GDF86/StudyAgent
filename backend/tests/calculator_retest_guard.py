"""Prepared only: requires an independently approved, already-authorized new run."""
from app.core.live_test import Ledger,Blocked
from app.core.development_budget import reserve

class CalculatorRetestLedger(Ledger):
    batch='calculator-only-20261112-v1'
    def reserve(self,stage,kind,payload,*,offline):
        if stage!='agent' or kind!='llm' or payload.get('model')!='qwen-flash':raise Blocked('CALCULATOR_RETEST_LLM_ONLY')
        if any(t.get('function',{}).get('name')!='calculator' for t in payload.get('tools',[])):raise Blocked('CALCULATOR_RETEST_TOOL_ONLY')
        for message in payload.get('messages',[]):
            for call in message.get('tool_calls',[]) or []:
                if call.get('function',{}).get('name')!='calculator':raise Blocked('CALCULATOR_RETEST_TOOL_ONLY')
        with self.transaction() as (c,r):
            if r['state']!='READY' or not r['authorized']:raise Blocked('CALCULATOR_RETEST_REQUIRES_NEW_AUTHORIZATION')
            c.execute('CREATE TABLE IF NOT EXISTS calculator_retest_attempts (seq INTEGER PRIMARY KEY,batch TEXT NOT NULL)')
            rows=c.execute('SELECT q.* FROM requests q JOIN calculator_retest_attempts b ON q.seq=b.seq WHERE b.batch=?',(self.batch,)).fetchall()
            if rows and any(q['run_id']!=self.run_id for q in rows):raise Blocked('CALCULATOR_RETEST_BATCH_CLOSED')
            if len(rows)>=2:raise Blocked('CALCULATOR_RETEST_ATTEMPT_LIMIT')
            if any(q['status']!='SUCCESS' or q['usage_status']!='KNOWN' for q in rows):raise Blocked('CALCULATOR_RETEST_PREVIOUS_FAILURE')
            held=sum(q['cost_nano'] for q in rows)
            seq=reserve(self,c,r,stage,kind,payload,offline)
            quote=c.execute('SELECT reserved_nano FROM requests WHERE seq=?',(seq,)).fetchone()[0]
            if held+quote>25_344_000:raise Blocked('CALCULATOR_RETEST_MONEY_LIMIT')
            c.execute('INSERT INTO calculator_retest_attempts VALUES (?,?)',(seq,self.batch))
            return seq
