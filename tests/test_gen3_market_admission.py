from dataclasses import replace
from datetime import date
from unittest.mock import patch
import tempfile, unittest
from pathlib import Path

from packages.research.gen3_local_market import make_local_market_contract
from packages.research.gen3_quality_campaign import build_corpus_snapshot, make_campaign_contract, aggregate_campaign_reports
from packages.research.gen3_quality_run import prepare_quality_run, execute_quality_run, _load_run, _read_completed_reports
from packages.research.gen3_market_admission import (build_content_file_entry, content_run_status, decide_range_admission, execute_content_run, load_content_run_state, make_admission_policy, migrate_content_run, prepare_content_run, recover_content_run_lock, _hash, _lock_bytes, _lock_hash)
from scripts.run_gen3_market_admission import main as admission_cli

def row(day, year=2015, **extra):
    v={"date":date(year,1,day),"open":10.,"high":11.,"low":9.,"close":10.5,"volume":100.};v.update(extra);return v
def write(root,symbol,rows):
    import pyarrow as pa, pyarrow.parquet as pq
    p=root/f"{symbol}.parquet";pq.write_table(pa.Table.from_pylist(rows),p);return p
def contract(root):return make_local_market_contract(source_id="fixture",root=str(root),date_column="date",open_column="open",high_column="high",low_column="low",close_column="close",volume_column="volume")

class MarketAdmissionTests(unittest.TestCase):
 def setup_quality(self,root,out,*,research_bad=False):
    # The bad pre-start row is quarantined but the 2015 row remains eligible.
    write(root,"000001",[row(1,1992,open=12.,high=11.),row(2,2015)])
    write(root,"000002",[row(2,2015)] if not research_bad else [row(2,2015,open=12.,high=11.)])
    c=contract(root);s=build_corpus_snapshot(c,max_files=10);q=make_campaign_contract(s,max_rows_per_file=10,max_issues_per_file=10);r=prepare_quality_run(s,q,c,workspace_output_root=out,allowed_output_root=out.parent);execute_quality_run(r,c,allowed_output_root=out.parent,max_files_this_run=10)
    reports=_read_completed_reports(r,s,q,c);a=aggregate_campaign_reports(s,q,c,reports);p=make_admission_policy(c,snapshot_hash=s.snapshot_hash,campaign_hash=q.campaign_hash,aggregate_hash=a.aggregate_hash,research_end=date(2026,8,5));return c,s,q,r,p
 def test_prestart_issue_is_eligible_and_content_entries_include_zero_files(self):
  with tempfile.TemporaryDirectory() as t:
   base=Path(t);src=base/'src';out=base/'out';src.mkdir();c,s,_,r,p=self.setup_quality(src,out);d=decide_range_admission(p,r,c,allowed_output_root=base)
   self.assertEqual(d.status,"eligible_for_content_snapshot");self.assertEqual(d.excluded_issue_count,1);self.assertEqual((d.research_files,d.research_rows),(2,2))
   run=prepare_content_run(d,p,s,c,workspace_output_root=out,allowed_output_root=base);first=execute_content_run(run,s,p,c,allowed_output_root=base,max_files_this_run=1,decision=d);self.assertEqual(first.status,"accumulating");last=execute_content_run(run,s,p,c,allowed_output_root=base,max_files_this_run=1,decision=d);self.assertEqual(last.status,"historical_market_content_snapshot_complete");self.assertIsNotNone(last.content_snapshot_hash)
 def test_research_issue_blocks_and_empty_selected_is_explicit_but_all_empty_rejects(self):
  with tempfile.TemporaryDirectory() as t:
   base=Path(t);src=base/'src';out=base/'out';src.mkdir();c,s,_,r,p=self.setup_quality(src,out,research_bad=True);d=decide_range_admission(p,r,c,allowed_output_root=base);self.assertEqual(d.status,"blocked")
  with tempfile.TemporaryDirectory() as t:
   base=Path(t);src=base/'src';out=base/'out';src.mkdir();write(src,"000001",[row(1,1992,open=12.,high=11.)]);c=contract(src);s=build_corpus_snapshot(c,max_files=10);q=make_campaign_contract(s,max_rows_per_file=10,max_issues_per_file=10);r=prepare_quality_run(s,q,c,workspace_output_root=out,allowed_output_root=base);execute_quality_run(r,c,allowed_output_root=base,max_files_this_run=1);a=aggregate_campaign_reports(s,q,c,_read_completed_reports(r,s,q,c));p=make_admission_policy(c,snapshot_hash=s.snapshot_hash,campaign_hash=q.campaign_hash,aggregate_hash=a.aggregate_hash,research_end=date(2026,8,5));d=decide_range_admission(p,r,c,allowed_output_root=base)
   self.assertEqual(d.status,"blocked")
   with self.assertRaisesRegex(ValueError,"not eligible"):prepare_content_run(d,p,s,c,workspace_output_root=out,allowed_output_root=base)
 def test_content_value_change_same_footer_changes_identity_and_source_is_not_written(self):
  with tempfile.TemporaryDirectory() as t:
   base=Path(t);src=base/'src';out=base/'out';src.mkdir();c,s,_,r,p=self.setup_quality(src,out);d=decide_range_admission(p,r,c,allowed_output_root=base);one=build_content_file_entry(s.files[1],c,p,d,s);write(src,"000002",[row(2,2015,close=10.6)]);two=build_content_file_entry(s.files[1],c,p,d,s);self.assertNotEqual(one.content_hash,two.content_hash)
 def test_partial_lock_tmp_and_date_bounds_fail_closed(self):
  with tempfile.TemporaryDirectory() as t:
   base=Path(t);src=base/'src';out=base/'out';src.mkdir();c,s,_,r,p=self.setup_quality(src,out);d=decide_range_admission(p,r,c,allowed_output_root=base);run=prepare_content_run(d,p,s,c,workspace_output_root=out,allowed_output_root=base);(run/'entries'/'000001.json.tmp').write_text('x')
   with self.assertRaisesRegex(ValueError,"orphan"):execute_content_run(run,s,p,c,allowed_output_root=base,max_files_this_run=1,decision=d)
   (run/'entries'/'000001.json.tmp').unlink();(run/'.lock').write_text('x')
   with self.assertRaisesRegex(ValueError,"residual"):content_run_status(run,s,c,allowed_output_root=base,policy=p,decision=d)
 def test_policy_window_and_limits_fail_closed(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);root.mkdir(exist_ok=True);write(root,"000001",[row(2,2015)]);c=contract(root);s=build_corpus_snapshot(c,max_files=10);q=make_campaign_contract(s,max_rows_per_file=10,max_issues_per_file=10)
   with self.assertRaises(ValueError):make_admission_policy(c,snapshot_hash=s.snapshot_hash,campaign_hash=q.campaign_hash,aggregate_hash="sha256:"+'a'*64,research_end=date(2026,9,1))
 def test_self_consistent_cross_policy_entry_and_decision_total_attacks_reject(self):
  with tempfile.TemporaryDirectory() as t:
   base=Path(t);src=base/'src';out=base/'out';src.mkdir();c,s,_,r,p=self.setup_quality(src,out);d=decide_range_admission(p,r,c,allowed_output_root=base);run=prepare_content_run(d,p,s,c,workspace_output_root=out,allowed_output_root=base);execute_content_run(run,s,p,c,allowed_output_root=base,max_files_this_run=1,decision=d)
   raw=__import__('json').loads((run/'entries'/'000001.json').read_text());raw['policy_hash']='sha256:'+'b'*64; raw['entry_hash']=_hash({k:v for k,v in raw.items() if k!='entry_hash'});(run/'entries'/'000001.json').write_text(__import__('json').dumps(raw))
   with self.assertRaisesRegex(ValueError,'binding'):execute_content_run(run,s,p,c,allowed_output_root=base,max_files_this_run=1,decision=d)
   raw['policy_hash']=p.policy_hash;raw['entry_hash']=_hash({k:v for k,v in raw.items() if k!='entry_hash'});(run/'entries'/'000001.json').write_text(__import__('json').dumps(raw));bad0=replace(d,research_rows=d.research_rows+1,decision_hash='sha256:'+'0'*64);bad=replace(bad0,decision_hash=_hash(bad0.payload()))
   with self.assertRaisesRegex(ValueError,'identity|metadata|totals'):execute_content_run(run,s,p,c,allowed_output_root=base,max_files_this_run=1,decision=bad)
 def test_explicit_legacy_lock_recovery_and_active_lock_refusal(self):
  with tempfile.TemporaryDirectory() as t:
   base=Path(t);src=base/'src';out=base/'out';src.mkdir();c,s,_,r,p=self.setup_quality(src,out);d=decide_range_admission(p,r,c,allowed_output_root=base);run=prepare_content_run(d,p,s,c,workspace_output_root=out,allowed_output_root=base);lock=run/'.lock';lock.write_bytes(b'lock');digest=_lock_hash(b'lock')
   with self.assertRaisesRegex(ValueError,'confirm'):recover_content_run_lock(run,s,p,d,c,allowed_output_root=base,expected_lock_sha256=digest,reason='external_timeout',confirm_process_terminated=False,allow_legacy_lock=True)
   with self.assertRaisesRegex(ValueError,'legacy'):recover_content_run_lock(run,s,p,d,c,allowed_output_root=base,expected_lock_sha256=digest,reason='external_timeout',confirm_process_terminated=True)
   receipt=recover_content_run_lock(run,s,p,d,c,allowed_output_root=base,expected_lock_sha256=digest,reason='external_timeout',confirm_process_terminated=True,allow_legacy_lock=True);self.assertTrue(receipt.is_file());self.assertFalse(lock.exists())
   lock.write_bytes(_lock_bytes(run,p,d,s));digest=_lock_hash(lock.read_bytes())
   with patch('packages.research.gen3_market_admission._pid_active',return_value=True), self.assertRaisesRegex(ValueError,'active'):recover_content_run_lock(run,s,p,d,c,allowed_output_root=base,expected_lock_sha256=digest,reason='interrupted_process',confirm_process_terminated=True)
 def test_explicit_v1_migration_is_required_and_idempotent(self):
  with tempfile.TemporaryDirectory() as t:
   base=Path(t);src=base/'src';out=base/'out';src.mkdir();c,s,_,r,p=self.setup_quality(src,out);d=decide_range_admission(p,r,c,allowed_output_root=base);run=prepare_content_run(d,p,s,c,workspace_output_root=out,allowed_output_root=base)
   (run/'recoveries').rmdir()
   with self.assertRaisesRegex(ValueError,'identity'):content_run_status(run,s,c,allowed_output_root=base,policy=p,decision=d)
   with self.assertRaisesRegex(ValueError,'confirm'):migrate_content_run(run,s,p,d,c,allowed_output_root=base,confirm_v1_to_v2=False)
   receipt=migrate_content_run(run,s,p,d,c,allowed_output_root=base,confirm_v1_to_v2=True)
   self.assertTrue(receipt.is_file());self.assertEqual(content_run_status(run,s,c,allowed_output_root=base,policy=p,decision=d).status,'waiting')
   self.assertEqual(migrate_content_run(run,s,p,d,c,allowed_output_root=base,confirm_v1_to_v2=True),receipt)
 def test_v1_migration_rejects_extra_lock_tmp_and_tampered_entry_but_allows_partial_directory(self):
  with tempfile.TemporaryDirectory() as t:
   base=Path(t);src=base/'src';out=base/'out';src.mkdir();c,s,_,r,p=self.setup_quality(src,out);d=decide_range_admission(p,r,c,allowed_output_root=base);run=prepare_content_run(d,p,s,c,workspace_output_root=out,allowed_output_root=base);(run/'recoveries').rmdir();(run/'extra').write_text('x')
   with self.assertRaisesRegex(ValueError,'unexpected'):migrate_content_run(run,s,p,d,c,allowed_output_root=base,confirm_v1_to_v2=True)
   (run/'extra').unlink();(run/'entries'/'000001.json.tmp').write_text('x')
   with self.assertRaisesRegex(ValueError,'tmp'):migrate_content_run(run,s,p,d,c,allowed_output_root=base,confirm_v1_to_v2=True)
   (run/'entries'/'000001.json.tmp').unlink();(run/'.lock').write_text('lock')
   with self.assertRaisesRegex(ValueError,'unexpected|lock'):migrate_content_run(run,s,p,d,c,allowed_output_root=base,confirm_v1_to_v2=True)
   (run/'.lock').unlink();(run/'recoveries').mkdir()
   self.assertTrue(migrate_content_run(run,s,p,d,c,allowed_output_root=base,confirm_v1_to_v2=True).is_file())
  with tempfile.TemporaryDirectory() as t:
   base=Path(t);src=base/'src';out=base/'out';src.mkdir();c,s,_,r,p=self.setup_quality(src,out);d=decide_range_admission(p,r,c,allowed_output_root=base);run=prepare_content_run(d,p,s,c,workspace_output_root=out,allowed_output_root=base);execute_content_run(run,s,p,c,allowed_output_root=base,max_files_this_run=1,decision=d);(run/'recoveries').rmdir()
   raw=__import__('json').loads((run/'entries'/'000001.json').read_text());raw['policy_hash']='sha256:'+'f'*64;raw['entry_hash']=_hash({k:v for k,v in raw.items() if k!='entry_hash'});(run/'entries'/'000001.json').write_text(__import__('json').dumps(raw))
   with self.assertRaisesRegex(ValueError,'binding|invalid'):migrate_content_run(run,s,p,d,c,allowed_output_root=base,confirm_v1_to_v2=True)
 def test_load_existing_state_does_not_decide_or_scan_content(self):
  with tempfile.TemporaryDirectory() as t:
   base=Path(t);src=base/'src';out=base/'out';src.mkdir();c,s,_,r,p=self.setup_quality(src,out);d=decide_range_admission(p,r,c,allowed_output_root=base);run=prepare_content_run(d,p,s,c,workspace_output_root=out,allowed_output_root=base)
   with patch('packages.research.gen3_market_admission.decide_range_admission',side_effect=AssertionError('must not rescan')), patch('packages.research.gen3_market_admission._content_entry',side_effect=AssertionError('must not scan rows')):
    loaded,got_s,got_p,got_d=load_content_run_state(run,c,allowed_output_root=base,quality_run_dir=r,quality_allowed_output_root=base)
   self.assertEqual((loaded,got_s.snapshot_hash,got_p.policy_hash,got_d.decision_hash),(run,s.snapshot_hash,p.policy_hash,d.decision_hash))
 def test_cli_status_and_one_file_execute_do_not_redecide_window(self):
  with tempfile.TemporaryDirectory() as t:
   base=Path(t);src=base/'src';out=base/'out';src.mkdir();c,s,_,r,p=self.setup_quality(src,out);d=decide_range_admission(p,r,c,allowed_output_root=base);run=prepare_content_run(d,p,s,c,workspace_output_root=out,allowed_output_root=base);cp=base/'contract.json';cp.write_text(__import__('json').dumps({'source_id':'fixture','root':str(src),'date_column':'date','open_column':'open','high_column':'high','low_column':'low','close_column':'close','volume_column':'volume'}))
   common=[str(cp),'--quality-run-dir',str(r),'--quality-allowed-output-root',str(base),'--run-dir',str(run),'--allowed-output-root',str(base)]
   with patch('scripts.run_gen3_market_admission.decide_range_admission',side_effect=AssertionError('must not redecide')), patch('packages.research.gen3_market_admission._content_entry',wraps=build_content_file_entry.__globals__['_content_entry']) as scan:
    self.assertEqual(admission_cli(['status',*common]),0)
    self.assertEqual(admission_cli(['execute',*common,'--max-files-this-run','1','--confirm-read-source']),0)
   self.assertEqual(scan.call_count,1)
 def test_cli_v1_migration_requires_explicit_confirmation(self):
  with tempfile.TemporaryDirectory() as t:
   base=Path(t);src=base/'src';out=base/'out';src.mkdir();c,s,_,r,p=self.setup_quality(src,out);d=decide_range_admission(p,r,c,allowed_output_root=base);run=prepare_content_run(d,p,s,c,workspace_output_root=out,allowed_output_root=base);(run/'recoveries').rmdir();cp=base/'contract.json';cp.write_text(__import__('json').dumps({'source_id':'fixture','root':str(src),'date_column':'date','open_column':'open','high_column':'high','low_column':'low','close_column':'close','volume_column':'volume'}))
   common=[str(cp),'--quality-run-dir',str(r),'--quality-allowed-output-root',str(base),'--run-dir',str(run),'--allowed-output-root',str(base)]
   self.assertEqual(admission_cli(['migrate-run',*common]),2);self.assertEqual(admission_cli(['migrate-run',*common,'--confirm-v1-to-v2']),0)
 def test_utf8_persistent_json_handles_unicode_quality_run_path_for_v1_migration_and_status(self):
  with tempfile.TemporaryDirectory() as t:
   base=Path(t)/'股票模型';base.mkdir();src=base/'src';out=base/'out';src.mkdir();c,s,_,r,p=self.setup_quality(src,out);d=decide_range_admission(p,r,c,allowed_output_root=base);self.assertIn('股票模型',str(r));run=prepare_content_run(d,p,s,c,workspace_output_root=out,allowed_output_root=base);(run/'recoveries').rmdir()
   loaded,got_s,got_p,got_d=load_content_run_state(run,c,allowed_output_root=base,quality_run_dir=r,quality_allowed_output_root=base)
   self.assertEqual((loaded,got_s.snapshot_hash,got_p.policy_hash,got_d.decision_hash),(run,s.snapshot_hash,p.policy_hash,d.decision_hash))
   migrate_content_run(run,got_s,got_p,got_d,c,allowed_output_root=base,confirm_v1_to_v2=True)
   self.assertEqual(content_run_status(run,got_s,c,allowed_output_root=base,policy=got_p,decision=got_d).status,'waiting')
