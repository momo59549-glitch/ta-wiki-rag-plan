"""Explicit gates; it never prints or persists a token."""
from __future__ import annotations
import argparse,json,os,sys
from datetime import date
from packages.research.gen3_tradability_exploratory import ExploratoryExecutionPolicy,ExploratoryRow,evaluate,apply_cost_stress
def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument('command',choices=('probe','fallback-dry-run'));p.add_argument('--confirm-network',action='store_true');p.add_argument('--confirm-read-token',action='store_true')
 try:
  a=p.parse_args(argv)
  if a.command=='probe':
   if not(a.confirm_network and a.confirm_read_token):raise ValueError('probe requires --confirm-network and --confirm-read-token')
   if not(os.environ.get('TUSHARE_TOKEN')or'').strip():raise ValueError('TUSHARE_TOKEN unavailable; no network probe attempted')
   raise ValueError('network probe requires reviewer execution')
  pcy=ExploratoryExecutionPolicy(.01);cal=(date(2020,1,2),date(2020,1,3));row=ExploratoryRow('000001',cal[1],10.,11.,9.,10.5,100.,False);d=evaluate('000001',cal,{cal[1]:row},cal[0],'sell',pcy);print(json.dumps({'status':'synthetic_only_fallback','decision':d.__dict__|{'requested_session':d.requested_session.isoformat(),'actual_session':d.actual_session.isoformat() if d.actual_session else None},'cost_stress':apply_cost_stress(.1,pcy)}));return 0
 except ValueError as e:print(json.dumps({'status':'blocked','error':str(e)}),file=sys.stderr);return 2
if __name__=='__main__':raise SystemExit(main())
