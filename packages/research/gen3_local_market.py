"""Bounded single-file local-market sample adapter; no directory scanning."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
import json
from pathlib import Path
import re

from .gen3_policy import DataClass
from .gen3_providers import PROVIDER_SCHEMA_VERSION, SourceFieldMapping
from .gen3_rows import canonicalize_and_validate_row

LOCAL_MARKET_CONTRACT_VERSION = "gen3-local-market-draft/v1"
_FILE_RE = re.compile(r"^(?P<symbol>[0-9]{6})\.parquet$")
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

def _hash(value: object) -> str:
    return "sha256:" + sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

@dataclass(frozen=True)
class LocalParquetFileContract:
    source_id: str
    root: str
    mapping_hash: str
    date_column: str
    open_column: str
    high_column: str
    low_column: str
    close_column: str
    volume_column: str
    contract_hash: str
    schema_version: str = LOCAL_MARKET_CONTRACT_VERSION

    def payload(self):
        return {"schema_version": self.schema_version, "source_id": self.source_id, "root": self.root, "mapping_hash": self.mapping_hash, "filename_symbol_regex": _FILE_RE.pattern, "date_column": self.date_column, "open_column": self.open_column, "high_column": self.high_column, "low_column": self.low_column, "close_column": self.close_column, "volume_column": self.volume_column}
    def verify(self):
        if self.schema_version != LOCAL_MARKET_CONTRACT_VERSION or self.date_column not in {"date", "trade_date"}: raise ValueError("invalid local market contract")
        if any(not isinstance(x, str) or not x or x != x.strip() for x in (self.source_id, self.root, self.mapping_hash, self.open_column, self.high_column, self.low_column, self.close_column, self.volume_column)): raise ValueError("contract fields must be non-empty trimmed strings")
        columns=(self.date_column,self.open_column,self.high_column,self.low_column,self.close_column,self.volume_column)
        if len(set(columns)) != len(columns) or "__filename_symbol" in columns: raise ValueError("contract source columns must be unique and cannot use filename context")
        mapping=_market_mapping(self)
        if not _HASH_RE.fullmatch(self.mapping_hash) or self.mapping_hash != mapping.mapping_hash: raise ValueError("mapping_hash does not match explicit local mapping")
        if not _HASH_RE.fullmatch(self.contract_hash): raise ValueError("contract_hash must be sha256")
        if self.contract_hash != _hash(self.payload()): raise ValueError("contract_hash does not match contract")

def make_local_market_contract(**values):
    if "contract_hash" in values or "mapping_hash" in values: raise ValueError("contract and mapping hashes are computed")
    root=values.get("root")
    proto0 = LocalParquetFileContract(**{**values, "mapping_hash": "sha256:"+"0"*64, "contract_hash": "sha256:"+"0"*64})
    mapping=_market_mapping(proto0)
    proto = LocalParquetFileContract(**{**proto0.__dict__, "mapping_hash": mapping.mapping_hash})
    result = LocalParquetFileContract(**{**proto.__dict__, "contract_hash": _hash(proto.payload())})
    result.verify(); return result

def _market_mapping(contract: LocalParquetFileContract) -> SourceFieldMapping:
    return SourceFieldMapping.from_mapping(source_id=contract.source_id, domain=DataClass.MARKET, schema_version=PROVIDER_SCHEMA_VERSION, root=contract.root, file_format="parquet", mapping={"symbol": "__filename_symbol", "session": contract.date_column, "open": contract.open_column, "high": contract.high_column, "low": contract.low_column, "close": contract.close_column, "volume": contract.volume_column})

@dataclass(frozen=True)
class LocalMarketSample:
    source_id: str; symbol: str; file_path: str; contract_hash: str; mapping_hash: str; file_size: int; row_hashes: tuple[str, ...]; row_count: int; min_session: date; max_session: date; truncated: bool; sample_hash: str
    def verify(self):
        if any(not isinstance(x,str) or not x for x in (self.source_id,self.symbol,self.file_path)) or not _FILE_RE.fullmatch(self.symbol+".parquet"): raise ValueError("invalid sample source/symbol/path")
        if any(not isinstance(x,str) or not _HASH_RE.fullmatch(x) for x in (self.contract_hash,self.mapping_hash,self.sample_hash)) or self.file_size < 1 or type(self.row_count) is not int or self.row_count < 1 or type(self.truncated) is not bool: raise ValueError("invalid sample hashes/count")
        if len(self.row_hashes)!=self.row_count or len(set(self.row_hashes))!=len(self.row_hashes) or any(not _HASH_RE.fullmatch(x) for x in self.row_hashes) or type(self.min_session) is not date or type(self.max_session) is not date or self.min_session>self.max_session: raise ValueError("invalid sample rows/date range")
        expected=_hash({"source_id":self.source_id,"symbol":self.symbol,"file_path":self.file_path,"contract_hash":self.contract_hash,"mapping_hash":self.mapping_hash,"file_size":self.file_size,"row_hashes":list(self.row_hashes),"row_count":self.row_count,"min_session":self.min_session.isoformat(),"max_session":self.max_session.isoformat(),"truncated":self.truncated})
        if self.sample_hash != expected: raise ValueError("sample_hash does not match sample")

def inspect_local_market_file(explicit_file, contract: LocalParquetFileContract, max_rows: int) -> LocalMarketSample:
    contract.verify()
    if type(max_rows) is not int or not 1 <= max_rows <= 10000: raise ValueError("max_rows must be a positive non-boolean integer <= 10000")
    path = Path(explicit_file).resolve(); root=Path(contract.root).resolve(); match = _FILE_RE.fullmatch(path.name)
    if path.suffix.lower() != ".parquet" or not path.is_file() or not match or path.parent != root: raise ValueError("explicit file must be a direct child of contract root with six-digit parquet filename")
    try:
        import pyarrow.parquet as pq
    except ImportError as exc: raise RuntimeError("pyarrow is required") from exc
    columns = [contract.date_column, contract.open_column, contract.high_column, contract.low_column, contract.close_column, contract.volume_column]
    schema = set(pq.ParquetFile(path).schema.names)
    if not set(columns) <= schema: raise ValueError("local file is missing required columns")
    mapping = _market_mapping(contract)
    rows=[]; more=False
    for batch in pq.ParquetFile(path).iter_batches(batch_size=min(max_rows + 1, 10000), columns=columns):
        for item in batch.to_pylist():
            if len(rows) >= max_rows: more=True; break
            value=item[contract.date_column]
            if isinstance(value, datetime):
                if value.tzinfo is not None: raise ValueError("timezone datetime session is not accepted")
                value=value.date()
            if type(value) is not date: raise ValueError("session must be a date or naive timestamp")
            item["__filename_symbol"]=match.group("symbol"); rows.append(canonicalize_and_validate_row(mapping, item))
        if more: break
    if not rows: raise ValueError("local market sample has zero rows")
    dates=[row.mapping["session"] for row in rows]
    if any(later <= earlier for earlier,later in zip(dates,dates[1:])): raise ValueError("sample sessions must be strictly increasing and unique")
    hashes=tuple(row.row_hash for row in rows); payload={"source_id":contract.source_id,"symbol":match.group("symbol"),"file_path":str(path),"contract_hash":contract.contract_hash,"mapping_hash":mapping.mapping_hash,"file_size":path.stat().st_size,"row_hashes":list(hashes),"row_count":len(rows),"min_session":min(dates).isoformat(),"max_session":max(dates).isoformat(),"truncated":more}
    result=LocalMarketSample(contract.source_id,match.group("symbol"),str(path),contract.contract_hash,mapping.mapping_hash,path.stat().st_size,hashes,len(rows),min(dates),max(dates),more,_hash(payload)); result.verify(); return result
