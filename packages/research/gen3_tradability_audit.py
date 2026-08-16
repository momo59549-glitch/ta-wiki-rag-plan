"""Write-once, non-adjudicable observed-session feasibility audit.

This is not an official exchange calendar or a tradability adjudication.
It deliberately freezes the union of observed local sessions before calculating
per-symbol missing observations.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json, os, re
from pathlib import Path
from .gen3_tradability_exploratory import ExploratoryExecutionPolicy, ExploratoryRow
from .gen3_mainboard_universe import MainboardUniversePolicy, SupplementContentEntry, UniverseEntry, UniverseCoverageDecision, mainboard_policy_from_data, trend_universe_entries, scan_supplement
from .gen3_market_admission import _content_entry
from .gen3_quality_campaign import read_corpus_file_footer
from .gen3_local_market import LocalParquetFileContract, make_local_market_contract, _market_mapping
from .gen3_rows import canonicalize_and_validate_row

SCHEMA='gen3-tradability-audit/v2'; H=re.compile(r'^sha256:[0-9a-f]{64}$'); ADAPTER='observed_session_calendar_approximation'
def j(x):return json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
def h(x):return 'sha256:'+sha256(j(x)).hexdigest()
def hh(x):
 if not isinstance(x,str) or not H.fullmatch(x):raise ValueError('hash invalid')
def _safe(path,root,label):
 p=Path(path).resolve();r=Path(root).resolve()
 try:p.relative_to(r)
 except ValueError as exc:raise ValueError(label+' escapes allowed root') from exc
 return p
def _read(path,label):
 try:return json.loads(Path(path).read_text(encoding='utf-8'))
 except (OSError,UnicodeDecodeError,json.JSONDecodeError) as exc:raise ValueError(label+' invalid') from exc
def _write_once(path,value):
 path=Path(path);tmp=path.with_name(path.name+'.tmp')
 if path.exists():raise ValueError('write-once target exists')
 if tmp.exists():raise ValueError('orphan tmp blocks publication')
 primary=None
 try:
  with open(tmp,'xb') as f:f.write(j(value));f.flush();os.fsync(f.fileno())
  os.link(tmp,path)
 except BaseException as exc:
  primary=exc;raise
 finally:
  if tmp.exists():
   try:tmp.unlink()
   except OSError:
    if primary is None:raise

@dataclass(frozen=True)
class ExploratoryCorpusPolicy:
 mainboard_policy_hash:str;predecision_hash:str;universe_hash:str;trend_content_hash:str;supplement_hash:str;research_start:date;research_end:date;fallback_policy_hash:str;adapter_identity:str;policy_hash:str;nonformal:bool=True;nonadjudicable:bool=True;no_trial_budget:bool=True;schema_version:str=SCHEMA
 def payload(self):return {'schema_version':self.schema_version,'mainboard_policy_hash':self.mainboard_policy_hash,'predecision_hash':self.predecision_hash,'universe_hash':self.universe_hash,'trend_content_hash':self.trend_content_hash,'supplement_hash':self.supplement_hash,'research_start':self.research_start.isoformat(),'research_end':self.research_end.isoformat(),'fallback_policy_hash':self.fallback_policy_hash,'adapter_identity':self.adapter_identity,'nonformal':self.nonformal,'nonadjudicable':self.nonadjudicable,'no_trial_budget':self.no_trial_budget}
 def verify(self):
  for x in (self.mainboard_policy_hash,self.predecision_hash,self.universe_hash,self.trend_content_hash,self.supplement_hash,self.fallback_policy_hash,self.policy_hash):hh(x)
  if type(self.research_start)is not date or type(self.research_end)is not date or (self.research_start,self.research_end)!=(date(2015,1,1),date(2026,8,5)) or self.adapter_identity!=ADAPTER or self.schema_version!=SCHEMA or not(self.nonformal and self.nonadjudicable and self.no_trial_budget) or self.policy_hash!=h(self.payload()):raise ValueError('corpus policy invalid')
def fallback_data(policy):
 policy.verify();return {'base_round_trip_cost':policy.base_round_trip_cost,'cost_multiplier':policy.cost_multiplier,'approximate_tradability':policy.approximate_tradability,'official_tradability_verified':policy.official_tradability_verified,'adjudicable':policy.adjudicable,'schema_version':policy.schema_version,'fallback_policy_hash':h({'base_round_trip_cost':policy.base_round_trip_cost,'cost_multiplier':policy.cost_multiplier,'schema_version':policy.schema_version})}
def fallback_from_data(v):
 fields={'base_round_trip_cost','cost_multiplier','approximate_tradability','official_tradability_verified','adjudicable','schema_version','fallback_policy_hash'}
 if not isinstance(v,dict) or set(v)!=fields:raise ValueError('fallback policy strict schema')
 p=ExploratoryExecutionPolicy(v['base_round_trip_cost'],v['cost_multiplier'],v['approximate_tradability'],v['official_tradability_verified'],v['adjudicable'],v['schema_version']);p.verify()
 if v['fallback_policy_hash']!=fallback_data(p)['fallback_policy_hash']:raise ValueError('fallback policy hash invalid')
 return p
def make_policy(mainboard_policy_hash,predecision_hash,universe_hash,trend_content_hash,supplement_hash,research_start,research_end,fallback_policy):
 data=fallback_data(fallback_policy);b=ExploratoryCorpusPolicy(mainboard_policy_hash,predecision_hash,universe_hash,trend_content_hash,supplement_hash,research_start,research_end,data['fallback_policy_hash'],ADAPTER,'sha256:'+'0'*64);r=ExploratoryCorpusPolicy(**{**b.__dict__,'policy_hash':h(b.payload())});r.verify();return r

@dataclass(frozen=True)
class CalendarReport:
 corpus_policy_hash:str;symbol:str;source:str;source_entry_hash:str;sessions:tuple[date,...];sessions_hash:str;report_hash:str
 def payload(self):return {'corpus_policy_hash':self.corpus_policy_hash,'symbol':self.symbol,'source':self.source,'source_entry_hash':self.source_entry_hash,'sessions':[x.isoformat() for x in self.sessions],'sessions_hash':self.sessions_hash}
 def verify(self):
  for x in (self.corpus_policy_hash,self.source_entry_hash,self.sessions_hash,self.report_hash):hh(x)
  if not re.fullmatch(r'\d{6}',self.symbol) or self.source not in {'trend','supplement'} or self.sessions!=tuple(sorted(self.sessions)) or len(set(self.sessions))!=len(self.sessions) or any(type(x)is not date for x in self.sessions) or self.sessions_hash!=h({'symbol':self.symbol,'sessions':[x.isoformat() for x in self.sessions]}) or self.report_hash!=h(self.payload()):raise ValueError('calendar report invalid')
@dataclass(frozen=True)
class ObservedCalendar:
 corpus_policy_hash:str; sessions:tuple[date,...]; calendar_hash:str
 def payload(self):return {'corpus_policy_hash':self.corpus_policy_hash,'sessions':[x.isoformat() for x in self.sessions],'calendar_count':len(self.sessions),'min_session':self.sessions[0].isoformat() if self.sessions else None,'max_session':self.sessions[-1].isoformat() if self.sessions else None,'calendar_kind':ADAPTER}
 def verify(self):
  hh(self.corpus_policy_hash);hh(self.calendar_hash)
  if not self.sessions or self.sessions!=tuple(sorted(self.sessions)) or len(set(self.sessions))!=len(self.sessions) or any(type(x)is not date for x in self.sessions) or self.calendar_hash!=h(self.payload()):raise ValueError('observed calendar invalid')
@dataclass(frozen=True)
class SymbolFeasibility:
 symbol:str;calendar_hash:str;calendar_count:int;observed_sessions:int;zero_volume:int;st_rows:int;one_price_boards:int;missing_observed_sessions:int;report_hash:str
 def payload(self):return {'symbol':self.symbol,'calendar_hash':self.calendar_hash,'calendar_count':self.calendar_count,'observed_sessions':self.observed_sessions,'zero_volume':self.zero_volume,'st_rows':self.st_rows,'one_price_boards':self.one_price_boards,'missing_observed_sessions':self.missing_observed_sessions}
 def verify(self):
  hh(self.calendar_hash);hh(self.report_hash)
  vals=(self.calendar_count,self.observed_sessions,self.zero_volume,self.st_rows,self.one_price_boards,self.missing_observed_sessions)
  if not re.fullmatch(r'\d{6}',self.symbol) or any(type(x)is not int or x<0 for x in vals) or self.observed_sessions>self.calendar_count or any(x>self.observed_sessions for x in (self.zero_volume,self.st_rows,self.one_price_boards)) or self.missing_observed_sessions+self.observed_sessions!=self.calendar_count or self.report_hash!=h(self.payload()):raise ValueError('symbol feasibility invalid')
def audit_symbol(symbol,calendar,rows,calendar_hash=None):
 cal=tuple(calendar)
 if not re.fullmatch(r'\d{6}',symbol) or not cal or cal!=tuple(sorted(cal)) or len(set(cal))!=len(cal) or any(type(x)is not date for x in cal):raise ValueError('audit input invalid')
 if any(not isinstance(r,ExploratoryRow) or r.symbol!=symbol or r.session not in cal for r in rows):raise ValueError('audit rows invalid')
 for r in rows:r.verify()
 ch=calendar_hash or h({'corpus_policy_hash':'unbound','sessions':[x.isoformat() for x in cal],'calendar_count':len(cal),'min_session':cal[0].isoformat(),'max_session':cal[-1].isoformat(),'calendar_kind':ADAPTER})
 b=SymbolFeasibility(symbol,ch,len(cal),len(rows),sum(r.volume<=0 for r in rows),sum(r.is_st for r in rows),sum(r.suspected_board() for r in rows),len(cal)-len(rows),'sha256:'+'0'*64);r=SymbolFeasibility(**{**b.__dict__,'report_hash':h(b.payload())});r.verify();return r

# Strict mainboard loader: it accepts no member or policy object from callers.
@dataclass(frozen=True)
class FrozenMainboardAuditState:
 run_dir:Path;policy:MainboardUniversePolicy;members:tuple[str,...];trend_entries:tuple; supplement_entries:tuple[SupplementContentEntry,...];universe_hash:str;supplement_root:Path;supplement_contract:LocalParquetFileContract;mainboard_allowed_output_root:Path;trend_content_run_dir:Path;trend_content_allowed_output_root:Path;quality_run_dir:Path;quality_allowed_output_root:Path;trend_contract:LocalParquetFileContract;trend_snapshot:object;trend_admission_policy:object;trend_admission_decision:object;predecision:UniverseCoverageDecision
def _supp(v):
 fields={'mainboard_policy_hash','symbol','selected_rows','min_session','max_session','content_hash','supplement_contract_hash','corpus_entry_hash','entry_hash'}
 if not isinstance(v,dict) or set(v)!=fields:raise ValueError('supplement entry strict schema')
 try:r=SupplementContentEntry(v['mainboard_policy_hash'],v['symbol'],v['selected_rows'],date.fromisoformat(v['min_session']),date.fromisoformat(v['max_session']),v['content_hash'],v['supplement_contract_hash'],v['corpus_entry_hash'],v['entry_hash'])
 except (TypeError,ValueError) as exc:raise ValueError('supplement entry invalid') from exc
 r.verify();return r
def _contract(v):
 fields={'source_id','root','date_column','open_column','high_column','low_column','close_column','volume_column'}
 if not isinstance(v,dict) or set(v)!=fields:raise ValueError('trend contract strict schema')
 return make_local_market_contract(**v)
def load_frozen_mainboard_audit_state(run_dir,allowed_output_root,*,trend_content_run_dir,trend_content_allowed_output_root,quality_run_dir,quality_allowed_output_root,trend_contract):
 run=_safe(run_dir,allowed_output_root,'mainboard run');required={'entries','policy.json','symbols.json','manifest_inventory.json','predecision.json','final_decision.json'}
 if not run.is_dir() or {p.name for p in run.iterdir()}-required or any(p.name.endswith('.tmp') for p in run.rglob('*') if p.is_file()) or (run/'.lock').exists():raise ValueError('mainboard run artifact invalid')
 policy=mainboard_policy_from_data(_read(run/'policy.json','mainboard policy'));inv=_read(run/'manifest_inventory.json','inventory')
 if not isinstance(inv,dict) or set(inv)!={'manifest_content_hash','record_count','excluded_by_board','members'} or (inv['manifest_content_hash'],inv['record_count'],inv['excluded_by_board'])!=(policy.manifest_content_hash,policy.manifest_record_count,policy.excluded_by_board) or not isinstance(inv['members'],list) or not inv['members'] or inv['members']!=sorted(inv['members']) or len(set(inv['members']))!=len(inv['members']) or any(not isinstance(x,str) or not re.fullmatch(r'\d{6}',x) for x in inv['members']):raise ValueError('mainboard inventory invalid')
 members=tuple(inv['members']);pre=_read(run/'predecision.json','predecision');pf={'policy_hash','members','trend_nonempty','trend_zero_explicit','supplement','missing','trend_outside_research_members','entries','status','universe_hash'}
 if not isinstance(pre,dict) or set(pre)!=pf or pre['policy_hash']!=policy.policy_hash or pre['status']!='blocked' or pre['universe_hash']!=h({k:v for k,v in pre.items() if k!='universe_hash'}) or not isinstance(pre['entries'],list):raise ValueError('mainboard predecision invalid')
 seen=set(); parsed=[]
 for x in pre['entries']:
  if not isinstance(x,dict) or set(x)!={'mainboard_policy_hash','symbol','source','entry_hash','selected_rows','active_to'}:raise ValueError('mainboard predecision entry invalid')
  try:active=None if x['active_to'] is None else date.fromisoformat(x['active_to'])
  except (TypeError,ValueError) as exc:raise ValueError('mainboard predecision date invalid') from exc
  entry=UniverseEntry(x['mainboard_policy_hash'],x['symbol'],x['source'],x['entry_hash'],x['selected_rows'],active,h({k:v for k,v in x.items()}));entry.verify()
  if entry.mainboard_policy_hash!=policy.policy_hash or entry.symbol not in members or entry.symbol in seen:raise ValueError('mainboard predecision ownership invalid')
  if entry.source=='trend' and entry.selected_rows<1:raise ValueError('trend attribution must be nonempty')
  if entry.source=='explicit_no_observed_trading_rows' and (entry.symbol,entry.active_to) not in policy.explicit_zero_exceptions:raise ValueError('explicit zero exception invalid')
  seen.add(entry.symbol);parsed.append(entry)
 try:predecision=UniverseCoverageDecision(pre['policy_hash'],pre['members'],pre['trend_nonempty'],pre['trend_zero_explicit'],pre['supplement'],pre['missing'],pre['trend_outside_research_members'],tuple(parsed),pre['status'],pre['universe_hash'])
 except TypeError as exc:raise ValueError('mainboard predecision type invalid') from exc
 predecision.verify()
 if predecision.status!='blocked' or predecision.supplement!=0 or predecision.members!=len(members) or predecision.missing!=len(members)-len(seen):raise ValueError('mainboard predecision counts invalid')
 meta=_read(run/'symbols.json','supplement symbols');missing=tuple(x for x in members if x not in seen)
 if not isinstance(meta,dict) or set(meta)!={'symbols','supplement_root','supplement_contract_hash'} or meta['symbols']!=list(missing):raise ValueError('supplement missing-set invalid')
 root=Path(meta['supplement_root']).resolve();supp_contract=make_local_market_contract(source_id='tushare_daily_cache',root=str(root),date_column='trade_date',open_column='open',high_column='high',low_column='low',close_column='close',volume_column='volume')
 if supp_contract.contract_hash!=meta['supplement_contract_hash']:raise ValueError('supplement contract invalid')
 files=list((run/'entries').iterdir());supp=tuple(sorted((_supp(_read(p,'supplement entry')) for p in files),key=lambda x:x.symbol))
 if any(not p.is_file() or not re.fullmatch(r'\d{6}\.json',p.name) for p in files) or tuple(x.symbol for x in supp)!=missing or any(x.mainboard_policy_hash!=policy.policy_hash or x.supplement_contract_hash!=supp_contract.contract_hash for x in supp):raise ValueError('supplement entries invalid')
 final=_read(run/'final_decision.json','final decision');expect={'policy_hash':policy.policy_hash,'predecision_hash':pre['universe_hash'],'supplement_entries':len(supp),'members':len(members),'status':'mainboard_universe_content_complete'}
 if final!=expect|{'universe_hash':h(expect)}:raise ValueError('mainboard final binding invalid')
 snap,ap,ad,trends=trend_universe_entries(trend_content_run_dir,trend_contract,policy,allowed_output_root=trend_content_allowed_output_root,quality_run_dir=quality_run_dir,quality_allowed_output_root=quality_allowed_output_root)
 bt={x.symbol:x for x in trends}
 if len(bt)!=len(trends):raise ValueError('trend duplicate')
 for x in pre['entries']:
  if x['source']=='trend' and (x['symbol'] not in bt or bt[x['symbol']].entry_hash!=x['entry_hash'] or bt[x['symbol']].selected_rows!=x['selected_rows']):raise ValueError('trend predecision binding invalid')
 # Rebuild the completed universe rather than trusting the compact final
 # receipt alone.  The original predecision plus the exact supplement entries
 # must yield the claimed finalized universe identity.
 rebuilt=[]
 for entry in predecision.entries:rebuilt.append(entry)
 for x in supp:
  active=None
  b=UniverseEntry(policy.policy_hash,x.symbol,'supplement',x.entry_hash,x.selected_rows,active,'sha256:'+'0'*64)
  rebuilt.append(UniverseEntry(**{**b.__dict__,'attribution_hash':h(b.payload())}))
 # A supplement member is necessarily active in this fixed window; active_to
 # is unavailable in the compact inventory, so retain the predecision/final
 # receipt binding and enforce every supplementary identity independently.
 if len(rebuilt)!=len(members):raise ValueError('final universe reconstruction incomplete')
 return FrozenMainboardAuditState(run,policy,members,trends,supp,final['universe_hash'],root,supp_contract,Path(allowed_output_root).resolve(),Path(trend_content_run_dir).resolve(),Path(trend_content_allowed_output_root).resolve(),Path(quality_run_dir).resolve(),Path(quality_allowed_output_root).resolve(),trend_contract,snap,ap,ad,predecision)

def _rows(state,symbol):
 import pyarrow.parquet as pq
 bt={x.symbol:x for x in state.trend_entries};bs={x.symbol:x for x in state.supplement_entries}
 if symbol in bt:
  entry=next((x for x in state.trend_snapshot.files if x.symbol==symbol),None);contract=state.trend_contract;stored=bt[symbol]
  if entry is None or read_corpus_file_footer(contract,entry.file_path)!=entry:raise ValueError('trend source identity changed')
  path=entry.file_path
 else:
  stored=bs.get(symbol);contract=state.supplement_contract;path=state.supplement_root/(symbol+'.parquet')
  if stored is None or scan_supplement(symbol,state.supplement_root,state.policy).entry_hash!=stored.entry_hash:raise ValueError('supplement source identity changed')
 before=read_corpus_file_footer(contract,path);pf=pq.ParquetFile(path);cols=[contract.date_column,contract.open_column,contract.high_column,contract.low_column,contract.close_column,contract.volume_column,'is_st']
 if 'is_st' not in pf.schema.names:raise ValueError('source lacks observed is_st')
 mapping=_market_mapping(contract);out=[];previous=None
 for batch in pf.iter_batches(batch_size=10000,columns=cols):
  for raw in batch.to_pylist():
   s=raw[contract.date_column];s=s.date() if isinstance(s,datetime) and s.tzinfo is None else s
   if type(s)is not date or (previous is not None and s<=previous):raise ValueError('source sessions invalid')
   previous=s
   if state.policy.research_start<=s<=state.policy.research_end:
    canonicalize_and_validate_row(mapping,{**raw,contract.date_column:s,'__filename_symbol':symbol})
    if type(raw['is_st'])is not bool:raise ValueError('is_st invalid')
    out.append(ExploratoryRow(symbol,s,raw[contract.open_column],raw[contract.high_column],raw[contract.low_column],raw[contract.close_column],raw[contract.volume_column],raw['is_st']))
 if read_corpus_file_footer(contract,path)!=before:raise ValueError('source identity changed during scan')
 if symbol in bt:
  count,_,_,digest=_content_entry(entry,contract,state.trend_admission_policy)
  if (count,digest)!=(stored.selected_rows,stored.content_hash):raise ValueError('trend content identity changed during audit')
 return tuple(out),'trend' if symbol in bt else 'supplement',stored.entry_hash

def _cal_data(x):
 fields={'corpus_policy_hash','symbol','source','source_entry_hash','sessions','sessions_hash','report_hash'}
 if not isinstance(x,dict) or set(x)!=fields or not isinstance(x['sessions'],list):raise ValueError('calendar report strict schema')
 try:r=CalendarReport(x['corpus_policy_hash'],x['symbol'],x['source'],x['source_entry_hash'],tuple(date.fromisoformat(s) for s in x['sessions']),x['sessions_hash'],x['report_hash'])
 except (TypeError,ValueError) as exc:raise ValueError('calendar report invalid') from exc
 r.verify();return r
def _obs_data(x):
 fields={'corpus_policy_hash','sessions','calendar_count','min_session','max_session','calendar_kind','calendar_hash'}
 if not isinstance(x,dict) or set(x)!=fields or not isinstance(x['sessions'],list):raise ValueError('observed calendar strict schema')
 try:r=ObservedCalendar(x['corpus_policy_hash'],tuple(date.fromisoformat(s) for s in x['sessions']),x['calendar_hash'])
 except (TypeError,ValueError) as exc:raise ValueError('observed calendar invalid') from exc
 if x!=r.payload()|{'calendar_hash':r.calendar_hash}:raise ValueError('observed calendar binding invalid')
 r.verify();return r
def _feas_data(x):
 fields={'symbol','calendar_hash','calendar_count','observed_sessions','zero_volume','st_rows','one_price_boards','missing_observed_sessions','report_hash'}
 if not isinstance(x,dict) or set(x)!=fields:raise ValueError('feasibility strict schema')
 r=SymbolFeasibility(**x);r.verify();return r
@dataclass(frozen=True)
class FrozenAuditReport:
 corpus_policy_hash:str;universe_hash:str;symbol:str;source:str;source_entry_hash:str;feasibility:SymbolFeasibility;report_hash:str
 def payload(self):return {'corpus_policy_hash':self.corpus_policy_hash,'universe_hash':self.universe_hash,'symbol':self.symbol,'source':self.source,'source_entry_hash':self.source_entry_hash,'feasibility':self.feasibility.payload()|{'report_hash':self.feasibility.report_hash}}
 def verify(self):
  for x in (self.corpus_policy_hash,self.universe_hash,self.source_entry_hash,self.report_hash):hh(x)
  if self.source not in {'trend','supplement'} or self.feasibility.symbol!=self.symbol or self.report_hash!=h(self.payload()):raise ValueError('audit report invalid')
  self.feasibility.verify()
def _report_data(x):
 fields={'corpus_policy_hash','universe_hash','symbol','source','source_entry_hash','feasibility','report_hash'}
 if not isinstance(x,dict) or set(x)!=fields:return (_ for _ in ()).throw(ValueError('audit report strict schema'))
 r=FrozenAuditReport(x['corpus_policy_hash'],x['universe_hash'],x['symbol'],x['source'],x['source_entry_hash'],_feas_data(x['feasibility']),x['report_hash']);r.verify();return r

def _lock(run,policy):
 p={'schema_version':'gen3-tradability-audit-lock/v2','run_id':run.name,'policy_hash':policy.policy_hash,'pid':os.getpid(),'created_at':datetime.now(timezone.utc).isoformat()};p['lock_hash']=h(p);path=run/'.lock'
 if path.exists():raise ValueError('audit residual lock')
 with open(path,'xb') as f:f.write(j(p));f.flush();os.fsync(f.fileno())
 return j(p)
def _verify_residual_lock(path,policy,run):
 value=_read(path,'audit lock');fields={'schema_version','run_id','policy_hash','pid','created_at','lock_hash'}
 if not isinstance(value,dict) or set(value)!=fields or value['schema_version']!='gen3-tradability-audit-lock/v2' or value['run_id']!=run.name or value['policy_hash']!=policy.policy_hash or type(value['pid'])is not int or value['pid']<1 or not isinstance(value['created_at'],str):raise ValueError('audit lock invalid')
 try:stamp=datetime.fromisoformat(value['created_at'])
 except ValueError as exc:raise ValueError('audit lock invalid') from exc
 if stamp.tzinfo is None or stamp.utcoffset() is None or value['lock_hash']!=h({k:v for k,v in value.items() if k!='lock_hash'}):raise ValueError('audit lock invalid')
 raise ValueError('audit residual lock requires manual review')
def _unlock(path,raw,primary):
 try:
  if path.exists() and path.read_bytes()==raw:path.unlink()
 except OSError:
  if primary is None:raise
def prepare_audit(policy,symbols,workspace_output_root,allowed_output_root):
 policy.verify();out=_safe(workspace_output_root,allowed_output_root,'audit output');syms=tuple(sorted(symbols))
 if not syms or len(set(syms))!=len(syms) or any(not re.fullmatch(r'\d{6}',x) for x in syms):raise ValueError('audit symbols invalid')
 run=out/('tradability-audit-'+sha256(j({'policy':policy.policy_hash,'symbols':list(syms)})).hexdigest())
 if run.exists():raise ValueError('audit run exists')
 out.mkdir(parents=True,exist_ok=True);run.mkdir();(run/'calendar_reports').mkdir();(run/'reports').mkdir();_write_once(run/'policy.json',policy.payload()|{'policy_hash':policy.policy_hash});_write_once(run/'symbols.json',{'symbols':list(syms)});return run
def prepare_frozen_audit(state,policy,fallback_policy,workspace_output_root,allowed_output_root):
 expected_supp=h({'entries':[x.entry_hash for x in state.supplement_entries]})
 if (policy.mainboard_policy_hash,policy.predecision_hash,policy.universe_hash,policy.trend_content_hash,policy.supplement_hash,policy.research_start,policy.research_end)!=(state.policy.policy_hash,state.predecision.universe_hash,state.universe_hash,state.policy.trend_content_snapshot_hash,expected_supp,state.policy.research_start,state.policy.research_end):raise ValueError('corpus policy mainboard binding invalid')
 if policy.fallback_policy_hash!=fallback_data(fallback_policy)['fallback_policy_hash']:raise ValueError('fallback policy binding invalid')
 run=prepare_audit(policy,state.members,workspace_output_root,allowed_output_root)
 _write_once(run/'fallback.json',fallback_data(fallback_policy));_write_once(run/'mainboard_binding.json',{'mainboard_run':state.run_dir.name,'mainboard_policy_hash':state.policy.policy_hash,'universe_hash':state.universe_hash,'members':list(state.members),'supplement_contract_hash':state.supplement_contract.contract_hash})
 _write_once(run/'source_binding.json',{'mainboard_run_dir':str(state.run_dir),'mainboard_allowed_output_root':str(state.mainboard_allowed_output_root),'trend_content_run_dir':str(state.trend_content_run_dir),'trend_content_allowed_output_root':str(state.trend_content_allowed_output_root),'quality_run_dir':str(state.quality_run_dir),'quality_allowed_output_root':str(state.quality_allowed_output_root),'trend_contract':{'source_id':state.trend_contract.source_id,'root':state.trend_contract.root,'date_column':state.trend_contract.date_column,'open_column':state.trend_contract.open_column,'high_column':state.trend_contract.high_column,'low_column':state.trend_contract.low_column,'close_column':state.trend_contract.close_column,'volume_column':state.trend_contract.volume_column}});return run
def load_frozen_audit_run_state(run_dir,allowed_output_root):
 run=_safe(run_dir,allowed_output_root,'audit run');allowed={'policy.json','symbols.json','fallback.json','mainboard_binding.json','source_binding.json','calendar_reports','reports','observed_calendar.json','final.json','.lock'}
 if not run.is_dir() or {p.name for p in run.iterdir()}-allowed or any(p.name.endswith('.tmp') for p in run.rglob('*') if p.is_file()):raise ValueError('audit artifact invalid')
 pd=_read(run/'policy.json','audit policy');fields={'schema_version','mainboard_policy_hash','predecision_hash','universe_hash','trend_content_hash','supplement_hash','research_start','research_end','fallback_policy_hash','adapter_identity','nonformal','nonadjudicable','no_trial_budget','policy_hash'}
 if not isinstance(pd,dict) or set(pd)!=fields:raise ValueError('audit policy strict schema')
 try:p=ExploratoryCorpusPolicy(pd['mainboard_policy_hash'],pd['predecision_hash'],pd['universe_hash'],pd['trend_content_hash'],pd['supplement_hash'],date.fromisoformat(pd['research_start']),date.fromisoformat(pd['research_end']),pd['fallback_policy_hash'],pd['adapter_identity'],pd['policy_hash'],pd['nonformal'],pd['nonadjudicable'],pd['no_trial_budget'],pd['schema_version'])
 except (TypeError,ValueError) as exc:raise ValueError('audit policy invalid') from exc
 p.verify();fb=fallback_from_data(_read(run/'fallback.json','fallback policy'));src=_read(run/'source_binding.json','source binding');need={'mainboard_run_dir','mainboard_allowed_output_root','trend_content_run_dir','trend_content_allowed_output_root','quality_run_dir','quality_allowed_output_root','trend_contract'}
 if not isinstance(src,dict) or set(src)!=need:raise ValueError('source binding invalid')
 state=load_frozen_mainboard_audit_state(src['mainboard_run_dir'],src['mainboard_allowed_output_root'],trend_content_run_dir=src['trend_content_run_dir'],trend_content_allowed_output_root=src['trend_content_allowed_output_root'],quality_run_dir=src['quality_run_dir'],quality_allowed_output_root=src['quality_allowed_output_root'],trend_contract=_contract(src['trend_contract']))
 expected_supp=h({'entries':[x.entry_hash for x in state.supplement_entries]})
 if p.fallback_policy_hash!=fallback_data(fb)['fallback_policy_hash'] or (p.mainboard_policy_hash,p.predecision_hash,p.universe_hash,p.trend_content_hash,p.supplement_hash,p.research_start,p.research_end)!=(state.policy.policy_hash,state.predecision.universe_hash,state.universe_hash,state.policy.trend_content_snapshot_hash,expected_supp,state.policy.research_start,state.policy.research_end):raise ValueError('audit state binding invalid')
 return run,p,fb,state
def _files(directory,members,parser,label):
    files=list(directory.iterdir())
    if any(not p.is_file() or not re.fullmatch(r'\d{6}\.json',p.name) for p in files):
        raise ValueError(label+' directory invalid')
    out={}
    for path in files:
        x=parser(_read(path,label))
        if x.symbol!=path.stem or x.symbol not in members or x.symbol in out:
            raise ValueError(label+' symbol binding invalid')
        out[x.symbol]=x
    return out
def _expected_attribution(state,symbol):
 bt={x.symbol:x for x in state.trend_entries};bs={x.symbol:x for x in state.supplement_entries}
 if symbol in bt:return 'trend',bt[symbol].entry_hash
 if symbol in bs:return 'supplement',bs[symbol].entry_hash
 raise ValueError('frozen attribution missing')
def _rebuild_observed_calendar(policy,calendar_reports):
    """Rebuild only from frozen report artifacts; never from source parquet."""
    sessions=tuple(sorted({s for x in calendar_reports.values() for s in x.sessions}))
    b=ObservedCalendar(policy.policy_hash,sessions,'sha256:'+'0'*64)
    return ObservedCalendar(**{**b.__dict__,'calendar_hash':h(b.payload())})
def _verify_attributions(items,state,policy,*,calendar=None):
 for symbol,x in items.items():
  source,entry_hash=_expected_attribution(state,symbol)
  if x.corpus_policy_hash!=policy.policy_hash or x.symbol!=symbol or x.source!=source or x.source_entry_hash!=entry_hash:raise ValueError('audit source attribution invalid')
  if calendar is not None and (x.feasibility.calendar_hash!=calendar.calendar_hash or x.feasibility.calendar_count!=len(calendar.sessions)):raise ValueError('audit calendar attribution invalid')
def audit_status(run_dir,allowed_output_root):
 run,p,_,state=load_frozen_audit_run_state(run_dir,allowed_output_root)
 if (run/'.lock').exists():_verify_residual_lock(run/'.lock',p,run)
 cal=_files(run/'calendar_reports',state.members,_cal_data,'calendar report');reports=_files(run/'reports',state.members,_report_data,'audit report');_verify_attributions(cal,state,p)
 if len(cal)<len(state.members):return {'calendar_completed':len(cal),'audit_completed':0,'total':len(state.members),'status':'calendar_accumulating'}
 obs_path=run/'observed_calendar.json'
 if not obs_path.exists():return {'calendar_completed':len(cal),'audit_completed':0,'total':len(state.members),'status':'calendar_complete'}
 obs=_obs_data(_read(obs_path,'observed calendar'))
 if obs.corpus_policy_hash!=p.policy_hash or obs!=_rebuild_observed_calendar(p,cal):raise ValueError('observed calendar union binding invalid')
 _verify_attributions(reports,state,p,calendar=obs)
 if any(x.universe_hash!=state.universe_hash for x in reports.values()):raise ValueError('audit report universe binding invalid')
 if len(reports)<len(state.members):return {'calendar_completed':len(cal),'audit_completed':len(reports),'total':len(state.members),'status':'audit_accumulating'}
 return {'calendar_completed':len(cal),'audit_completed':len(reports),'total':len(state.members),'status':'complete'}
def execute_frozen_audit_run(run_dir,allowed_output_root,*,max_files_this_run,phase='auto'):
 if type(max_files_this_run)is not int or not 1<=max_files_this_run<=100 or phase not in {'auto','calendar','audit'}:raise ValueError('execute options invalid')
 run,p,_,state=load_frozen_audit_run_state(run_dir,allowed_output_root);raw=_lock(run,p);primary=None;out=[]
 try:
  cal=_files(run/'calendar_reports',state.members,_cal_data,'calendar report');_verify_attributions(cal,state,p)
  if phase in {'auto','calendar'} and len(cal)<len(state.members):
   out=[]
   for symbol in [x for x in state.members if x not in cal][:max_files_this_run]:
    rows,source,entry_hash=_rows(state,symbol);sessions=tuple(r.session for r in rows);b=CalendarReport(p.policy_hash,symbol,source,entry_hash,sessions,'sha256:'+'0'*64,'sha256:'+'0'*64);x=CalendarReport(**{**b.__dict__,'sessions_hash':h({'symbol':symbol,'sessions':[z.isoformat() for z in sessions]})});x=CalendarReport(**{**x.__dict__,'report_hash':h(x.payload())});x.verify();_write_once(run/'calendar_reports'/(symbol+'.json'),x.payload()|{'report_hash':x.report_hash});out.append(x)
   return {'phase':'calendar','completed':len(out)}
  if phase=='calendar':return {'phase':'calendar','completed':0}
  if len(cal)!=len(state.members):raise ValueError('calendar phase incomplete')
  op=run/'observed_calendar.json'
  if not op.exists():
   obs=_rebuild_observed_calendar(p,cal);obs.verify();_write_once(op,obs.payload()|{'calendar_hash':obs.calendar_hash})
  obs=_obs_data(_read(op,'observed calendar'))
  if obs!=_rebuild_observed_calendar(p,cal):raise ValueError('observed calendar union binding invalid')
  reports=_files(run/'reports',state.members,_report_data,'audit report');_verify_attributions(reports,state,p,calendar=obs)
  for symbol in [x for x in state.members if x not in reports][:max_files_this_run]:
   rows,source,entry_hash=_rows(state,symbol)
   if tuple(r.session for r in rows)!=cal[symbol].sessions:raise ValueError('calendar report no longer matches source')
   f=audit_symbol(symbol,obs.sessions,rows,obs.calendar_hash);b=FrozenAuditReport(p.policy_hash,state.universe_hash,symbol,source,entry_hash,f,'sha256:'+'0'*64);x=FrozenAuditReport(**{**b.__dict__,'report_hash':h(b.payload())});x.verify();_write_once(run/'reports'/(symbol+'.json'),x.payload()|{'report_hash':x.report_hash});out.append(x)
  return {'phase':'audit','completed':len(out)}
 except BaseException as exc:
  primary=exc;raise
 finally:_unlock(run/'.lock',raw,primary)
def finalize_frozen_audit(run_dir,allowed_output_root):
 run,p,_,state=load_frozen_audit_run_state(run_dir,allowed_output_root);status=audit_status(run,allowed_output_root)
 if status['status']!='complete':raise ValueError('audit is incomplete')
 obs=_obs_data(_read(run/'observed_calendar.json','observed calendar'));reports=_files(run/'reports',state.members,_report_data,'audit report')
 payload={'corpus_policy_hash':p.policy_hash,'universe_hash':state.universe_hash,'calendar_hash':obs.calendar_hash,'calendar_count':len(obs.sessions),'symbols':len(reports),'observed_sessions':sum(x.feasibility.observed_sessions for x in reports.values()),'zero_volume':sum(x.feasibility.zero_volume for x in reports.values()),'st_rows':sum(x.feasibility.st_rows for x in reports.values()),'one_price_boards':sum(x.feasibility.one_price_boards for x in reports.values()),'missing_observed_sessions':sum(x.feasibility.missing_observed_sessions for x in reports.values()),'status':'exploratory_execution_feasibility_complete','approximate_tradability':True,'official_tradability_verified':False,'adjudicable':False};value=payload|{'audit_hash':h(payload)};target=run/'final.json'
 if target.exists():
  if _read(target,'audit final')!=value:raise ValueError('audit final differs')
 else:_write_once(target,value)
 return value
