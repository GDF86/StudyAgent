"""Persisted batch attempt/money sublimit inside the existing Guard transaction."""
from app.core.live_test import Ledger,Blocked
from app.core.development_budget import reserve

class AcceptanceLedger(Ledger):
    batch='os-real-stage1-20261111-approved20-v1'
    def reserve(self,stage,kind,payload,*,offline):
        with self.transaction() as (c,r):
            try:purpose=__import__('json').loads(payload.get('messages',[{}])[-1].get('content','{}')).get('purpose')
            except (ValueError,AttributeError,IndexError):purpose=None
            if purpose=='conversation_summary':raise Blocked('ACCEPTANCE_HIDDEN_SUMMARY_FORBIDDEN')
            c.execute('CREATE TABLE IF NOT EXISTS acceptance_attempts (seq INTEGER PRIMARY KEY,batch TEXT NOT NULL)')
            rows=c.execute('SELECT q.* FROM requests q JOIN acceptance_attempts a ON a.seq=q.seq WHERE a.batch=?',(self.batch,)).fetchall()
            if any(q['run_id']!=self.run_id for q in rows):raise Blocked('ACCEPTANCE_BATCH_CLOSED')
            if len(rows)>=20 or sum(q['kind']==kind for q in rows)>=10:raise Blocked('ACCEPTANCE_ATTEMPT_LIMIT')
            held=sum(q['cost_nano'] if q['status']=='SUCCESS' and q['cost_nano'] is not None else q['reserved_nano'] for q in rows)
            seq=reserve(self,c,r,stage,kind,payload,offline)
            quote=c.execute('SELECT reserved_nano FROM requests WHERE seq=?',(seq,)).fetchone()[0]
            if held+quote>180_000_000:raise Blocked('ACCEPTANCE_MONEY_LIMIT')
            c.execute('INSERT INTO acceptance_attempts VALUES (?,?)',(seq,self.batch))
            return seq
