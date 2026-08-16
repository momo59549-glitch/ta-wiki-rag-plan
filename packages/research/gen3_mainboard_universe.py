"""Read-only, draft-only Shanghai/Shenzhen main-board universe admission.

This module deliberately excludes ChiNext, STAR, Beijing and every other board.
It freezes local content identities; it is neither a PIT manifest nor permission
to backtest, create candidates, or alter source data.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json, os, re, sys
from pathlib import Path

from .gen3_local_market import LocalParquetFileContract, _market_mapping, make_local_market_contract
from .gen3_market_admission import AdmissionPolicy, ContentFileEntry, RangeAdmissionDecision, _hash, _h, _json, _n, _write_once, load_content_run_state, _read_entries
from .gen3_quality_campaign import CorpusFileEntry, read_corpus_file_footer
from .gen3_rows import canonicalize_and_validate_row

SCHEMA="gen3-mainboard-universe-draft/v1"
PREFIXES=frozenset({"000","001","002","003","600","601","603","605"})
SZ_PREFIXES=frozenset({"000","001","002","003"})
SH_PREFIXES=PREFIXES-SZ_PREFIXES
START=date(2015,1,1); END=date(2026,8,5)

def _iso(v:object,name:str)->date:
    if not isinstance(v,str):raise ValueError(f"{name} must be ISO date")
    try:return date.fromisoformat(v)
    except ValueError as exc:raise ValueError(f"{name} must be ISO date") from exc
def _aware(v:object,name:str)->datetime:
    if not isinstance(v,str):raise ValueError(f"{name} must be UTC datetime")
    try:x=datetime.fromisoformat(v)
    except ValueError as exc:raise ValueError(f"{name} must be UTC datetime") from exc
    if x.tzinfo is None or x.utcoffset() is None or x.astimezone(timezone.utc).isoformat()!=v:raise ValueError(f"{name} must be UTC datetime")
    return x
def _safe(path:Path,allowed:Path)->Path:
    x=path.resolve()
    try:x.relative_to(allowed.resolve())
    except ValueError as exc:raise ValueError("output escapes allowed root") from exc
    return x

@dataclass(frozen=True)
class MainboardUniversePolicy:
    manifest_content_hash:str; manifest_record_count:int; excluded_by_board:int; trend_content_snapshot_hash:str; quality_snapshot_hash:str; trend_source_contract_hash:str; admission_policy_hash:str; admission_decision_hash:str; research_start:date; research_end:date; allowed_prefixes:tuple[str,...]; explicit_zero_exceptions:tuple[tuple[str,date],...]; write_policy:str; policy_hash:str; is_formal:bool=False; schema_version:str=SCHEMA
    def payload(self):return {"schema_version":self.schema_version,"manifest_content_hash":self.manifest_content_hash,"manifest_record_count":self.manifest_record_count,"excluded_by_board":self.excluded_by_board,"trend_content_snapshot_hash":self.trend_content_snapshot_hash,"quality_snapshot_hash":self.quality_snapshot_hash,"trend_source_contract_hash":self.trend_source_contract_hash,"admission_policy_hash":self.admission_policy_hash,"admission_decision_hash":self.admission_decision_hash,"research_start":self.research_start.isoformat(),"research_end":self.research_end.isoformat(),"allowed_prefixes":list(self.allowed_prefixes),"explicit_zero_exceptions":[[s,d.isoformat()] for s,d in self.explicit_zero_exceptions],"write_policy":self.write_policy,"is_formal":self.is_formal}
    def verify(self):
        if self.schema_version!=SCHEMA or self.is_formal or self.research_start!=START or self.research_end!=END or self.allowed_prefixes!=tuple(sorted(PREFIXES)) or self.write_policy!="no_source_mutation":raise ValueError("mainboard policy must remain frozen draft")
        _n("manifest_record_count",self.manifest_record_count,1);_n("excluded_by_board",self.excluded_by_board)
        for k in ("manifest_content_hash","trend_content_snapshot_hash","quality_snapshot_hash","trend_source_contract_hash","admission_policy_hash","admission_decision_hash","policy_hash"):_h(k,getattr(self,k))
        if self.explicit_zero_exceptions != (("000562",date(2015,1,26)),("601268",date(2015,5,21))):raise ValueError("zero exceptions are frozen")
        if self.policy_hash!=_hash(self.payload()):raise ValueError("mainboard policy hash mismatch")
def make_mainboard_policy(manifest_bytes:bytes,manifest_record_count:int,excluded_by_board:int,trend_content_snapshot_hash:str,quality_snapshot_hash:str,trend_source_contract_hash:str,admission_policy:AdmissionPolicy,admission_decision:RangeAdmissionDecision,members:tuple[MainboardMember,...])->MainboardUniversePolicy:
    admission_policy.verify();admission_decision.verify();_h("trend content snapshot",trend_content_snapshot_hash)
    active={m.symbol:m.active_to for m in members}
    if active.get("000562")!=date(2015,1,26) or active.get("601268")!=date(2015,5,21):raise ValueError("frozen zero exceptions must belong to manifest members")
    b=MainboardUniversePolicy("sha256:"+sha256(manifest_bytes).hexdigest(),manifest_record_count,excluded_by_board,trend_content_snapshot_hash,quality_snapshot_hash,trend_source_contract_hash,admission_policy.policy_hash,admission_decision.decision_hash,START,END,tuple(sorted(PREFIXES)),(("000562",date(2015,1,26)),("601268",date(2015,5,21))),"no_source_mutation","sha256:"+"0"*64)
    r=MainboardUniversePolicy(**{**b.__dict__,"policy_hash":_hash(b.payload())});r.verify();return r
def mainboard_policy_from_data(v:object)->MainboardUniversePolicy:
    fields={"schema_version","manifest_content_hash","manifest_record_count","excluded_by_board","trend_content_snapshot_hash","quality_snapshot_hash","trend_source_contract_hash","admission_policy_hash","admission_decision_hash","research_start","research_end","allowed_prefixes","explicit_zero_exceptions","write_policy","policy_hash","is_formal"}
    if not isinstance(v,dict) or set(v)!=fields or not isinstance(v["allowed_prefixes"],list) or not isinstance(v["explicit_zero_exceptions"],list):raise ValueError("mainboard policy strict schema")
    try:r=MainboardUniversePolicy(v["manifest_content_hash"],v["manifest_record_count"],v["excluded_by_board"],v["trend_content_snapshot_hash"],v["quality_snapshot_hash"],v["trend_source_contract_hash"],v["admission_policy_hash"],v["admission_decision_hash"],date.fromisoformat(v["research_start"]),date.fromisoformat(v["research_end"]),tuple(v["allowed_prefixes"]),tuple((x[0],date.fromisoformat(x[1])) for x in v["explicit_zero_exceptions"]),v["write_policy"],v["policy_hash"],v["is_formal"],v["schema_version"])
    except (TypeError,ValueError,IndexError) as exc:raise ValueError("mainboard policy data invalid") from exc
    r.verify();return r

@dataclass(frozen=True)
class MainboardMember:
    symbol:str; ts_code:str; name:str; exchange:str; market:str; list_status:str; active_from:date; active_to:date|None; fetched_at:datetime
    def verify(self):
        if not re.fullmatch(r"[0-9]{6}",self.symbol) or not isinstance(self.name,str) or not self.name.strip() or self.name!=self.name.strip():raise ValueError("manifest symbol/name invalid")
        expected={"SZSE":"SZ","SSE":"SH","BSE":"BJ"}.get(self.exchange)
        expected_exchange="SZSE" if self.symbol[:3] in SZ_PREFIXES else "SSE"
        if expected is None or self.ts_code!=self.symbol+"."+expected or self.list_status not in {"L","D","P","G"}:raise ValueError("manifest exchange/status invalid")
        if self.symbol[:3] in PREFIXES and (self.exchange!=expected_exchange or self.market!="主板"):raise ValueError("mainboard prefix exchange/market invalid")
        if self.active_to is not None and self.active_to<self.active_from:raise ValueError("active dates invalid")
        if self.fetched_at.tzinfo is None or self.fetched_at.utcoffset() is None:raise ValueError("fetched_at must be aware")
    def active(self):return self.active_from<=END and (self.active_to is None or self.active_to>=START)

@dataclass(frozen=True)
class MainboardManifestInventory:
    raw_bytes:bytes; record_count:int; excluded_by_board:int; members:tuple[MainboardMember,...]
    def verify(self):
        _n("record_count",self.record_count,1);_n("excluded_by_board",self.excluded_by_board)
        if self.members!=tuple(sorted(self.members,key=lambda x:x.symbol)) or len({x.symbol for x in self.members})!=len(self.members):raise ValueError("manifest inventory invalid")
        for x in self.members:x.verify()
def load_mainboard_manifest(path:str|Path)->MainboardManifestInventory:
    raw=Path(path).read_bytes(); lines=raw.splitlines()
    if not lines:raise ValueError("manifest is empty")
    members=[]; seen=set(); excluded=0; fields={"source","symbol","ts_code","name","exchange","market","list_status","active_from","active_to","fetched_at"}
    for index,line in enumerate(lines,1):
        try:item=json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError,json.JSONDecodeError) as exc:raise ValueError("manifest must be UTF-8 JSONL") from exc
        if not isinstance(item,dict) or set(item)!=fields or item["source"]!="tushare.stock_basic":raise ValueError("manifest strict schema/source invalid")
        end=None if item["active_to"] is None else _iso(item["active_to"],"active_to")
        m=MainboardMember(item["symbol"],item["ts_code"],item["name"],item["exchange"],item["market"],item["list_status"],_iso(item["active_from"],"active_from"),end,_aware(item["fetched_at"],"fetched_at"));m.verify()
        if m.symbol in seen:raise ValueError("duplicate manifest symbol")
        seen.add(m.symbol)
        if m.symbol[:3] not in PREFIXES:
            excluded+=1;continue
        if m.active():members.append(m)
    result=MainboardManifestInventory(raw,len(lines),excluded,tuple(sorted(members,key=lambda x:x.symbol)));result.verify();return result

@dataclass(frozen=True)
class UniverseEntry:
    mainboard_policy_hash:str; symbol:str; source:str; entry_hash:str; selected_rows:int; active_to:date|None; attribution_hash:str
    def payload(self):return {"mainboard_policy_hash":self.mainboard_policy_hash,"symbol":self.symbol,"source":self.source,"entry_hash":self.entry_hash,"selected_rows":self.selected_rows,"active_to":self.active_to.isoformat() if self.active_to else None}
    def verify(self):
        if not re.fullmatch(r"[0-9]{6}",self.symbol) or self.source not in {"trend","supplement","explicit_no_observed_trading_rows"}:raise ValueError("universe entry invalid")
        _h("mainboard_policy_hash",self.mainboard_policy_hash);_h("entry_hash",self.entry_hash);_h("attribution_hash",self.attribution_hash);_n("selected_rows",self.selected_rows)
        if self.source=="explicit_no_observed_trading_rows" and (self.selected_rows or self.active_to is None):raise ValueError("zero row member requires delisting evidence")
        if self.source!="explicit_no_observed_trading_rows" and self.selected_rows<1:raise ValueError("universe content entry empty")
        if self.attribution_hash!=_hash(self.payload()):raise ValueError("universe attribution hash invalid")

def _universe_entry(policy:MainboardUniversePolicy,symbol:str,source:str,entry_hash:str,rows:int,active_to:date|None)->UniverseEntry:
    b=UniverseEntry(policy.policy_hash,symbol,source,entry_hash,rows,active_to,"sha256:"+"0"*64);r=UniverseEntry(**{**b.__dict__,"attribution_hash":_hash(b.payload())});r.verify();return r

@dataclass(frozen=True)
class SupplementContentEntry:
    mainboard_policy_hash:str; symbol:str; selected_rows:int; min_session:date; max_session:date; content_hash:str; supplement_contract_hash:str; corpus_entry_hash:str; entry_hash:str
    def payload(self):return {"mainboard_policy_hash":self.mainboard_policy_hash,"symbol":self.symbol,"selected_rows":self.selected_rows,"min_session":self.min_session.isoformat(),"max_session":self.max_session.isoformat(),"content_hash":self.content_hash,"supplement_contract_hash":self.supplement_contract_hash,"corpus_entry_hash":self.corpus_entry_hash}
    def verify(self):
        _h("mainboard_policy_hash",self.mainboard_policy_hash);_h("content_hash",self.content_hash);_h("supplement_contract_hash",self.supplement_contract_hash);_h("corpus_entry_hash",self.corpus_entry_hash);_h("entry_hash",self.entry_hash)
        if not re.fullmatch(r"[0-9]{6}",self.symbol) or self.symbol[:3] not in PREFIXES or self.selected_rows<1 or self.min_session<START or self.max_session>END or self.min_session>self.max_session or self.entry_hash!=_hash(self.payload()):raise ValueError("supplement content entry invalid")

def trend_universe_entries(run_dir:str|Path,contract:LocalParquetFileContract,mainboard_policy:MainboardUniversePolicy,*,allowed_output_root:str|Path,quality_run_dir:str|Path,quality_allowed_output_root:str|Path)->tuple[object,AdmissionPolicy,RangeAdmissionDecision,tuple[ContentFileEntry,...]]:
    mainboard_policy.verify()
    run,snapshot,policy,decision=load_content_run_state(run_dir,contract,allowed_output_root=allowed_output_root,quality_run_dir=quality_run_dir,quality_allowed_output_root=quality_allowed_output_root)
    entries=_read_entries(run,snapshot,policy,decision,contract)
    if len(entries)!=len(snapshot.files):raise ValueError("trend content run incomplete")
    digest=_hash({"snapshot_hash":snapshot.snapshot_hash,"entries":[x.entry_hash for x in entries]})
    if (digest,snapshot.snapshot_hash,contract.contract_hash,policy.policy_hash,decision.decision_hash)!=(mainboard_policy.trend_content_snapshot_hash,mainboard_policy.quality_snapshot_hash,mainboard_policy.trend_source_contract_hash,mainboard_policy.admission_policy_hash,mainboard_policy.admission_decision_hash):raise ValueError("trend content identity does not bind mainboard policy")
    return snapshot,policy,decision,entries

def _supplement_hash(entry:CorpusFileEntry,contract:LocalParquetFileContract,policy:MainboardUniversePolicy)->tuple[int,date,date,str]:
    if read_corpus_file_footer(contract,entry.file_path)!=entry:raise ValueError("supplement footer changed before scan")
    import pyarrow.parquet as pq
    rows=[]; dates=[]; prev=None; mapping=_market_mapping(contract); cols=[contract.date_column,contract.open_column,contract.high_column,contract.low_column,contract.close_column,contract.volume_column]
    for batch in pq.ParquetFile(entry.file_path).iter_batches(batch_size=10_000,columns=cols):
        for raw in batch.to_pylist():
            session=raw[contract.date_column]
            if isinstance(session,datetime):
                if session.tzinfo is not None:raise ValueError("supplement session invalid")
                session=session.date()
            if type(session)is not date or (prev is not None and session<=prev):raise ValueError("supplement sessions invalid")
            prev=session
            if START<=session<=END:rows.append(canonicalize_and_validate_row(mapping,{**raw,contract.date_column:session,"__filename_symbol":entry.symbol}).row_hash);dates.append(session)
    if read_corpus_file_footer(contract,entry.file_path)!=entry:raise ValueError("supplement footer changed during scan")
    if not rows:raise ValueError("supplement has no research rows")
    return len(rows),min(dates),max(dates),_hash({"symbol":entry.symbol,"row_hashes":rows,"policy_hash":policy.policy_hash,"corpus_entry_hash":_hash(entry.payload())})

def scan_supplement(symbol:str,root:str|Path,policy:MainboardUniversePolicy)->SupplementContentEntry:
    policy.verify();root=Path(root).resolve();path=(root/(symbol+".parquet")).resolve()
    if path.parent!=root:raise ValueError("supplement path escape")
    contract=make_local_market_contract(source_id="tushare_daily_cache",root=str(root),date_column="trade_date",open_column="open",high_column="high",low_column="low",close_column="close",volume_column="volume")
    footer=read_corpus_file_footer(contract,path);count,start,end,digest=_supplement_hash(footer,contract,policy)
    b=SupplementContentEntry(policy.policy_hash,symbol,count,start,end,digest,contract.contract_hash,_hash(footer.payload()),"sha256:"+"0"*64);result=SupplementContentEntry(**{**b.__dict__,"entry_hash":_hash(b.payload())});result.verify();return result

@dataclass(frozen=True)
class UniverseCoverageDecision:
    policy_hash:str; members:int; trend_nonempty:int; trend_zero_explicit:int; supplement:int; missing:int; trend_outside_research_members:int; entries:tuple[UniverseEntry,...]; status:str; universe_hash:str
    def payload(self):return {"policy_hash":self.policy_hash,"members":self.members,"trend_nonempty":self.trend_nonempty,"trend_zero_explicit":self.trend_zero_explicit,"supplement":self.supplement,"missing":self.missing,"trend_outside_research_members":self.trend_outside_research_members,"entries":[x.payload() for x in self.entries],"status":self.status}
    def verify(self):
        _h("policy_hash",self.policy_hash);_h("universe_hash",self.universe_hash)
        for n in ("members","trend_nonempty","trend_zero_explicit","supplement","missing","trend_outside_research_members"):_n(n,getattr(self,n))
        if self.status not in {"mainboard_universe_content_complete","blocked"} or self.entries!=tuple(sorted(self.entries,key=lambda x:x.symbol)) or len({x.symbol for x in self.entries})!=len(self.entries) or self.members!=len(self.entries)+self.missing:raise ValueError("universe coverage invalid")
        for x in self.entries:x.verify()
        if any(x.mainboard_policy_hash!=self.policy_hash for x in self.entries) or (self.trend_nonempty,self.trend_zero_explicit,self.supplement)!=(sum(x.source=="trend" for x in self.entries),sum(x.source=="explicit_no_observed_trading_rows" for x in self.entries),sum(x.source=="supplement" for x in self.entries)):raise ValueError("universe coverage counts invalid")
        if self.status=="mainboard_universe_content_complete" and (self.missing or len(self.entries)!=self.members):raise ValueError("complete universe coverage invalid")
        if self.universe_hash!=_hash(self.payload()):raise ValueError("universe hash mismatch")
def build_universe_coverage(policy:MainboardUniversePolicy,members:tuple[MainboardMember,...],trend_entries:tuple[ContentFileEntry,...],supplement_entries:tuple[SupplementContentEntry,...]=(),*,verified_complete_trend:bool=False)->UniverseCoverageDecision:
    policy.verify(); bytrend={x.symbol:x for x in trend_entries};bysupp={x.symbol:x for x in supplement_entries};result=[]
    if len(bytrend)!=len(trend_entries) or len(bysupp)!=len(supplement_entries) or len({m.symbol for m in members})!=len(members) or tuple(sorted(m.symbol for m in members))!=tuple(m.symbol for m in members):raise ValueError("members/trend/supplement must be unique sorted")
    for m in members:
        t=bytrend.get(m.symbol); s=bysupp.get(m.symbol)
        if t:
            t.verify()
            if s:raise ValueError("supplement may not cover trend symbol")
            if t.selected_rows:result.append(_universe_entry(policy,m.symbol,"trend",t.entry_hash,t.selected_rows,m.active_to))
            elif (m.symbol,m.active_to) in policy.explicit_zero_exceptions:result.append(_universe_entry(policy,m.symbol,"explicit_no_observed_trading_rows",t.entry_hash,0,m.active_to))
            # A still-active zero-row member is not silently treated as a
            # suspension; leave it unresolved so the decision is blocked.
            else:continue
        elif s:
            s.verify()
            if s.mainboard_policy_hash!=policy.policy_hash:raise ValueError("supplement policy binding invalid")
            result.append(_universe_entry(policy,m.symbol,"supplement",s.entry_hash,s.selected_rows,m.active_to))
    member_symbols={m.symbol for m in members}
    outside=sum(t.symbol not in member_symbols for t in trend_entries)
    if outside and not verified_complete_trend:raise ValueError("unverified trend has nonmember entry")
    for s in supplement_entries:
        if s.symbol not in member_symbols:raise ValueError("supplement is not mainboard member")
    missing=len(members)-len(result); status="mainboard_universe_content_complete" if not missing else "blocked"
    b=UniverseCoverageDecision(policy.policy_hash,len(members),sum(x.source=="trend" for x in result),sum(x.source=="explicit_no_observed_trading_rows" for x in result),sum(x.source=="supplement" for x in result),missing,outside,tuple(result),status,"sha256:"+"0"*64)
    r=UniverseCoverageDecision(**{**b.__dict__,"universe_hash":_hash(b.payload())});r.verify();return r

# A deliberately small resumable supplement runner.  It does not enumerate the
# root: the caller supplies the exact missing symbols derived above.
def prepare_supplement_run(policy:MainboardUniversePolicy,missing_symbols:tuple[str,...],supplement_root:str|Path,*,workspace_output_root:str|Path,allowed_output_root:str|Path)->Path:
    policy.verify(); symbols=tuple(sorted(missing_symbols))
    if not symbols or any(not re.fullmatch(r"[0-9]{6}",x) or x[:3] not in PREFIXES for x in symbols):raise ValueError("missing symbols invalid")
    root=Path(supplement_root).resolve();contract=make_local_market_contract(source_id="tushare_daily_cache",root=str(root),date_column="trade_date",open_column="open",high_column="high",low_column="low",close_column="close",volume_column="volume")
    out=_safe(Path(workspace_output_root),Path(allowed_output_root));run=out/("mainboard-supplement-"+sha256(_json({"policy":policy.policy_hash,"symbols":list(symbols),"root":str(root),"contract":contract.contract_hash})).hexdigest())
    if run.exists():raise ValueError("supplement run already exists")
    out.mkdir(parents=True,exist_ok=True);run.mkdir();(run/"entries").mkdir();_write_once(run/"policy.json",policy.payload()|{"policy_hash":policy.policy_hash});_write_once(run/"symbols.json",{"symbols":list(symbols),"supplement_root":str(root),"supplement_contract_hash":contract.contract_hash});return run
def execute_supplement_run(run_dir:str|Path,root:str|Path,policy:MainboardUniversePolicy,*,allowed_output_root:str|Path,max_files_this_run:int)->tuple[SupplementContentEntry,...]:
    _n("max_files_this_run",max_files_this_run,1,100);policy.verify();run=_safe(Path(run_dir),Path(allowed_output_root));lock=run/".lock"
    if lock.exists() or any(p.name.endswith(".tmp") for p in (run/"entries").iterdir()):raise ValueError("supplement lock or tmp blocks run")
    stored=json.loads((run/"policy.json").read_text(encoding="utf-8"));meta=json.loads((run/"symbols.json").read_text(encoding="utf-8"));symbols=meta.get("symbols") if isinstance(meta,dict) else None
    if _json(stored)!=_json(policy.payload()|{"policy_hash":policy.policy_hash}) or not isinstance(symbols,list) or symbols!=sorted(symbols):raise ValueError("supplement run metadata invalid")
    root_path=Path(root).resolve(); contract=make_local_market_contract(source_id="tushare_daily_cache",root=str(root_path),date_column="trade_date",open_column="open",high_column="high",low_column="low",close_column="close",volume_column="volume")
    if set(meta)!={"symbols","supplement_root","supplement_contract_hash"} or meta["supplement_root"]!=str(root_path) or meta["supplement_contract_hash"]!=contract.contract_hash:raise ValueError("supplement root/contract binding invalid")
    raw=_json({"schema_version":SCHEMA,"policy_hash":policy.policy_hash,"pid":os.getpid()})
    with open(lock,"xb") as f:f.write(raw);f.flush();os.fsync(f.fileno())
    try:
        expected={x+".json" for x in symbols}; found=set()
        for p in (run/"entries").iterdir():
            if not p.is_file() or p.name.endswith(".tmp") or p.name not in expected:raise ValueError("supplement entry artifact invalid")
            found.add(p.stem)
        for symbol in [x for x in symbols if x not in found][:max_files_this_run]:
            item=scan_supplement(symbol,root,policy);_write_once(run/"entries"/(symbol+".json"),item.payload()|{"entry_hash":item.entry_hash})
        items=[]
        for p in (run/"entries").iterdir():
            v=json.loads(p.read_text(encoding="utf-8")); fields={"mainboard_policy_hash","symbol","selected_rows","min_session","max_session","content_hash","supplement_contract_hash","corpus_entry_hash","entry_hash"}
            if not isinstance(v,dict) or set(v)!=fields:raise ValueError("supplement entry schema invalid")
            item=SupplementContentEntry(v["mainboard_policy_hash"],v["symbol"],v["selected_rows"],date.fromisoformat(v["min_session"]),date.fromisoformat(v["max_session"]),v["content_hash"],v["supplement_contract_hash"],v["corpus_entry_hash"],v["entry_hash"]);item.verify()
            if item.symbol!=p.stem or item.mainboard_policy_hash!=policy.policy_hash or item.supplement_contract_hash!=contract.contract_hash or item.corpus_entry_hash!=_hash(read_corpus_file_footer(contract,root_path/(item.symbol+".parquet")).payload()):raise ValueError("supplement entry binding invalid")
            items.append(item)
        return tuple(sorted(items,key=lambda x:x.symbol))
    finally:
        try:lock.unlink()
        except OSError:
            if sys.exc_info()[0] is None:raise

def supplement_run_status(run_dir:str|Path,policy:MainboardUniversePolicy,*,allowed_output_root:str|Path)->tuple[int,int]:
    """Metadata-only status; never opens a supplement parquet file."""
    run=_safe(Path(run_dir),Path(allowed_output_root));policy.verify()
    if (run/".lock").exists() or any(p.name.endswith(".tmp") for p in (run/"entries").iterdir()):raise ValueError("supplement lock or tmp blocks status")
    if _json(json.loads((run/"policy.json").read_text(encoding="utf-8")))!=_json(policy.payload()|{"policy_hash":policy.policy_hash}):raise ValueError("supplement policy metadata invalid")
    meta=json.loads((run/"symbols.json").read_text(encoding="utf-8")); symbols=meta.get("symbols") if isinstance(meta,dict) else None
    if not isinstance(symbols,list) or symbols!=sorted(symbols):raise ValueError("supplement symbols metadata invalid")
    files=list((run/"entries").iterdir())
    if any(not p.is_file() or p.name not in {x+".json" for x in symbols} for p in files):raise ValueError("supplement entry artifact invalid")
    return len(files),len(symbols)
