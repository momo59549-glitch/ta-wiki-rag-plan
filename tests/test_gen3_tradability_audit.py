from datetime import date
from types import SimpleNamespace
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch
from packages.research.gen3_tradability_audit import *
import packages.research.gen3_tradability_audit as audit_module
from packages.research.gen3_tradability_exploratory import ExploratoryRow,ExploratoryExecutionPolicy
from scripts.run_gen3_tradability_audit import main as audit_cli
from test_gen3_mainboard_universe import Tests as MainboardLifecycleFixture
H=lambda c:'sha256:'+c*64
class T(unittest.TestCase):
 def test_observed_calendar_coverage_only(self):
  c=(date(2020,1,2),date(2020,1,3));r=ExploratoryRow('000001',c[0],10.,10.,10.,10.,0.,True);x=audit_symbol('000001',c,(r,));self.assertEqual((x.observed_sessions,x.zero_volume,x.st_rows,x.one_price_boards,x.missing_observed_sessions),(1,1,1,1,1))
 def test_policy_nonadjudicable(self):
  p=make_policy(H('a'),H('b'),H('c'),H('d'),H('e'),date(2015,1,1),date(2026,8,5),ExploratoryExecutionPolicy(.01));p.verify();self.assertTrue(p.nonadjudicable)
 def test_cli_requires_frozen_mainboard_not_manual_symbols(self):
  self.assertNotIn('symbols',audit_cli.__code__.co_varnames)

 def _state(self):
  p=make_policy(H('a'),H('b'),H('c'),H('d'),H('e'),date(2015,1,1),date(2026,8,5),ExploratoryExecutionPolicy(.01));entry=SimpleNamespace(symbol='000001',entry_hash=H('9'));return p,SimpleNamespace(members=('000001',),trend_entries=(entry,),supplement_entries=(),universe_hash=H('c'))
 def _calendar(self,p,day=date(2020,1,2)):
  b=CalendarReport(p.policy_hash,'000001','trend',H('9'),(day,),H('0'),H('0'));x=CalendarReport(**{**b.__dict__,'sessions_hash':h({'symbol':'000001','sessions':[day.isoformat()]})});return CalendarReport(**{**x.__dict__,'report_hash':h(x.payload())})
 def _files(self,run,p,cal,report=None):
  (run/'calendar_reports').mkdir();(run/'reports').mkdir();(run/'calendar_reports'/'000001.json').write_bytes(j(cal.payload()|{'report_hash':cal.report_hash}));obs=audit_module._rebuild_observed_calendar(p,{'000001':cal});(run/'observed_calendar.json').write_bytes(j(obs.payload()|{'calendar_hash':obs.calendar_hash}))
  if report:(run/'reports'/'000001.json').write_bytes(j(report.payload()|{'report_hash':report.report_hash}))
  return obs
 def test_explicit_unicode_cli_two_phase_lifecycle(self):
  # The reusable fixture creates temporary Unicode Parquet roots and performs
  # calendar two-batch, union, audit two-batch, idempotent finalization.
  MainboardLifecycleFixture.test_cli_full_lifecycle_unicode_and_tamper_blocks(self)
 def test_status_never_reads_parquet_and_union_self_tamper_rejects(self):
  with tempfile.TemporaryDirectory() as t:
   run=Path(t)/'审计';run.mkdir();p,state=self._state();cal=self._calendar(p);obs=self._files(run,p,cal)
   with patch('packages.research.gen3_tradability_audit.load_frozen_audit_run_state',return_value=(run,p,ExploratoryExecutionPolicy(.01),state)),patch('packages.research.gen3_tradability_audit._rows',side_effect=AssertionError('no parquet')):
    self.assertEqual(audit_status(run,Path(t))['status'],'audit_accumulating')
   bad=ObservedCalendar(p.policy_hash,(date(2020,1,2),date(2020,1,3)),H('0'));bad=ObservedCalendar(**{**bad.__dict__,'calendar_hash':h(bad.payload())});(run/'observed_calendar.json').write_bytes(j(bad.payload()|{'calendar_hash':bad.calendar_hash}))
   with patch('packages.research.gen3_tradability_audit.load_frozen_audit_run_state',return_value=(run,p,ExploratoryExecutionPolicy(.01),state)):
    with self.assertRaisesRegex(ValueError,'union'):audit_status(run,Path(t))
 def test_calendar_self_tamper_is_rechecked_before_audit(self):
  with tempfile.TemporaryDirectory() as t:
   run=Path(t)/'审计';run.mkdir();p,state=self._state();cal=self._calendar(p,date(2020,1,3));self._files(run,p,cal)
   row=ExploratoryRow('000001',date(2020,1,2),10.,11.,9.,10.5,1.,False)
   with patch('packages.research.gen3_tradability_audit.load_frozen_audit_run_state',return_value=(run,p,ExploratoryExecutionPolicy(.01),state)),patch('packages.research.gen3_tradability_audit._rows',return_value=((row,),'trend',H('9'))):
    with self.assertRaisesRegex(ValueError,'calendar report'):execute_frozen_audit_run(run,Path(t),max_files_this_run=1,phase='audit')
 def test_feasibility_source_attribution_lock_tmp_and_unknown_artifact_reject(self):
  with tempfile.TemporaryDirectory() as t:
   run=Path(t)/'审计';run.mkdir();p,state=self._state();cal=self._calendar(p);obs=self._files(run,p,cal);f=audit_symbol('000001',obs.sessions,(ExploratoryRow('000001',date(2020,1,2),10.,11.,9.,10.5,1.,False),),obs.calendar_hash);b=FrozenAuditReport(p.policy_hash,state.universe_hash,'000001','supplement',H('8'),f,H('0'));bad=FrozenAuditReport(**{**b.__dict__,'report_hash':h(b.payload())});(run/'reports'/'000001.json').write_bytes(j(bad.payload()|{'report_hash':bad.report_hash}))
   with patch('packages.research.gen3_tradability_audit.load_frozen_audit_run_state',return_value=(run,p,ExploratoryExecutionPolicy(.01),state)):
    with self.assertRaisesRegex(ValueError,'attribution'):audit_status(run,Path(t))
   (run/'reports'/'000001.json').unlink();(run/'reports'/'orphan.tmp').write_bytes(b'x')
   with patch('packages.research.gen3_tradability_audit.load_frozen_audit_run_state',return_value=(run,p,ExploratoryExecutionPolicy(.01),state)):
    with self.assertRaisesRegex(ValueError,'directory'):audit_status(run,Path(t))
 def test_policy_dates_and_lock_are_fail_closed(self):
  p,state=self._state();bad=ExploratoryCorpusPolicy(p.mainboard_policy_hash,p.predecision_hash,p.universe_hash,p.trend_content_hash,p.supplement_hash,date(2015,1,2),p.research_end,p.fallback_policy_hash,p.adapter_identity,'sha256:'+'0'*64);bad=ExploratoryCorpusPolicy(**{**bad.__dict__,'policy_hash':h(bad.payload())})
  with self.assertRaises(ValueError):bad.verify()
  with tempfile.TemporaryDirectory() as t:
   run=Path(t)/'审计';run.mkdir();cal=self._calendar(p);self._files(run,p,cal);(run/'.lock').write_bytes(j({'bad':1}))
   with patch('packages.research.gen3_tradability_audit.load_frozen_audit_run_state',return_value=(run,p,ExploratoryExecutionPolicy(.01),state)):
    with self.assertRaisesRegex(ValueError,'lock'):audit_status(run,Path(t))
