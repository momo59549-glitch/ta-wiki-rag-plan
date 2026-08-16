"""Bounded CLI for the non-adjudicable exploratory feasibility audit."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from packages.research.gen3_tradability_audit import (h, make_policy,
    load_frozen_mainboard_audit_state, prepare_frozen_audit,
    load_frozen_audit_run_state, execute_frozen_audit_run, audit_status,
    finalize_frozen_audit)
from packages.research.gen3_tradability_exploratory import ExploratoryExecutionPolicy
from packages.research.gen3_local_market import make_local_market_contract

def _contract(path):
    v=json.loads(Path(path).read_text(encoding='utf-8'))
    required={'source_id','root','date_column','open_column','high_column','low_column','close_column','volume_column'}
    if not isinstance(v,dict) or set(v)!=required:raise ValueError('trend contract strict schema')
    return make_local_market_contract(**v)

def main(argv=None):
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest='command',required=True)
    q=sub.add_parser('prepare');q.add_argument('--mainboard-run-dir',required=True);q.add_argument('--mainboard-allowed-output-root',required=True);q.add_argument('--trend-content-run-dir',required=True);q.add_argument('--trend-content-allowed-output-root',required=True);q.add_argument('--quality-run-dir',required=True);q.add_argument('--quality-allowed-output-root',required=True);q.add_argument('--trend-contract-json',required=True);q.add_argument('--workspace-output-root',required=True);q.add_argument('--allowed-output-root',required=True);q.add_argument('--base-round-trip-cost',required=True,type=float)
    for name in ('status','finalize'):
        q=sub.add_parser(name);q.add_argument('--run-dir',required=True);q.add_argument('--allowed-output-root',required=True)
    q=sub.add_parser('execute');q.add_argument('--run-dir',required=True);q.add_argument('--allowed-output-root',required=True);q.add_argument('--max-files-this-run',required=True,type=int);q.add_argument('--phase',choices=('auto','calendar','audit'),default='auto');q.add_argument('--confirm-read-source',action='store_true')
    try:
        a=p.parse_args(argv)
        if a.command=='prepare':
            state=load_frozen_mainboard_audit_state(a.mainboard_run_dir,a.mainboard_allowed_output_root,trend_content_run_dir=a.trend_content_run_dir,trend_content_allowed_output_root=a.trend_content_allowed_output_root,quality_run_dir=a.quality_run_dir,quality_allowed_output_root=a.quality_allowed_output_root,trend_contract=_contract(a.trend_contract_json))
            pre=json.loads((state.run_dir/'predecision.json').read_text(encoding='utf-8'))['universe_hash']
            supplement=h({'entries':[x.entry_hash for x in state.supplement_entries]})
            fallback=ExploratoryExecutionPolicy(a.base_round_trip_cost);fallback.verify()
            policy=make_policy(state.policy.policy_hash,pre,state.universe_hash,state.policy.trend_content_snapshot_hash,supplement,state.policy.research_start,state.policy.research_end,fallback)
            run=prepare_frozen_audit(state,policy,fallback,a.workspace_output_root,a.allowed_output_root)
            print(json.dumps({'status':'prepared','run_dir':str(run),'policy_hash':policy.policy_hash},ensure_ascii=False));return 0
        if a.command=='status':
            run,_,_,_=load_frozen_audit_run_state(a.run_dir,a.allowed_output_root);print(json.dumps(audit_status(run,a.allowed_output_root),ensure_ascii=False));return 0
        if a.command=='execute':
            if not a.confirm_read_source:raise ValueError('execute requires --confirm-read-source')
            done=execute_frozen_audit_run(a.run_dir,a.allowed_output_root,max_files_this_run=a.max_files_this_run,phase=a.phase);print(json.dumps({'status':'executed',**done},ensure_ascii=False));return 0
        print(json.dumps(finalize_frozen_audit(a.run_dir,a.allowed_output_root),ensure_ascii=False));return 0
    except (ValueError,OSError,TypeError,KeyError,json.JSONDecodeError) as exc:
        print(json.dumps({'status':'blocked','error':str(exc)},ensure_ascii=False),file=sys.stderr);return 2
if __name__=='__main__':raise SystemExit(main())
