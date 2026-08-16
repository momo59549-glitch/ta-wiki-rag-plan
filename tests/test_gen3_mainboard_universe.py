from datetime import date, datetime, timezone
from pathlib import Path
import json, tempfile, unittest
from packages.research.gen3_local_market import make_local_market_contract
from packages.research.gen3_market_admission import ContentFileEntry, RangeAdmissionDecision, make_admission_policy, _hash
from packages.research.gen3_mainboard_universe import END, MainboardMember, SupplementContentEntry, build_universe_coverage, execute_supplement_run, load_mainboard_manifest, make_mainboard_policy, prepare_supplement_run, scan_supplement
from packages.research.gen3_quality_campaign import build_corpus_snapshot, make_campaign_contract
from packages.research.gen3_quality_run import prepare_quality_run, execute_quality_run, _read_completed_reports
from packages.research.gen3_quality_campaign import aggregate_campaign_reports
from packages.research.gen3_market_admission import decide_range_admission, prepare_content_run, execute_content_run
from scripts.run_gen3_mainboard_universe import main as universe_cli
from scripts.run_gen3_tradability_audit import main as audit_cli

H=lambda c:'sha256:'+c*64
def trend(symbol,rows=1):
 b=ContentFileEntry(symbol,rows,date(2015,1,2) if rows else None,date(2015,1,2) if rows else None,H('a'),H('b'),H('c'),H('d'),H('e'),H('f'),H('0'));return ContentFileEntry(**{**b.__dict__,'entry_hash':_hash(b.payload())})
def member(symbol='000001',end=None):
 ex='SZSE' if symbol[:3] in {'000','001','002','003'} else 'SSE'; suffix='SZ' if ex=='SZSE' else 'SH'
 return MainboardMember(symbol,symbol+'.'+suffix,'X',ex,'主板','D' if end else 'L',date(2010,1,1),end,datetime(2026,8,6,tzinfo=timezone.utc))
def record(symbol,market='主板'):
 ex='SZSE' if symbol[:3] in {'000','001','002','003','300','301'} else 'SSE';suf='SZ' if ex=='SZSE' else 'SH'
 return {'source':'tushare.stock_basic','symbol':symbol,'ts_code':symbol+'.'+suf,'name':'名称','exchange':ex,'market':market,'list_status':'L','active_from':'2010-01-01','active_to':None,'fetched_at':'2026-08-06T00:00:00+00:00'}
def row():return {'trade_date':date(2015,1,2),'open':10.,'high':11.,'low':9.,'close':10.5,'volume':100.,'is_st':False}
class Tests(unittest.TestCase):
 def policy(self,root):
  c=make_local_market_contract(source_id='trend',root=str(root),date_column='date',open_column='open',high_column='high',low_column='low',close_column='close',volume_column='volume');p=make_admission_policy(c,snapshot_hash=H('1'),campaign_hash=H('2'),aggregate_hash=H('3'),research_end=END);d0=RangeAdmissionDecision(p.policy_hash,'x',(),0,1,1,'eligible_for_content_snapshot',H('0'));d=RangeAdmissionDecision(**{**d0.__dict__,'decision_hash':_hash(d0.payload())});return make_mainboard_policy(b'x',4,2,H('4'),H('5'),c.contract_hash,p,d,tuple(sorted((member('000562',date(2015,1,26)),member('601268',date(2015,5,21))),key=lambda x:x.symbol)))
 def test_real_shape_all_boards_and_unicode(self):
  with tempfile.TemporaryDirectory() as t:
   f=Path(t)/'股票模型';f.mkdir();p=f/'a.jsonl';items=[record('000001'),record('300001','创业板'),record('688001','科创板'),record('830001','北交所')];p.write_bytes(('\n'.join(json.dumps(x,ensure_ascii=False) for x in items)+'\n').encode())
   inv=load_mainboard_manifest(p);self.assertEqual((inv.record_count,inv.excluded_by_board,[x.symbol for x in inv.members]),(4,3,['000001']))
   bad=record('000002');bad['exchange']='SSE';p.write_bytes((json.dumps(bad,ensure_ascii=False)+'\n').encode());
   with self.assertRaises(ValueError):load_mainboard_manifest(p)
 def test_manifest_rejects_extra_duplicate_dates_and_naive(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'x';v=record('000001');v['fetched_at']='bad';p.write_bytes((json.dumps(v)+'\n').encode());
   with self.assertRaises(ValueError):load_mainboard_manifest(p)
   v=record('000001');p.write_bytes((json.dumps(v)+'\n'+json.dumps(v)+'\n').encode());
   with self.assertRaisesRegex(ValueError,'duplicate'):load_mainboard_manifest(p)
   v['extra']=1;p.write_bytes((json.dumps(v)+'\n').encode());
   with self.assertRaisesRegex(ValueError,'schema'):load_mainboard_manifest(p)
 def test_exact_zero_exceptions_missing_and_supplement_binding(self):
  with tempfile.TemporaryDirectory() as t:
   p=self.policy(Path(t));ms=tuple(sorted((member('000001'),member('000562',date(2015,1,26)),member('601268',date(2015,5,21))),key=lambda x:x.symbol));d=build_universe_coverage(p,ms,(trend('000001'),trend('000562',0),trend('601268',0)));self.assertEqual((d.status,d.trend_zero_explicit),('mainboard_universe_content_complete',2))
   outside=build_universe_coverage(p,ms,(trend('000001'),trend('000562',0),trend('601268',0),trend('600001')),verified_complete_trend=True);self.assertEqual(outside.trend_outside_research_members,1)
   with self.assertRaisesRegex(ValueError,'unverified'):build_universe_coverage(p,ms,(trend('000001'),trend('000562',0),trend('601268',0),trend('600001')))
   self.assertEqual(build_universe_coverage(p,(member('000562'),),(trend('000562',0),)).status,'blocked')
   fake=SupplementContentEntry(p.policy_hash,'000001',1,date(2015,1,1),date(2015,1,1),H('9'),H('8'),H('7'),H('0'));fake=SupplementContentEntry(**{**fake.__dict__,'entry_hash':_hash(fake.payload())})
   with self.assertRaisesRegex(ValueError,'may not'):build_universe_coverage(p,(member(),),(trend('000001'),),(fake,))
 def test_supplement_content_hash_and_runner_rejects_arbitrary_entry(self):
  with tempfile.TemporaryDirectory() as t:
   import pyarrow as pa,pyarrow.parquet as pq
   root=Path(t)/'补充';root.mkdir();pq.write_table(pa.Table.from_pylist([row()]),root/'605001.parquet');p=self.policy(root);one=scan_supplement('605001',root,p);pq.write_table(pa.Table.from_pylist([{**row(),'close':10.6}]),root/'605001.parquet');self.assertNotEqual(one.entry_hash,scan_supplement('605001',root,p).entry_hash)
   run=prepare_supplement_run(p,('605001',),root,workspace_output_root=Path(t)/'out',allowed_output_root=Path(t));(run/'entries'/'605001.json').write_text(json.dumps({'bad':1}),encoding='utf-8')
   with self.assertRaisesRegex(ValueError,'schema'):execute_supplement_run(run,root,p,allowed_output_root=Path(t),max_files_this_run=1)
  with tempfile.TemporaryDirectory() as t:
   import pyarrow as pa,pyarrow.parquet as pq
   root=Path(t)/'补充';root.mkdir();pq.write_table(pa.Table.from_pylist([row()]),root/'605001.parquet');p=self.policy(root);run=prepare_supplement_run(p,('605001',),root,workspace_output_root=Path(t)/'out',allowed_output_root=Path(t));first=execute_supplement_run(run,root,p,allowed_output_root=Path(t),max_files_this_run=1);second=execute_supplement_run(run,root,p,allowed_output_root=Path(t),max_files_this_run=1);self.assertEqual(first,second);self.assertIn('entry_hash',json.loads((run/'entries'/'605001.json').read_text(encoding='utf-8')))
 def test_cli_full_lifecycle_unicode_and_tamper_blocks(self):
  with tempfile.TemporaryDirectory() as t:
   import pyarrow as pa,pyarrow.parquet as pq
   base=Path(t)/'股票模型';src=base/'trend';out=base/'out';supp=base/'补充';src.mkdir(parents=True);supp.mkdir()
   def tr(day,year):return {'date':date(year,1,day),'open':10.,'high':11.,'low':9.,'close':10.5,'volume':100.,'is_st':False}
   for sym,rs in [('000001',[tr(2,2015)]),('000562',[tr(1,1992)]),('601268',[tr(1,1992)])]:pq.write_table(pa.Table.from_pylist(rs),src/(sym+'.parquet'))
   c=make_local_market_contract(source_id='trend',root=str(src),date_column='date',open_column='open',high_column='high',low_column='low',close_column='close',volume_column='volume');snap=build_corpus_snapshot(c,max_files=10);camp=make_campaign_contract(snap,max_rows_per_file=10,max_issues_per_file=10);qr=prepare_quality_run(snap,camp,c,workspace_output_root=out,allowed_output_root=base);execute_quality_run(qr,c,allowed_output_root=base,max_files_this_run=10);agg=aggregate_campaign_reports(snap,camp,c,_read_completed_reports(qr,snap,camp,c));ap=make_admission_policy(c,snapshot_hash=snap.snapshot_hash,campaign_hash=camp.campaign_hash,aggregate_hash=agg.aggregate_hash,research_end=END);ad=decide_range_admission(ap,qr,c,allowed_output_root=base);cr=prepare_content_run(ad,ap,snap,c,workspace_output_root=out,allowed_output_root=base);execute_content_run(cr,snap,ap,c,allowed_output_root=base,max_files_this_run=10,decision=ad)
   pq.write_table(pa.Table.from_pylist([row()]),supp/'605001.parquet');manifest=base/'a.jsonl';items=[record('000001'),record('000562'),record('601268'),record('605001')];items[1]['active_to']='2015-01-26';items[1]['list_status']='D';items[2]['active_to']='2015-05-21';items[2]['list_status']='D';manifest.write_bytes(('\n'.join(json.dumps(x,ensure_ascii=False) for x in items)+'\n').encode());cfg=base/'contract.json';cfg.write_text(json.dumps({'source_id':'trend','root':str(src),'date_column':'date','open_column':'open','high_column':'high','low_column':'low','close_column':'close','volume_column':'volume'}),encoding='utf-8');before=(supp/'605001.parquet').read_bytes()
   content_out=base/'content-runs';content_out.mkdir();cr_target=content_out/cr.name;cr.rename(cr_target);pre=['prepare','--workspace-output-root',str(out),'--allowed-output-root',str(out),'--manifest',str(manifest),'--trend-contract-json',str(cfg),'--quality-run-dir',str(qr),'--quality-allowed-output-root',str(base),'--trend-content-run-dir',str(cr_target),'--trend-content-allowed-output-root',str(content_out),'--supplement-root',str(supp)];badroot=pre.copy();badroot[badroot.index('--trend-content-allowed-output-root')+1]=str(out);self.assertEqual(universe_cli(badroot),2);badescape=pre.copy();badescape[badescape.index('--workspace-output-root')+1]=str(base);self.assertEqual(universe_cli(badescape),2);self.assertEqual(universe_cli(pre),0);run=next(out.glob('mainboard-supplement-*'));common=['--run-dir',str(run),'--allowed-output-root',str(out)];self.assertEqual(universe_cli(['status',*common]),0);self.assertEqual(universe_cli(['execute',*common,'--max-files-this-run','1']),2);self.assertEqual(universe_cli(['execute',*common,'--max-files-this-run','1','--confirm-read-source']),0);self.assertEqual(universe_cli(['status',*common]),0);self.assertEqual(universe_cli(['finalize',*common]),0);self.assertEqual(universe_cli(['finalize',*common]),0);self.assertEqual(before,(supp/'605001.parquet').read_bytes())
   audits=base/'审计';audits.mkdir();audit_prepare=['prepare','--mainboard-run-dir',str(run),'--mainboard-allowed-output-root',str(out),'--trend-content-run-dir',str(cr_target),'--trend-content-allowed-output-root',str(content_out),'--quality-run-dir',str(qr),'--quality-allowed-output-root',str(base),'--trend-contract-json',str(cfg),'--workspace-output-root',str(audits),'--allowed-output-root',str(audits),'--base-round-trip-cost','0.01']
   self.assertEqual(audit_cli(audit_prepare),0);audit_run=next(audits.glob('tradability-audit-*'));audit_common=['--run-dir',str(audit_run),'--allowed-output-root',str(audits)]
   self.assertEqual(audit_cli(['status',*audit_common]),0);self.assertEqual(audit_cli(['execute',*audit_common,'--max-files-this-run','2','--phase','calendar']),2);self.assertEqual(audit_cli(['execute',*audit_common,'--max-files-this-run','2','--phase','calendar','--confirm-read-source']),0);self.assertEqual(audit_cli(['execute',*audit_common,'--max-files-this-run','2','--phase','calendar','--confirm-read-source']),0);self.assertEqual(audit_cli(['execute',*audit_common,'--max-files-this-run','2','--phase','audit','--confirm-read-source']),0);self.assertEqual(audit_cli(['execute',*audit_common,'--max-files-this-run','2','--phase','audit','--confirm-read-source']),0);self.assertEqual(audit_cli(['status',*audit_common]),0);self.assertEqual(audit_cli(['finalize',*audit_common]),0);self.assertEqual(audit_cli(['finalize',*audit_common]),0);self.assertEqual(before,(supp/'605001.parquet').read_bytes())
   for name,key in (('policy.json','manifest_record_count'),('manifest_inventory.json','record_count'),('predecision.json','status')):
    raw=(run/name).read_text(encoding='utf-8');bad=json.loads(raw);bad[key]=999 if key!='status' else 'complete';(run/name).write_text(json.dumps(bad),encoding='utf-8');self.assertEqual(universe_cli(['status',*common]),2);(run/name).write_text(raw,encoding='utf-8')
