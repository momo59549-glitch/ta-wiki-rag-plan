"""Explicit bounded lifecycle CLI for local main-board supplement entries."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
from packages.research.gen3_mainboard_universe import execute_supplement_run, mainboard_policy_from_data, prepare_supplement_run, supplement_run_status, load_mainboard_manifest, make_mainboard_policy, build_universe_coverage
from packages.research.gen3_local_market import make_local_market_contract
from packages.research.gen3_market_admission import load_content_run_state, _read_entries, _hash, _write_once

def _policy(path):return mainboard_policy_from_data(json.loads(Path(path).read_text(encoding='utf-8')))
def _run_state(run_dir,allowed):
 run=Path(run_dir).resolve();allow=Path(allowed).resolve()
 try:run.relative_to(allow)
 except ValueError as e:raise ValueError('run escapes allowed output root') from e
 if not run.is_dir() or not (run/'entries').is_dir():raise ValueError('supplement run incomplete')
 try:policy=mainboard_policy_from_data(json.loads((run/'policy.json').read_text(encoding='utf-8')));meta=json.loads((run/'symbols.json').read_text(encoding='utf-8'));inventory=json.loads((run/'manifest_inventory.json').read_text(encoding='utf-8'));pre=json.loads((run/'predecision.json').read_text(encoding='utf-8'))
 except (OSError,json.JSONDecodeError,ValueError) as e:raise ValueError('supplement run metadata invalid') from e
 if not isinstance(meta,dict) or set(meta)!={'symbols','supplement_root','supplement_contract_hash'} or not isinstance(meta['symbols'],list) or meta['symbols']!=sorted(meta['symbols']):raise ValueError('supplement run symbols metadata invalid')
 if not isinstance(inventory,dict) or set(inventory)!={'manifest_content_hash','record_count','excluded_by_board','members'} or inventory['manifest_content_hash']!=policy.manifest_content_hash or inventory['record_count']!=policy.manifest_record_count or inventory['excluded_by_board']!=policy.excluded_by_board or not isinstance(inventory['members'],list):raise ValueError('supplement manifest inventory binding invalid')
 if not isinstance(pre,dict) or pre.get('policy_hash')!=policy.policy_hash or pre.get('status')!='blocked' or pre.get('universe_hash')!=_hash({k:v for k,v in pre.items() if k!='universe_hash'}):raise ValueError('supplement predecision binding invalid')
 return run,policy,meta,inventory,pre
def _contract(path):
 v=json.loads(Path(path).read_text(encoding='utf-8')); fields={'source_id','root','date_column','open_column','high_column','low_column','close_column','volume_column'}
 if not isinstance(v,dict) or set(v)!=fields:raise ValueError('trend contract strict schema')
 return make_local_market_contract(**v)
def main(argv=None):
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
 for name in ('prepare','status','execute','finalize'):
  q=sub.add_parser(name);q.add_argument('--run-dir' if name!='prepare' else '--workspace-output-root',required=True);q.add_argument('--allowed-output-root',required=True)
  if name=='prepare':
   q.add_argument('--manifest',required=True);q.add_argument('--trend-contract-json',required=True);q.add_argument('--quality-run-dir',required=True);q.add_argument('--quality-allowed-output-root',required=True);q.add_argument('--trend-content-run-dir',required=True);q.add_argument('--trend-content-allowed-output-root',required=True);q.add_argument('--supplement-root',required=True)
  else:pass
  if name=='execute':q.add_argument('--max-files-this-run',required=True,type=int);q.add_argument('--confirm-read-source',action='store_true')
 try:
  a=p.parse_args(argv)
  if a.command=='prepare':
   inv=load_mainboard_manifest(a.manifest);contract=_contract(a.trend_contract_json)
   _,snap,ap,ad=load_content_run_state(a.trend_content_run_dir,contract,allowed_output_root=a.trend_content_allowed_output_root,quality_run_dir=a.quality_run_dir,quality_allowed_output_root=a.quality_allowed_output_root);entries=_read_entries(Path(a.trend_content_run_dir),snap,ap,ad,contract)
   if len(entries)!=len(snap.files):raise ValueError('trend content run incomplete')
   digest=_hash({'snapshot_hash':snap.snapshot_hash,'entries':[x.entry_hash for x in entries]});policy=make_mainboard_policy(inv.raw_bytes,inv.record_count,inv.excluded_by_board,digest,snap.snapshot_hash,contract.contract_hash,ap,ad,inv.members)
   pre=build_universe_coverage(policy,inv.members,entries,verified_complete_trend=True); covered={x.symbol for x in pre.entries};missing=tuple(m.symbol for m in inv.members if m.symbol not in covered)
   if not missing:raise ValueError('no supplement symbols are missing')
   run=prepare_supplement_run(policy,missing,a.supplement_root,workspace_output_root=a.workspace_output_root,allowed_output_root=a.allowed_output_root)
   _write_once(run/'manifest_inventory.json',{'manifest_content_hash':policy.manifest_content_hash,'record_count':inv.record_count,'excluded_by_board':inv.excluded_by_board,'members':[m.symbol for m in inv.members]})
   _write_once(run/'predecision.json',pre.payload()|{'universe_hash':pre.universe_hash})
   print(json.dumps({'status':'prepared','run_dir':str(run),'missing':len(missing),'policy_hash':policy.policy_hash},ensure_ascii=False));return 0
  run,policy,meta,inventory,pre=_run_state(a.run_dir,a.allowed_output_root);a.supplement_root=meta['supplement_root']
  if a.command=='execute':
   if not a.confirm_read_source:raise ValueError('execute requires --confirm-read-source')
   items=execute_supplement_run(run,a.supplement_root,policy,allowed_output_root=a.allowed_output_root,max_files_this_run=a.max_files_this_run);print(json.dumps({'status':'executed','completed':len(items),'entries':[x.payload()|{'entry_hash':x.entry_hash} for x in items]},ensure_ascii=False));return 0
  done,total=supplement_run_status(run,policy,allowed_output_root=a.allowed_output_root);complete=done==total
  if a.command=='finalize':
   if not complete:raise ValueError('supplement run incomplete')
   final=run/'final_decision.json'; payload={'policy_hash':policy.policy_hash,'predecision_hash':pre['universe_hash'],'supplement_entries':done,'members':len(inventory['members']),'status':'mainboard_universe_content_complete'}; value=payload|{'universe_hash':_hash(payload)}
   if final.exists():
    if json.loads(final.read_text(encoding='utf-8'))!=value:raise ValueError('final decision differs')
   else:_write_once(final,value)
   print(json.dumps(value,ensure_ascii=False));return 0
  print(json.dumps({'status':'complete' if complete else 'accumulating','completed':done},ensure_ascii=False));return 0
 except (ValueError,OSError,KeyError,TypeError,json.JSONDecodeError) as e:print(json.dumps({'status':'blocked','error':str(e)},ensure_ascii=False),file=sys.stderr);return 2
if __name__=='__main__':raise SystemExit(main())
