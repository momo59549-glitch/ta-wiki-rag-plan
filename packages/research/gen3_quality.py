"""Read-only, quarantine-only quality isolation for local market Parquet files.

This module is intentionally not a repair tool.  It reads one explicitly named
file through a :class:`LocalParquetFileContract`, records at most one stable
issue per row, and never writes a source file, cache, manifest, trial ledger,
or feature.  A later workflow may obtain an independently sourced replacement,
but even verified replacement evidence does not alter the original file or
release it from quarantine.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import re
from typing import Mapping

from .gen3_local_market import LocalParquetFileContract, _FILE_RE, _market_mapping
from .gen3_policy import DataClass
from .gen3_rows import CanonicalRow, canonicalize_and_validate_row


QUALITY_SCHEMA_VERSION = "gen3-quality-draft/v1"
REPLACEMENT_SCHEMA_VERSION = "gen3-replacement-evidence-draft/v1"
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ISSUE_CODES = frozenset(
    {
        "null",
        "non_finite",
        "non_positive_price",
        "negative_volume",
        "ohlc_bounds",
        "duplicate_session",
        "non_monotonic_session",
        "invalid_session_type",
    }
)


def _canonical_value(value: object) -> object:
    """Encode evidence deterministically without allowing JSON NaN literals."""
    if isinstance(value, datetime):
        if value.tzinfo is not None and value.utcoffset() is not None:
            return value.astimezone(timezone.utc).isoformat()
        return value.isoformat()
    if type(value) is date:
        return value.isoformat()
    if type(value) is float:
        if value != value:
            return {"__float__": "NaN"}
        if value == float("inf"):
            return {"__float__": "+Inf"}
        if value == float("-inf"):
            return {"__float__": "-Inf"}
    if isinstance(value, tuple):
        return [_canonical_value(item) for item in value]
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    return value


def _hash(value: object) -> str:
    payload = json.dumps(
        _canonical_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return "sha256:" + sha256(payload).hexdigest()


def _text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty trimmed string")
    return value


def _hash_text(name: str, value: object, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{name} must match sha256:<64 lowercase hex>")
    return value


def _utc_datetime(name: str, value: object, *, require_utc: bool) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    result = value.astimezone(timezone.utc)
    if require_utc and (value.utcoffset() != timezone.utc.utcoffset(value) or value.tzinfo != timezone.utc):
        raise ValueError(f"{name} must be canonical UTC")
    return result


def _issue_payload(
    *, source_id: str, symbol: str, session: date | None, row_number: int,
    issue_code: str, original_row_hash: str | None, evidence_hash: str, details_code: str,
) -> dict[str, object]:
    return {
        "schema_version": QUALITY_SCHEMA_VERSION,
        "source_id": source_id,
        "symbol": symbol,
        "session": session.isoformat() if session is not None else None,
        "row_number": row_number,
        "domain": DataClass.MARKET.value,
        "issue_code": issue_code,
        "original_row_hash": original_row_hash,
        "evidence_hash": evidence_hash,
        "details_code": details_code,
    }


@dataclass(frozen=True)
class DataQualityIssue:
    """One immutable, prioritized quality finding for one scanned source row."""

    source_id: str
    symbol: str
    session: date | None
    row_number: int
    issue_code: str
    original_row_hash: str | None
    evidence_hash: str
    details_code: str
    issue_hash: str
    domain: DataClass = DataClass.MARKET
    schema_version: str = QUALITY_SCHEMA_VERSION

    def payload(self) -> dict[str, object]:
        return _issue_payload(
            source_id=self.source_id, symbol=self.symbol, session=self.session,
            row_number=self.row_number, issue_code=self.issue_code,
            original_row_hash=self.original_row_hash, evidence_hash=self.evidence_hash,
            details_code=self.details_code,
        )

    def verify(self) -> None:
        if self.schema_version != QUALITY_SCHEMA_VERSION or self.domain is not DataClass.MARKET:
            raise ValueError("invalid quality issue schema or domain")
        _text("source_id", self.source_id)
        if not re.fullmatch(r"[0-9]{6}", self.symbol):
            raise ValueError("issue symbol must be a six-digit filename symbol")
        if self.session is not None and type(self.session) is not date:
            raise ValueError("issue session must be a date or None")
        if type(self.row_number) is not int or self.row_number < 1:
            raise ValueError("issue row_number must be a positive non-boolean integer")
        if self.issue_code not in _ISSUE_CODES or self.details_code != self.issue_code:
            raise ValueError("issue details_code must exactly equal a supported issue_code")
        _hash_text("original_row_hash", self.original_row_hash, nullable=True)
        _hash_text("evidence_hash", self.evidence_hash)
        _hash_text("issue_hash", self.issue_hash)
        if self.issue_hash != _hash(self.payload()):
            raise ValueError("issue_hash does not match canonical issue payload")

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> "DataQualityIssue":
        required = {
            "source_id", "symbol", "session", "row_number", "issue_code", "original_row_hash",
            "evidence_hash", "details_code", "issue_hash", "domain", "schema_version",
        }
        if set(values) != required:
            raise ValueError("quality issue fields must exactly match the strict schema")
        issue = cls(**values)  # type: ignore[arg-type]
        issue.verify()
        return issue


def _make_issue(
    *, source_id: str, symbol: str, session: date | None, row_number: int,
    issue_code: str, evidence: Mapping[str, object], original_row_hash: str | None = None,
) -> DataQualityIssue:
    evidence_hash = _hash(evidence)
    provisional = DataQualityIssue(
        source_id=source_id, symbol=symbol, session=session, row_number=row_number,
        issue_code=issue_code, original_row_hash=original_row_hash,
        evidence_hash=evidence_hash, details_code=issue_code,
        issue_hash="sha256:" + "0" * 64,
    )
    result = DataQualityIssue(**{**provisional.__dict__, "issue_hash": _hash(provisional.payload())})
    result.verify()
    return result


def _report_payload(report: "QualityAuditReport") -> dict[str, object]:
    return {
        "schema_version": report.schema_version,
        "source_id": report.source_id,
        "symbol": report.symbol,
        "file_path": report.file_path,
        "contract_hash": report.contract_hash,
        "mapping_hash": report.mapping_hash,
        "file_size": report.file_size,
        "rows_scanned": report.rows_scanned,
        "valid_rows": report.valid_rows,
        "truncated": report.truncated,
        "issue_hashes": [issue.issue_hash for issue in report.issues],
        "issues_encountered": report.issues_encountered,
        "truncated_issues": report.truncated_issues,
        "min_session": report.min_session.isoformat() if report.min_session else None,
        "max_session": report.max_session.isoformat() if report.max_session else None,
        "status": report.status,
    }


@dataclass(frozen=True)
class QualityAuditReport:
    """A bounded, read-only audit outcome.  Quarantine is informational only."""

    source_id: str
    symbol: str
    file_path: str
    contract_hash: str
    mapping_hash: str
    file_size: int
    rows_scanned: int
    valid_rows: int
    truncated: bool
    issues: tuple[DataQualityIssue, ...]
    issues_encountered: int
    truncated_issues: bool
    min_session: date | None
    max_session: date | None
    status: str
    report_hash: str
    schema_version: str = QUALITY_SCHEMA_VERSION

    def verify(self) -> None:
        if self.schema_version != QUALITY_SCHEMA_VERSION:
            raise ValueError("unexpected quality report schema_version")
        _text("source_id", self.source_id)
        if not re.fullmatch(r"[0-9]{6}", self.symbol) or not isinstance(self.file_path, str) or not self.file_path:
            raise ValueError("invalid quality report attribution")
        if Path(self.file_path).name != f"{self.symbol}.parquet":
            raise ValueError("quality report file path does not match its filename symbol")
        _hash_text("contract_hash", self.contract_hash)
        _hash_text("mapping_hash", self.mapping_hash)
        _hash_text("report_hash", self.report_hash)
        if type(self.file_size) is not int or self.file_size < 1:
            raise ValueError("file_size must be a positive non-boolean integer")
        if type(self.rows_scanned) is not int or self.rows_scanned < 0:
            raise ValueError("rows_scanned must be a non-negative non-boolean integer")
        if type(self.valid_rows) is not int or not 0 <= self.valid_rows <= self.rows_scanned:
            raise ValueError("valid_rows must be within rows_scanned")
        if type(self.issues_encountered) is not int or self.issues_encountered < len(self.issues):
            raise ValueError("issues_encountered is inconsistent with retained issues")
        if self.valid_rows + self.issues_encountered != self.rows_scanned:
            raise ValueError("every scanned row must be exactly one valid row or one encountered issue")
        if type(self.truncated) is not bool or type(self.truncated_issues) is not bool:
            raise ValueError("truncation flags must be bool")
        if self.truncated_issues != (self.issues_encountered > len(self.issues)):
            raise ValueError("truncated_issues must mean an additional issue was actually encountered")
        if self.issues != tuple(sorted(self.issues, key=lambda issue: issue.issue_hash)):
            raise ValueError("issues must be sorted by issue_hash")
        if len({issue.issue_hash for issue in self.issues}) != len(self.issues):
            raise ValueError("issues must have unique issue_hash values")
        for issue in self.issues:
            issue.verify()
            if issue.source_id != self.source_id or issue.symbol != self.symbol:
                raise ValueError("quality report issues have mixed attribution")
        if self.valid_rows == 0:
            if self.min_session is not None or self.max_session is not None:
                raise ValueError("empty valid set cannot have a session range")
        elif type(self.min_session) is not date or type(self.max_session) is not date or self.min_session > self.max_session:
            raise ValueError("valid rows need an ordered date range")
        expected_status = "clean_sample" if not self.issues and not self.truncated_issues else "quarantined_sample"
        if self.status != expected_status:
            raise ValueError("quality report status does not match its isolation state")
        if self.report_hash != _hash(_report_payload(self)):
            raise ValueError("report_hash does not match canonical report payload")


def _safe_file(contract: LocalParquetFileContract, explicit_file: str | Path) -> tuple[Path, str]:
    path = Path(explicit_file).resolve()
    root = Path(contract.root).resolve()
    match = _FILE_RE.fullmatch(path.name)
    if path.suffix.lower() != ".parquet" or not path.is_file() or match is None or path.parent != root:
        raise ValueError("explicit file must be a direct child of contract root with six-digit parquet filename")
    return path, match.group("symbol")


def _evidence_for_raw(contract: LocalParquetFileContract, raw: Mapping[str, object], symbol: str) -> dict[str, object]:
    """Keep exactly six mapped source fields and filename context; ignore extras."""
    columns = (
        contract.date_column, contract.open_column, contract.high_column,
        contract.low_column, contract.close_column, contract.volume_column,
    )
    return {
        "filename_symbol": symbol,
        "source_fields": {column: raw.get(column) for column in sorted(columns)},
    }


def _session_or_issue(value: object) -> tuple[date | None, str | None]:
    if value is None:
        return None, "null"
    if isinstance(value, datetime):
        if value.tzinfo is not None or value.utcoffset() is not None:
            return None, "invalid_session_type"
        return value.date(), None
    if type(value) is date:
        return value, None
    return None, "invalid_session_type"


def _numeric_issue(contract: LocalParquetFileContract, raw: Mapping[str, object]) -> str | None:
    prices = (contract.open_column, contract.high_column, contract.low_column, contract.close_column)
    numeric = (*prices, contract.volume_column)
    for column in numeric:
        value = raw.get(column)
        if value is None:
            return "null"
        if type(value) not in (int, float) or not isfinite(value):
            return "non_finite"
    for column in prices:
        if raw[column] <= 0:  # type: ignore[operator]
            return "non_positive_price"
    if raw[contract.volume_column] < 0:  # type: ignore[operator]
        return "negative_volume"
    high, low = raw[contract.high_column], raw[contract.low_column]
    if high < max(raw[contract.open_column], low, raw[contract.close_column]) or low > min(raw[contract.open_column], high, raw[contract.close_column]):  # type: ignore[arg-type]
        return "ohlc_bounds"
    return None


def audit_local_market_file(
    explicit_file: str | Path,
    contract: LocalParquetFileContract,
    max_rows: int,
    max_issues: int,
) -> QualityAuditReport:
    """Audit one file with bounded reads and no source mutation.

    Exactly the contract's six source columns are read.  ``max_rows`` controls
    sampled rows; only observing row ``max_rows + 1`` sets ``truncated``.
    ``max_issues`` limits retained records but scanning continues so
    ``truncated_issues`` means a real additional issue was seen.
    """
    contract.verify()
    if type(max_rows) is not int or not 1 <= max_rows <= 10_000:
        raise ValueError("max_rows must be a non-boolean integer from 1 through 10000")
    if type(max_issues) is not int or not 1 <= max_issues <= 1_000:
        raise ValueError("max_issues must be a non-boolean integer from 1 through 1000")
    path, symbol = _safe_file(contract, explicit_file)
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - environment specific
        raise RuntimeError("pyarrow is required for local market quality audit") from exc

    columns = [
        contract.date_column, contract.open_column, contract.high_column,
        contract.low_column, contract.close_column, contract.volume_column,
    ]
    parquet = pq.ParquetFile(path)
    if not set(columns) <= set(parquet.schema.names):
        raise ValueError("local file is missing required columns")
    mapping = _market_mapping(contract)
    retained: list[DataQualityIssue] = []
    valid_sessions: list[date] = []
    previous_session: date | None = None
    rows_scanned = 0
    issues_encountered = 0
    truncated = False

    # The extra slot is only a probe.  It is never canonicalized or counted.
    for batch in parquet.iter_batches(batch_size=min(max_rows + 1, 10_000), columns=columns):
        for raw_value in batch.to_pylist():
            if rows_scanned == max_rows:
                truncated = True
                break
            rows_scanned += 1
            raw: dict[str, object] = dict(raw_value)
            evidence = _evidence_for_raw(contract, raw, symbol)
            session, issue_code = _session_or_issue(raw.get(contract.date_column))
            if issue_code is None:
                # This only changes the in-memory row sent to the canonicalizer.
                raw[contract.date_column] = session
                if previous_session is not None and session == previous_session:
                    issue_code = "duplicate_session"
                elif previous_session is not None and session < previous_session:
                    issue_code = "non_monotonic_session"
                previous_session = session
            if issue_code is None:
                issue_code = _numeric_issue(contract, raw)
            if issue_code is None:
                try:
                    canonicalize_and_validate_row(mapping, {**raw, "__filename_symbol": symbol})
                except ValueError as exc:
                    # All market validation branches are pre-classified above.
                    raise RuntimeError("unexpected local market canonicalization failure") from exc
                valid_sessions.append(session)  # type: ignore[arg-type]
                continue
            issues_encountered += 1
            if len(retained) < max_issues:
                retained.append(
                    _make_issue(
                        source_id=contract.source_id, symbol=symbol, session=session,
                        row_number=rows_scanned, issue_code=issue_code, evidence=evidence,
                    )
                )
        if truncated:
            break

    issues = tuple(sorted(retained, key=lambda issue: issue.issue_hash))
    report = QualityAuditReport(
        source_id=contract.source_id,
        symbol=symbol,
        file_path=str(path),
        contract_hash=contract.contract_hash,
        mapping_hash=mapping.mapping_hash,
        file_size=path.stat().st_size,
        rows_scanned=rows_scanned,
        valid_rows=len(valid_sessions),
        truncated=truncated,
        issues=issues,
        issues_encountered=issues_encountered,
        truncated_issues=issues_encountered > len(issues),
        min_session=min(valid_sessions) if valid_sessions else None,
        max_session=max(valid_sessions) if valid_sessions else None,
        status="clean_sample" if not issues and issues_encountered == 0 else "quarantined_sample",
        report_hash="sha256:" + "0" * 64,
    )
    result = QualityAuditReport(**{**report.__dict__, "report_hash": _hash(_report_payload(report))})
    result.verify()
    return result


def _replacement_payload(value: "ReplacementEvidence") -> dict[str, object]:
    return {
        "schema_version": value.schema_version,
        "issue_hash": value.issue_hash,
        "replacement_source_id": value.replacement_source_id,
        "replacement_content_hash": value.replacement_content_hash,
        "replacement_row_hash": value.replacement_row_hash,
        "observed_at": value.observed_at.isoformat(),
    }


@dataclass(frozen=True)
class ReplacementEvidence:
    """An untrusted, immutable claim about an independently sourced replacement."""

    issue_hash: str
    replacement_source_id: str
    replacement_content_hash: str
    replacement_row_hash: str
    observed_at: datetime
    evidence_hash: str
    schema_version: str = REPLACEMENT_SCHEMA_VERSION

    def verify(self, *, issue: DataQualityIssue, replacement_row: CanonicalRow) -> None:
        if self.schema_version != REPLACEMENT_SCHEMA_VERSION:
            raise ValueError("unexpected replacement evidence schema_version")
        issue.verify()
        if self.issue_hash != issue.issue_hash:
            raise ValueError("replacement evidence issue_hash does not match issue")
        _text("replacement_source_id", self.replacement_source_id)
        if self.replacement_source_id == issue.source_id:
            raise ValueError("replacement source must be independent from issue source")
        _hash_text("replacement_content_hash", self.replacement_content_hash)
        _hash_text("replacement_row_hash", self.replacement_row_hash)
        _hash_text("evidence_hash", self.evidence_hash)
        _utc_datetime("observed_at", self.observed_at, require_utc=True)
        replacement_row.verify()
        if replacement_row.domain is not DataClass.MARKET:
            raise ValueError("replacement row must be a market CanonicalRow")
        if replacement_row.source_id != self.replacement_source_id:
            raise ValueError("replacement row source does not match replacement_source_id")
        if replacement_row.row_hash != self.replacement_row_hash:
            raise ValueError("replacement row hash does not match evidence")
        if issue.session is None:
            raise ValueError("an issue without a valid session cannot receive a replacement")
        if replacement_row.mapping["symbol"] != issue.symbol:
            raise ValueError("replacement symbol does not match issue")
        if replacement_row.mapping["session"] != issue.session:
            raise ValueError("replacement session does not match issue")
        if self.evidence_hash != _hash(_replacement_payload(self)):
            raise ValueError("evidence_hash does not match canonical replacement evidence")


def build_replacement_evidence(
    *, issue: DataQualityIssue, replacement_source_id: str,
    replacement_content_hash: str, replacement_row: CanonicalRow, observed_at: datetime,
) -> ReplacementEvidence:
    """Build evidence in memory, normalizing the observation time to UTC."""
    issue.verify()
    utc_observed = _utc_datetime("observed_at", observed_at, require_utc=False)
    provisional = ReplacementEvidence(
        issue_hash=issue.issue_hash,
        replacement_source_id=replacement_source_id,
        replacement_content_hash=replacement_content_hash,
        replacement_row_hash=replacement_row.row_hash,
        observed_at=utc_observed,
        evidence_hash="sha256:" + "0" * 64,
    )
    result = ReplacementEvidence(**{**provisional.__dict__, "evidence_hash": _hash(_replacement_payload(provisional))})
    result.verify(issue=issue, replacement_row=replacement_row)
    return result


@dataclass(frozen=True)
class VerifiedReplacement:
    """Verification receipt only; it never mutates or clears the original issue."""

    issue_hash: str
    replacement_source_id: str
    replacement_content_hash: str
    replacement_row_hash: str
    observed_at: datetime
    evidence_hash: str
    verification_hash: str
    schema_version: str = REPLACEMENT_SCHEMA_VERSION

    def evidence(self) -> ReplacementEvidence:
        return ReplacementEvidence(
            issue_hash=self.issue_hash, replacement_source_id=self.replacement_source_id,
            replacement_content_hash=self.replacement_content_hash,
            replacement_row_hash=self.replacement_row_hash, observed_at=self.observed_at,
            evidence_hash=self.evidence_hash, schema_version=self.schema_version,
        )

    def verify(self, *, issue: DataQualityIssue, replacement_row: CanonicalRow) -> None:
        evidence = self.evidence()
        evidence.verify(issue=issue, replacement_row=replacement_row)
        _hash_text("verification_hash", self.verification_hash)
        expected = _hash({"schema_version": self.schema_version, "evidence_hash": self.evidence_hash})
        if self.verification_hash != expected:
            raise ValueError("verification_hash does not match verified replacement")


def verify_replacement_evidence(
    evidence: ReplacementEvidence, *, issue: DataQualityIssue, replacement_row: CanonicalRow,
) -> VerifiedReplacement:
    """Verify an independent replacement without applying it anywhere."""
    evidence.verify(issue=issue, replacement_row=replacement_row)
    provisional = VerifiedReplacement(
        issue_hash=evidence.issue_hash, replacement_source_id=evidence.replacement_source_id,
        replacement_content_hash=evidence.replacement_content_hash,
        replacement_row_hash=evidence.replacement_row_hash, observed_at=evidence.observed_at,
        evidence_hash=evidence.evidence_hash, verification_hash="sha256:" + "0" * 64,
    )
    result = VerifiedReplacement(
        **{**provisional.__dict__, "verification_hash": _hash({"schema_version": provisional.schema_version, "evidence_hash": provisional.evidence_hash})}
    )
    result.verify(issue=issue, replacement_row=replacement_row)
    return result
