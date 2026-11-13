"""Incident-specific CLI. No operation happens on import; never calls a model."""
import argparse,json,socket,sys,ast,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.core.calculator_recovery import TARGET,prepare,create,authorize
from app.core.live_test import Ledger

def settings():
    tree=ast.parse((ROOT/'app/core/config.py').read_text(encoding='utf8'))
    cls=next(x for x in tree.body if isinstance(x,ast.ClassDef) and x.name=='Settings')
    for x in cls.body:
        if isinstance(x,ast.AnnAssign):os.environ.pop(x.target.id.upper(),None)
    from app.core.config import Settings
    return Settings(_env_file=ROOT/'.env',live_test_mode=True,personal_learning=True,llm_max_tokens=2048,agent_max_rounds=2,agent_max_tool_calls=1)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','create','authorize'])
    p.add_argument('--ticket',type=Path,required=True);p.add_argument('--run-id');p.add_argument('--approve',action='store_true')
    p.add_argument('--ledger',type=Path,default=ROOT/'test-results/bailian-first-live/budget.sqlite');a=p.parse_args()
    if a.action=='prepare':
        ticket=prepare(a.ledger,TARGET)
        a.ticket.parent.mkdir(parents=True,exist_ok=True)
        with a.ticket.open('x',encoding='utf8') as f:json.dump(ticket,f,indent=2)
        print('READ_ONLY_TICKET_PREPARED; not approved, no session created.');return
    if not a.approve:p.error('Explicit independent user approval is required')
    ticket=json.loads(a.ticket.read_text(encoding='utf8'))
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1',8010))  # Refuse running server; never terminate it.
        if a.action=='create':
            new=create(a.ledger,ticket,True);print(json.dumps({'new_run_id':new.run_id,'authorized':False}))
        else:
            if not a.run_id:p.error('authorize requires the actual new run-id')
            authorize(Ledger(a.ledger,a.run_id),ticket,settings(),True)
            print('AUTHORIZED_FINITE_CALCULATOR_GRANT; no model request sent.')
if __name__=='__main__':main()
