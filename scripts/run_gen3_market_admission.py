"""Explicit bounded CLI for Gen3 research-window content snapshots."""
from __future__ import annotations
import argparse, json, sys
from datetime import date
from pathlib import Path
from packages.research.gen3_local_market import make_local_market_contract
from packages.research.gen3_market_admission import (content_run_status, decide_range_admission, execute_content_run, load_content_run_state, make_admission_policy, migrate_content_run, prepare_content_run, recover_content_run_lock)
from packages.research.gen3_quality_run import _load_run
from packages.research.gen3_quality_campaign import aggregate_campaign_reports

FIELDS={"source_id","root","date_column","open_column","high_column","low_column","close_column","volume_column"}
def _contract(path:str):
    raw=json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw,dict) or set(raw)!=FIELDS:raise ValueError("contract JSON must have exact local market fields")
    return make_local_market_contract(**raw)
def _prepare_state(args,contract):
    root,snapshot,campaign,_=_load_run(args.quality_run_dir,contract,allowed_output_root=args.quality_allowed_output_root)
    from packages.research.gen3_quality_run import _read_completed_reports
    reports=_read_completed_reports(root,snapshot,campaign,contract); agg=aggregate_campaign_reports(snapshot,campaign,contract,reports)
    policy=make_admission_policy(contract,snapshot_hash=snapshot.snapshot_hash,campaign_hash=campaign.campaign_hash,aggregate_hash=agg.aggregate_hash,research_end=date.fromisoformat(args.research_end))
    decision=decide_range_admission(policy,root,contract,allowed_output_root=args.quality_allowed_output_root)
    return snapshot,policy,decision
def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description="Gen3 market admission content snapshot")
    sub=p.add_subparsers(dest="command",required=True)
    for name in ("prepare","status","execute","recover-lock","migrate-run"):
        q=sub.add_parser(name);q.add_argument("contract_json");q.add_argument("--quality-run-dir",required=True);q.add_argument("--quality-allowed-output-root",required=True);q.add_argument("--run-dir" if name!="prepare" else "--workspace-output-root",required=True);q.add_argument("--allowed-output-root",required=True)
        if name=="prepare":q.add_argument("--research-end",required=True)
        if name=="execute":q.add_argument("--max-files-this-run",required=True,type=int);q.add_argument("--confirm-read-source",action="store_true")
        if name=="recover-lock":q.add_argument("--expected-lock-sha256",required=True);q.add_argument("--reason",required=True,choices=("external_timeout","interrupted_process"));q.add_argument("--confirm-process-terminated",action="store_true");q.add_argument("--allow-legacy-lock",action="store_true")
        if name=="migrate-run":q.add_argument("--confirm-v1-to-v2",action="store_true")
    try:
        args=p.parse_args(argv);c=_contract(args.contract_json)
        if args.command=="prepare":snapshot,policy,decision=_prepare_state(args,c)
        else:_,snapshot,policy,decision=load_content_run_state(args.run_dir,c,allowed_output_root=args.allowed_output_root,quality_run_dir=args.quality_run_dir,quality_allowed_output_root=args.quality_allowed_output_root)
        if args.command=="prepare":
            run=prepare_content_run(decision,policy,snapshot,c,workspace_output_root=args.workspace_output_root,allowed_output_root=args.allowed_output_root);print(json.dumps({"status":"prepared","run_dir":str(run),"decision_hash":decision.decision_hash},ensure_ascii=False));return 0
        if args.command=="status": print(json.dumps(content_run_status(args.run_dir,snapshot,c,allowed_output_root=args.allowed_output_root,policy=policy,decision=decision).as_dict(),ensure_ascii=False));return 0
        if args.command=="recover-lock":
            receipt=recover_content_run_lock(args.run_dir,snapshot,policy,decision,c,allowed_output_root=args.allowed_output_root,expected_lock_sha256=args.expected_lock_sha256,reason=args.reason,confirm_process_terminated=args.confirm_process_terminated,allow_legacy_lock=args.allow_legacy_lock);print(json.dumps({"status":"lock_recovered","receipt":str(receipt)},ensure_ascii=False));return 0
        if args.command=="migrate-run":
            receipt=migrate_content_run(args.run_dir,snapshot,policy,decision,c,allowed_output_root=args.allowed_output_root,confirm_v1_to_v2=args.confirm_v1_to_v2);print(json.dumps({"status":"migrated_v1_to_v2","receipt":str(receipt)},ensure_ascii=False));return 0
        if not args.confirm_read_source:raise ValueError("execute requires --confirm-read-source")
        print(json.dumps(execute_content_run(args.run_dir,snapshot,policy,c,allowed_output_root=args.allowed_output_root,max_files_this_run=args.max_files_this_run,decision=decision).as_dict(),ensure_ascii=False));return 0
    except (OSError,ValueError,TypeError,KeyError,json.JSONDecodeError) as exc: print(json.dumps({"status":"blocked","error":str(exc)},ensure_ascii=False),file=sys.stderr);return 2
if __name__=="__main__":raise SystemExit(main())
