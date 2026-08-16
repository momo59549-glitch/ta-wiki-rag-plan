"""Research-window market admission and content-identity snapshots (Phase 1 draft).

This is a quality-derived, local content identity only.  It is explicitly not a
PIT manifest, a lockbox, a formal protocol, or authorization to backtest.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
from typing import Iterable

from .gen3_local_market import LocalParquetFileContract, _market_mapping
from .gen3_quality_campaign import CorpusFileEntry, CorpusSnapshot, CampaignContract, read_corpus_file_footer
from .gen3_quality_run import _load_run, _read_completed_reports, _safe_output_root
from .gen3_rows import canonicalize_and_validate_row


MARKET_ADMISSION_SCHEMA_VERSION = "gen3-market-admission-draft/v1"
_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")


def _json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _hash(value: object) -> str:
    return "sha256:" + sha256(_json(value)).hexdigest()


def _h(name: str, value: object) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value): raise ValueError(f"{name} must be sha256")
    return value


def _n(name: str, value: object, minimum: int = 0, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum): raise ValueError(f"{name} is invalid")
    return value


def _d(name: str, value: object) -> date:
    if type(value) is not date: raise ValueError(f"{name} must be date")
    return value


@dataclass(frozen=True)
class AdmissionPolicy:
    source_contract_hash: str; snapshot_hash: str; campaign_hash: str; aggregate_hash: str
    research_start: date; research_end: date; required_clean_range_start: date
    write_policy: str; policy_hash: str; is_formal: bool = False
    schema_version: str = MARKET_ADMISSION_SCHEMA_VERSION
    def payload(self): return {"schema_version":self.schema_version,"source_contract_hash":self.source_contract_hash,"snapshot_hash":self.snapshot_hash,"campaign_hash":self.campaign_hash,"aggregate_hash":self.aggregate_hash,"research_start":self.research_start.isoformat(),"research_end":self.research_end.isoformat(),"required_clean_range_start":self.required_clean_range_start.isoformat(),"write_policy":self.write_policy,"is_formal":self.is_formal}
    def verify(self):
        if self.schema_version != MARKET_ADMISSION_SCHEMA_VERSION or self.is_formal: raise ValueError("admission policy must remain draft/nonformal")
        for name in ("source_contract_hash","snapshot_hash","campaign_hash","aggregate_hash","policy_hash"): _h(name,getattr(self,name))
        if self.research_start != date(2015,1,1) or self.required_clean_range_start != self.research_start or self.research_end < self.research_start or self.research_end > date(2026,8,31): raise ValueError("invalid frozen research window")
        if self.write_policy != "no_source_mutation": raise ValueError("write_policy must be no_source_mutation")
        if self.policy_hash != _hash(self.payload()): raise ValueError("policy_hash mismatch")


def make_admission_policy(contract: LocalParquetFileContract, *, snapshot_hash: str, campaign_hash: str, aggregate_hash: str, research_end: date) -> AdmissionPolicy:
    contract.verify(); base=AdmissionPolicy(contract.contract_hash,snapshot_hash,campaign_hash,aggregate_hash,date(2015,1,1),research_end,date(2015,1,1),"no_source_mutation","sha256:"+"0"*64)
    result=AdmissionPolicy(**{**base.__dict__,"policy_hash":_hash(base.payload())}); result.verify(); return result
def policy_from_data(value:object)->AdmissionPolicy:
    fields={"schema_version","source_contract_hash","snapshot_hash","campaign_hash","aggregate_hash","research_start","research_end","required_clean_range_start","write_policy","is_formal","policy_hash"}
    if not isinstance(value,dict) or set(value)!=fields:raise ValueError("policy strict schema")
    try:result=AdmissionPolicy(value["source_contract_hash"],value["snapshot_hash"],value["campaign_hash"],value["aggregate_hash"],date.fromisoformat(value["research_start"]),date.fromisoformat(value["research_end"]),date.fromisoformat(value["required_clean_range_start"]),value["write_policy"],value["policy_hash"],value["is_formal"],value["schema_version"])
    except (TypeError,ValueError) as exc:raise ValueError("policy data invalid") from exc
    result.verify();return result

def _bind(policy: AdmissionPolicy, decision: "RangeAdmissionDecision", snapshot: CorpusSnapshot, contract: LocalParquetFileContract) -> None:
    policy.verify(); decision.verify(); snapshot.verify(); contract.verify()
    if policy.snapshot_hash!=snapshot.snapshot_hash or policy.source_contract_hash!=contract.contract_hash or decision.policy_hash!=policy.policy_hash: raise ValueError("policy/decision/snapshot/contract binding is invalid")


@dataclass(frozen=True)
class RangeAdmissionDecision:
    policy_hash: str; quality_run_dir: str; excluded_issue_hashes: tuple[str,...]; excluded_issue_count: int
    research_files: int; research_rows: int; status: str; decision_hash: str
    schema_version: str = MARKET_ADMISSION_SCHEMA_VERSION
    def payload(self): return {"schema_version":self.schema_version,"policy_hash":self.policy_hash,"quality_run_dir":self.quality_run_dir,"excluded_issue_hashes":list(self.excluded_issue_hashes),"excluded_issue_count":self.excluded_issue_count,"research_files":self.research_files,"research_rows":self.research_rows,"status":self.status}
    def verify(self):
        if self.schema_version!=MARKET_ADMISSION_SCHEMA_VERSION or self.status not in {"eligible_for_content_snapshot","blocked"}: raise ValueError("invalid admission decision")
        _h("policy_hash",self.policy_hash); _h("decision_hash",self.decision_hash)
        if self.excluded_issue_hashes!=tuple(sorted(self.excluded_issue_hashes)) or len(set(self.excluded_issue_hashes))!=len(self.excluded_issue_hashes) or self.excluded_issue_count!=len(self.excluded_issue_hashes): raise ValueError("excluded issue hashes invalid")
        for value in self.excluded_issue_hashes:_h("issue hash",value)
        _n("research_files",self.research_files);_n("research_rows",self.research_rows)
        if self.status=="eligible_for_content_snapshot" and (self.research_files<1 or self.research_rows<1): raise ValueError("eligible decision needs non-empty research content")
        if self.decision_hash != _hash(self.payload()): raise ValueError("decision_hash mismatch")
def decision_from_data(value:object)->RangeAdmissionDecision:
    fields={"schema_version","policy_hash","quality_run_dir","excluded_issue_hashes","excluded_issue_count","research_files","research_rows","status","decision_hash"}
    if not isinstance(value,dict) or set(value)!=fields or not isinstance(value["excluded_issue_hashes"],list):raise ValueError("decision strict schema")
    result=RangeAdmissionDecision(value["policy_hash"],value["quality_run_dir"],tuple(value["excluded_issue_hashes"]),value["excluded_issue_count"],value["research_files"],value["research_rows"],value["status"],value["decision_hash"],value["schema_version"]);result.verify();return result


def decide_range_admission(policy: AdmissionPolicy, quality_run_dir: str|Path, contract: LocalParquetFileContract, *, allowed_output_root: str|Path) -> RangeAdmissionDecision:
    policy.verify(); root,snapshot,campaign,_=_load_run(quality_run_dir,contract,allowed_output_root=allowed_output_root)
    reports=_read_completed_reports(root,snapshot,campaign,contract)
    # A completed run's aggregate is reconstructed, and must match the policy.
    from .gen3_quality_campaign import aggregate_campaign_reports
    if len(reports)!=len(snapshot.files): raise ValueError("quality run is incomplete")
    aggregate=aggregate_campaign_reports(snapshot,campaign,contract,reports)
    if snapshot.snapshot_hash!=policy.snapshot_hash or campaign.campaign_hash!=policy.campaign_hash or aggregate.aggregate_hash!=policy.aggregate_hash or contract.contract_hash!=policy.source_contract_hash: raise ValueError("policy does not bind quality run")
    blocked=False; excluded=[]
    for report,entry in zip(reports,snapshot.files):
        if report.truncated or report.truncated_issues or report.rows_scanned!=entry.num_rows: blocked=True
        for issue in report.issues:
            if issue.session is None or issue.session>=policy.research_start: blocked=True
            else: excluded.append(issue.issue_hash)
    # Research rows are deliberately computed by streaming content, not inferred
    # from min/max report metadata. A research-window issue blocks before rows
    # are admitted into any content identity.
    rows=0; files=0
    if not blocked:
        for entry in snapshot.files:
            count,_,_,_=_content_entry(entry,contract,policy)
            rows += count
            if count: files += 1
    base=RangeAdmissionDecision(policy.policy_hash,str(root),tuple(sorted(excluded)),len(excluded),files,rows,"blocked" if blocked or rows==0 else "eligible_for_content_snapshot","sha256:"+"0"*64)
    result=RangeAdmissionDecision(**{**base.__dict__,"decision_hash":_hash(base.payload())});result.verify();return result


@dataclass(frozen=True)
class ContentFileEntry:
    symbol:str; selected_rows:int; min_session:date|None; max_session:date|None; content_hash:str; policy_hash:str; decision_hash:str; snapshot_hash:str; source_contract_hash:str; corpus_entry_hash:str; entry_hash:str
    def payload(self): return {"symbol":self.symbol,"selected_rows":self.selected_rows,"min_session":self.min_session.isoformat() if self.min_session else None,"max_session":self.max_session.isoformat() if self.max_session else None,"content_hash":self.content_hash,"policy_hash":self.policy_hash,"decision_hash":self.decision_hash,"snapshot_hash":self.snapshot_hash,"source_contract_hash":self.source_contract_hash,"corpus_entry_hash":self.corpus_entry_hash}
    def verify(self):
        if not re.fullmatch(r"[0-9]{6}",self.symbol):raise ValueError("content symbol invalid")
        _n("selected_rows",self.selected_rows)
        for x in ("content_hash","policy_hash","decision_hash","snapshot_hash","source_contract_hash","corpus_entry_hash","entry_hash"): _h(x,getattr(self,x))
        if self.selected_rows==0 and (self.min_session is not None or self.max_session is not None):raise ValueError("zero selected file needs null range")
        if self.selected_rows and (type(self.min_session)is not date or type(self.max_session)is not date or self.min_session>self.max_session):raise ValueError("content range invalid")
        if self.entry_hash!=_hash(self.payload()):raise ValueError("content entry hash mismatch")


def _content_entry(entry: CorpusFileEntry, contract: LocalParquetFileContract, policy: AdmissionPolicy) -> tuple[int,date|None,date|None,str]:
    """Stream six columns and return canonical selected-row identity."""
    policy.verify()
    if read_corpus_file_footer(contract,entry.file_path)!=entry:raise ValueError("source footer mismatch before content scan")
    import pyarrow.parquet as pq
    pf=pq.ParquetFile(entry.file_path); cols=[contract.date_column,contract.open_column,contract.high_column,contract.low_column,contract.close_column,contract.volume_column]
    mapping=_market_mapping(contract); previous=None; hashes=[]; dates=[]
    for batch in pf.iter_batches(batch_size=10_000,columns=cols):
        for raw in batch.to_pylist():
            session=raw[contract.date_column]
            if isinstance(session,datetime):
                if session.tzinfo is not None:raise ValueError("invalid session type in content scan")
                session=session.date()
            if type(session)is not date:raise ValueError("invalid session type in content scan")
            if previous is not None and session<=previous:raise ValueError("content sessions must be strictly increasing")
            previous=session
            if policy.research_start<=session<=policy.research_end:
                row=canonicalize_and_validate_row(mapping,{**raw,contract.date_column:session,"__filename_symbol":entry.symbol})
                hashes.append(row.row_hash); dates.append(session)
    if read_corpus_file_footer(contract,entry.file_path)!=entry:raise ValueError("source footer changed during content scan")
    return len(hashes),(min(dates) if dates else None),(max(dates) if dates else None),_hash({"symbol":entry.symbol,"row_hashes":hashes})


def build_content_file_entry(entry: CorpusFileEntry, contract: LocalParquetFileContract, policy: AdmissionPolicy, decision: RangeAdmissionDecision, snapshot: CorpusSnapshot)->ContentFileEntry:
    _bind(policy,decision,snapshot,contract);count,start,end,digest=_content_entry(entry,contract,policy);base=ContentFileEntry(entry.symbol,count,start,end,digest,policy.policy_hash,decision.decision_hash,snapshot.snapshot_hash,contract.contract_hash,_hash(entry.payload()),"sha256:"+"0"*64);result=ContentFileEntry(**{**base.__dict__,"entry_hash":_hash(base.payload())});result.verify();return result


def _write_once(path:Path,value:object):
    if path.exists():raise ValueError("write-once target exists")
    tmp=path.with_name(path.name+".tmp")
    if tmp.exists():raise ValueError("orphan tmp blocks publication")
    try:
        with open(tmp,"xb") as f:f.write(_json(value));f.flush();os.fsync(f.fileno())
        os.link(tmp,path)
    finally:
        if tmp.exists():
            try:tmp.unlink()
            except OSError:
                if sys.exc_info()[0] is None:raise

LOCK_SCHEMA_VERSION="gen3-content-run-lock/v1"; RECOVERY_SCHEMA_VERSION="gen3-content-lock-recovery/v1"
_RECOVERY_REASONS=frozenset({"external_timeout","interrupted_process"})
def _lock_payload(run:Path,policy:AdmissionPolicy,decision:RangeAdmissionDecision,snapshot:CorpusSnapshot,pid:int,created_at:datetime):
    return {"schema_version":LOCK_SCHEMA_VERSION,"run_id":run.name,"policy_hash":policy.policy_hash,"decision_hash":decision.decision_hash,"snapshot_hash":snapshot.snapshot_hash,"pid":pid,"created_at":created_at.astimezone(timezone.utc).isoformat()}
def _lock_bytes(run:Path,policy:AdmissionPolicy,decision:RangeAdmissionDecision,snapshot:CorpusSnapshot)->bytes:
    now=datetime.now(timezone.utc);payload=_lock_payload(run,policy,decision,snapshot,os.getpid(),now);payload["lock_hash"]=_hash(payload);return _json(payload)
def _parse_lock(raw:bytes,run:Path,policy:AdmissionPolicy,decision:RangeAdmissionDecision,snapshot:CorpusSnapshot)->dict[str,object]:
    try:value=json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError,json.JSONDecodeError) as exc:raise ValueError("legacy or invalid content lock") from exc
    fields={"schema_version","run_id","policy_hash","decision_hash","snapshot_hash","pid","created_at","lock_hash"}
    if not isinstance(value,dict) or set(value)!=fields:raise ValueError("content lock strict schema")
    if value["schema_version"]!=LOCK_SCHEMA_VERSION or value["run_id"]!=run.name or value["policy_hash"]!=policy.policy_hash or value["decision_hash"]!=decision.decision_hash or value["snapshot_hash"]!=snapshot.snapshot_hash or type(value["pid"]) is not int or value["pid"]<1:raise ValueError("content lock binding invalid")
    if not isinstance(value["created_at"],str):raise ValueError("content lock created_at invalid")
    try:stamp=datetime.fromisoformat(value["created_at"])
    except ValueError as exc:raise ValueError("content lock created_at invalid") from exc
    if stamp.tzinfo is None or stamp.utcoffset() is None or stamp.astimezone(timezone.utc).isoformat()!=value["created_at"]:raise ValueError("content lock created_at must be UTC")
    supplied=value.pop("lock_hash");
    if not isinstance(supplied,str) or supplied!=_hash(value):raise ValueError("content lock hash invalid")
    value["lock_hash"]=supplied;return value
def _pid_active(pid:int)->bool:
    try:os.kill(pid,0)
    except ProcessLookupError:return False
    except PermissionError:raise ValueError("cannot verify lock process state")
    except OSError:raise ValueError("cannot verify lock process state")
    return True
def _lock_hash(raw:bytes)->str:return "sha256:"+sha256(raw).hexdigest()
def _recovery_payload(run:Path,policy:AdmissionPolicy,decision:RangeAdmissionDecision,snapshot:CorpusSnapshot,lock_hash:str,reason:str,recovered_at:datetime):
    return {"schema_version":RECOVERY_SCHEMA_VERSION,"run_id":run.name,"policy_hash":policy.policy_hash,"decision_hash":decision.decision_hash,"snapshot_hash":snapshot.snapshot_hash,"lock_hash":lock_hash,"reason":reason,"recovered_at":recovered_at.astimezone(timezone.utc).isoformat()}


def _entry_data(item:ContentFileEntry):item.verify();return item.payload()|{"entry_hash":item.entry_hash}
def _entry_load(value:object)->ContentFileEntry:
    fields={"symbol","selected_rows","min_session","max_session","content_hash","policy_hash","decision_hash","snapshot_hash","source_contract_hash","corpus_entry_hash","entry_hash"}
    if not isinstance(value,dict) or set(value)!=fields:raise ValueError("content entry strict schema")
    def parse(v):
        if v is None:return None
        if not isinstance(v,str):raise ValueError("content date must be ISO or null")
        try:return date.fromisoformat(v)
        except ValueError as exc:raise ValueError("content date must be ISO or null") from exc
    result=ContentFileEntry(value["symbol"],value["selected_rows"],parse(value["min_session"]),parse(value["max_session"]),value["content_hash"],value["policy_hash"],value["decision_hash"],value["snapshot_hash"],value["source_contract_hash"],value["corpus_entry_hash"],value["entry_hash"]);result.verify();return result


@dataclass(frozen=True)
class ContentRunStatus:
    run_dir:str; completed_files:int; total_files:int; status:str; content_snapshot_hash:str|None
    def as_dict(self):return self.__dict__.copy()


def _run_id(decision:RangeAdmissionDecision)->str:return "market-content-"+sha256(_json({"policy":decision.policy_hash,"decision":decision.decision_hash})).hexdigest()
def _policy_data(policy:AdmissionPolicy): policy.verify(); return policy.payload()|{"policy_hash":policy.policy_hash}
def _decision_data(decision:RangeAdmissionDecision): decision.verify(); return decision.payload()|{"decision_hash":decision.decision_hash}
def _verify_content_run(run:Path,policy:AdmissionPolicy,decision:RangeAdmissionDecision,snapshot:CorpusSnapshot)->None:
    policy.verify();decision.verify();snapshot.verify()
    if policy.snapshot_hash!=snapshot.snapshot_hash or decision.policy_hash!=policy.policy_hash:raise ValueError("content policy/decision/snapshot binding is invalid")
    required={"entries","recoveries","policy.json","decision.json","snapshot.json"}
    try:current={p.name for p in run.iterdir()}
    except OSError as exc:raise ValueError("content run identity is invalid") from exc
    if run.name!=_run_id(decision) or not (run/"entries").is_dir() or not (run/"recoveries").is_dir() or current-required-{".lock"}: raise ValueError("content run identity is invalid")
    try:
        stored_policy=json.loads((run/"policy.json").read_text(encoding="utf-8"));stored_decision=json.loads((run/"decision.json").read_text(encoding="utf-8"));stored_snapshot=json.loads((run/"snapshot.json").read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError) as exc:raise ValueError("content run metadata is invalid") from exc
    if _json(stored_policy)!=_json(_policy_data(policy)) or _json(stored_decision)!=_json(_decision_data(decision)) or stored_snapshot!={"snapshot_hash":snapshot.snapshot_hash,"symbols":[e.symbol for e in snapshot.files]}:raise ValueError("content run metadata binding is invalid")

def _migration_payload(run:Path,policy:AdmissionPolicy,decision:RangeAdmissionDecision,snapshot:CorpusSnapshot)->dict[str,object]:
    return {"schema_version":"gen3-content-run-migration/v1","run_id":run.name,"policy_hash":policy.policy_hash,"decision_hash":decision.decision_hash,"snapshot_hash":snapshot.snapshot_hash,"from":"v1","to":"v2"}

def _verify_migration_receipt(path:Path,payload:dict[str,object])->None:
    try:value=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError) as exc:raise ValueError("migration receipt invalid") from exc
    if not isinstance(value,dict):raise ValueError("migration receipt invalid")
    supplied=value.pop("receipt_hash",None)
    if not isinstance(supplied,str) or supplied!=_hash(value) or value!=payload:raise ValueError("migration receipt differs")
def load_content_run_state(run_dir:str|Path,contract:LocalParquetFileContract,*,allowed_output_root:str|Path,quality_run_dir:str|Path,quality_allowed_output_root:str|Path)->tuple[Path,CorpusSnapshot,AdmissionPolicy,RangeAdmissionDecision]:
    """Load existing write-once state without recomputing research-window rows."""
    run=Path(run_dir).resolve();allowed=Path(allowed_output_root).resolve()
    try:run.relative_to(allowed)
    except ValueError as exc:raise ValueError("content run escapes allowed output") from exc
    _,snapshot,_,_=_load_run(quality_run_dir,contract,allowed_output_root=quality_allowed_output_root)
    try:policy=policy_from_data(json.loads((run/"policy.json").read_text(encoding="utf-8")));decision=decision_from_data(json.loads((run/"decision.json").read_text(encoding="utf-8")));stored=json.loads((run/"snapshot.json").read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError,ValueError) as exc:raise ValueError("content run metadata is invalid") from exc
    if stored!={"snapshot_hash":snapshot.snapshot_hash,"symbols":[e.symbol for e in snapshot.files]}:raise ValueError("content snapshot binding is invalid")
    _bind(policy,decision,snapshot,contract);return run,snapshot,policy,decision

def migrate_content_run(run_dir:str|Path,snapshot:CorpusSnapshot,policy:AdmissionPolicy,decision:RangeAdmissionDecision,contract:LocalParquetFileContract,*,allowed_output_root:str|Path,confirm_v1_to_v2:bool)->Path:
    if not confirm_v1_to_v2:raise ValueError("migration requires confirm_v1_to_v2")
    run=Path(run_dir).resolve();allowed=Path(allowed_output_root).resolve()
    try:run.relative_to(allowed)
    except ValueError as exc:raise ValueError("content run escapes allowed output") from exc
    _bind(policy,decision,snapshot,contract)
    required={"entries","policy.json","decision.json","snapshot.json"}
    try:current={p.name for p in run.iterdir()}
    except OSError as exc:raise ValueError("v1 run root artifact set is invalid") from exc
    if current-required-{"recoveries"}:raise ValueError("v1 run has unexpected root artifact")
    recovery_dir=run/"recoveries"; payload=_migration_payload(run,policy,decision,snapshot); receipt_path=recovery_dir/"v1-to-v2.json"
    if "recoveries" in current:
        if not recovery_dir.is_dir():raise ValueError("partial migration recovery directory is invalid")
        contents={p.name for p in recovery_dir.iterdir()}
        if contents not in (set(),{"v1-to-v2.json"}):raise ValueError("partial migration recovery directory is invalid")
        if contents=={"v1-to-v2.json"}:_verify_migration_receipt(receipt_path,payload)
    elif current!=required:raise ValueError("v1 run root artifact set is invalid")
    if (run/".lock").exists() or any(p.name.endswith(".tmp") for p in (run/"entries").iterdir()):raise ValueError("v1 migration blocked by lock or tmp")
    # Validate existing metadata and every write-once entry before recording migration.
    try:sp=json.loads((run/"snapshot.json").read_text(encoding="utf-8"));pp=policy_from_data(json.loads((run/"policy.json").read_text(encoding="utf-8")));dd=decision_from_data(json.loads((run/"decision.json").read_text(encoding="utf-8")))
    except (OSError,json.JSONDecodeError,ValueError) as exc:raise ValueError("v1 metadata invalid") from exc
    if sp!={"snapshot_hash":snapshot.snapshot_hash,"symbols":[e.symbol for e in snapshot.files]} or pp!=policy or dd!=decision or run.name!=_run_id(decision):raise ValueError("v1 metadata binding invalid")
    _read_entries(run,snapshot,policy,decision,contract)
    # Creating this new directory is the sole v1->v2 mutation; all v1 files stay untouched.
    if not recovery_dir.exists():recovery_dir.mkdir()
    receipt=payload|{"receipt_hash":_hash(payload)}
    if receipt_path.exists():_verify_migration_receipt(receipt_path,payload)
    else:_write_once(receipt_path,receipt)
    return receipt_path
def prepare_content_run(decision:RangeAdmissionDecision,policy:AdmissionPolicy,snapshot:CorpusSnapshot,contract:LocalParquetFileContract,*,workspace_output_root:str|Path,allowed_output_root:str|Path)->Path:
    _bind(policy,decision,snapshot,contract)
    if decision.status!="eligible_for_content_snapshot" or decision.policy_hash!=policy.policy_hash:raise ValueError("range admission is not eligible")
    out=_safe_output_root(workspace_output_root,allowed_output_root,contract); run=out/_run_id(decision)
    if run.exists():raise ValueError("content run already exists")
    out.mkdir(parents=True,exist_ok=True);run.mkdir();(run/"entries").mkdir();(run/"recoveries").mkdir()
    _write_once(run/"policy.json",_policy_data(policy))
    _write_once(run/"decision.json",_decision_data(decision))
    _write_once(run/"snapshot.json",{"snapshot_hash":snapshot.snapshot_hash,"symbols":[e.symbol for e in snapshot.files]})
    return run


def _read_entries(run:Path,snapshot:CorpusSnapshot,policy:AdmissionPolicy,decision:RangeAdmissionDecision,contract:LocalParquetFileContract)->tuple[ContentFileEntry,...]:
    found={}
    for p in (run/"entries").iterdir():
        if p.name.endswith(".tmp") or p.is_dir():raise ValueError("orphan content artifact")
        if p.name not in {e.symbol+".json" for e in snapshot.files}:raise ValueError("unexpected content artifact")
        try:found[p.stem]=_entry_load(json.loads(p.read_text(encoding="utf-8")))
        except (OSError,json.JSONDecodeError,ValueError) as exc:raise ValueError("invalid content entry") from exc
        entry=next(e for e in snapshot.files if e.symbol==p.stem); item=found[p.stem]
        if (item.policy_hash,item.decision_hash,item.snapshot_hash,item.source_contract_hash,item.corpus_entry_hash)!=(policy.policy_hash,decision.decision_hash,snapshot.snapshot_hash,contract.contract_hash,_hash(entry.payload())):raise ValueError("content entry binding is invalid")
        if item.min_session and (item.min_session<policy.research_start or item.max_session>policy.research_end):raise ValueError("content entry range escapes policy")
    return tuple(found[e.symbol] for e in snapshot.files if e.symbol in found)

def _status(run:Path,snapshot:CorpusSnapshot,policy:AdmissionPolicy,decision:RangeAdmissionDecision,contract:LocalParquetFileContract)->ContentRunStatus:
    entries=_read_entries(run,snapshot,policy,decision,contract)
    if not entries:return ContentRunStatus(str(run),0,len(snapshot.files),"waiting",None)
    if len(entries)<len(snapshot.files):return ContentRunStatus(str(run),len(entries),len(snapshot.files),"accumulating",None)
    if sum(e.selected_rows for e in entries)!=decision.research_rows or sum(e.selected_rows>0 for e in entries)!=decision.research_files:raise ValueError("content totals do not match admission decision")
    digest=_hash({"snapshot_hash":snapshot.snapshot_hash,"entries":[e.entry_hash for e in entries]})
    return ContentRunStatus(str(run),len(entries),len(snapshot.files),"historical_market_content_snapshot_complete",digest)

def execute_content_run(run_dir:str|Path,snapshot:CorpusSnapshot,policy:AdmissionPolicy,contract:LocalParquetFileContract,*,allowed_output_root:str|Path,max_files_this_run:int, decision:RangeAdmissionDecision|None=None)->ContentRunStatus:
    _n("max_files_this_run",max_files_this_run,1,100);run=Path(run_dir).resolve();allowed=Path(allowed_output_root).resolve()
    try:run.relative_to(allowed)
    except ValueError as exc:raise ValueError("content run escapes allowed output") from exc
    if decision is None: raise ValueError("content execution requires range admission decision")
    _bind(policy,decision,snapshot,contract); _verify_content_run(run,policy,decision,snapshot)
    lock=run/".lock"
    if lock.exists():raise ValueError("residual content lock")
    raw=_lock_bytes(run,policy,decision,snapshot)
    with open(lock,"xb") as f:f.write(raw);f.flush();os.fsync(f.fileno())
    try:
        done={e.symbol for e in _read_entries(run,snapshot,policy,decision,contract)}
        for entry in [e for e in snapshot.files if e.symbol not in done][:max_files_this_run]:
            content=build_content_file_entry(entry,contract,policy,decision,snapshot);_write_once(run/"entries"/(entry.symbol+".json"),_entry_data(content))
        return _status(run,snapshot,policy,decision,contract)
    finally:
        try:lock.unlink()
        except OSError:
            if sys.exc_info()[0] is None:raise

def content_run_status(run_dir:str|Path,snapshot:CorpusSnapshot,contract:LocalParquetFileContract,*,allowed_output_root:str|Path,policy:AdmissionPolicy|None=None,decision:RangeAdmissionDecision|None=None)->ContentRunStatus:
    run=Path(run_dir).resolve();allowed=Path(allowed_output_root).resolve()
    try:run.relative_to(allowed)
    except ValueError as exc:raise ValueError("content run escapes allowed output") from exc
    if policy is None or decision is None:raise ValueError("content status requires policy and decision")
    _bind(policy,decision,snapshot,contract); _verify_content_run(run,policy,decision,snapshot)
    if (run/".lock").exists():raise ValueError("residual content lock")
    return _status(run,snapshot,policy,decision,contract)

def recover_content_run_lock(run_dir:str|Path,snapshot:CorpusSnapshot,policy:AdmissionPolicy,decision:RangeAdmissionDecision,contract:LocalParquetFileContract,*,allowed_output_root:str|Path,expected_lock_sha256:str,reason:str,confirm_process_terminated:bool,allow_legacy_lock:bool=False)->Path:
    if not confirm_process_terminated:raise ValueError("lock recovery requires confirm_process_terminated")
    if reason not in _RECOVERY_REASONS:raise ValueError("lock recovery reason is invalid")
    _h("expected_lock_sha256",expected_lock_sha256);_bind(policy,decision,snapshot,contract)
    run=Path(run_dir).resolve();allowed=Path(allowed_output_root).resolve()
    try:run.relative_to(allowed)
    except ValueError as exc:raise ValueError("content run escapes allowed output") from exc
    _verify_content_run(run,policy,decision,snapshot);_read_entries(run,snapshot,policy,decision,contract)
    lock=run/".lock"
    if not lock.is_file():raise ValueError("no content lock to recover")
    raw=lock.read_bytes(); actual=_lock_hash(raw)
    if actual!=expected_lock_sha256:raise ValueError("expected lock hash does not match")
    try:meta=_parse_lock(raw,run,policy,decision,snapshot)
    except ValueError:
        if raw!=b"lock" or not allow_legacy_lock:raise
        meta=None
    if meta is not None and _pid_active(meta["pid"]):raise ValueError("lock process is still active")
    receipt_path=run/"recoveries"/(actual.removeprefix("sha256:")+".json")
    payload=_recovery_payload(run,policy,decision,snapshot,actual,reason,datetime.now(timezone.utc)); receipt=payload|{"receipt_hash":_hash(payload)}
    if receipt_path.exists():
        try:existing=json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError,json.JSONDecodeError) as exc:raise ValueError("recovery receipt invalid") from exc
        supplied=existing.pop("receipt_hash",None)
        if not isinstance(supplied,str) or supplied!=_hash(existing) or {k:existing.get(k) for k in ("schema_version","run_id","policy_hash","decision_hash","snapshot_hash","lock_hash","reason")} != {k:payload[k] for k in ("schema_version","run_id","policy_hash","decision_hash","snapshot_hash","lock_hash","reason")}:raise ValueError("recovery receipt binding differs")
    else:_write_once(receipt_path,receipt)
    if lock.read_bytes()!=raw:raise ValueError("lock changed before recovery deletion")
    lock.unlink();return receipt_path
