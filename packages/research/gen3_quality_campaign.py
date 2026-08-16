"""Bounded, read-only market-corpus quality campaign contracts.

The normal entry point creates a metadata snapshot only.  Full row audits are
available only to an explicit in-memory runner and are intentionally not wired
to the CLI.  This prevents a dry-run from accidentally scanning a multi-GB
local cache.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Iterable, Mapping

from .gen3_local_market import LocalParquetFileContract, _FILE_RE, _market_mapping
from .gen3_quality import QualityAuditReport, audit_local_market_file


QUALITY_CAMPAIGN_SCHEMA_VERSION = "gen3-quality-campaign-draft/v1"
WRITE_POLICY = "no_source_mutation"
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _hash(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + sha256(encoded).hexdigest()


def _hash_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{name} must match sha256:<64 lowercase hex>")
    return value


def _positive_limit(name: str, value: object, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be a non-boolean integer from 1 through {maximum}")
    return value


def _path_text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("path must be a non-empty string")
    return value


@dataclass(frozen=True)
class CorpusFileEntry:
    """Footer-only identity for one contract-root direct child file."""

    file_path: str
    symbol: str
    file_size: int
    footer_schema: str
    num_rows: int
    row_group_count: int
    row_group_rows: tuple[int, ...]

    def verify(self, *, root: Path) -> None:
        path = Path(_path_text(self.file_path)).resolve()
        if path.parent != root or path.name != f"{self.symbol}.parquet" or not _FILE_RE.fullmatch(path.name):
            raise ValueError("corpus file entry is not a direct six-digit parquet child")
        if type(self.file_size) is not int or self.file_size < 1:
            raise ValueError("corpus file size must be a positive non-boolean integer")
        if not isinstance(self.footer_schema, str) or not self.footer_schema:
            raise ValueError("corpus footer schema must be non-empty")
        if type(self.num_rows) is not int or self.num_rows < 0:
            raise ValueError("corpus footer num_rows must be a non-negative non-boolean integer")
        if type(self.row_group_count) is not int or self.row_group_count < 1:
            raise ValueError("corpus footer row_group_count must be positive")
        if len(self.row_group_rows) != self.row_group_count or any(type(value) is not int or value < 0 for value in self.row_group_rows):
            raise ValueError("corpus footer row_group rows are invalid")
        if sum(self.row_group_rows) != self.num_rows:
            raise ValueError("corpus footer row group total does not match num_rows")

    def payload(self) -> dict[str, object]:
        return {
            "file_path": self.file_path,
            "symbol": self.symbol,
            "file_size": self.file_size,
            "footer_schema": self.footer_schema,
            "num_rows": self.num_rows,
            "row_group_count": self.row_group_count,
            "row_group_rows": list(self.row_group_rows),
        }


@dataclass(frozen=True)
class CorpusSnapshot:
    """Canonical footer-only view of a finite local corpus; not a manifest."""

    root: str
    source_id: str
    contract_hash: str
    mapping_hash: str
    files: tuple[CorpusFileEntry, ...]
    snapshot_hash: str
    schema_version: str = QUALITY_CAMPAIGN_SCHEMA_VERSION

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "root": self.root,
            "source_id": self.source_id,
            "contract_hash": self.contract_hash,
            "mapping_hash": self.mapping_hash,
            "files": [entry.payload() for entry in self.files],
        }

    def verify(self) -> None:
        if self.schema_version != QUALITY_CAMPAIGN_SCHEMA_VERSION:
            raise ValueError("unexpected corpus snapshot schema_version")
        root = Path(_path_text(self.root)).resolve()
        if not isinstance(self.source_id, str) or not self.source_id or self.source_id != self.source_id.strip():
            raise ValueError("corpus snapshot source_id must be a non-empty trimmed string")
        _hash_text("contract_hash", self.contract_hash)
        _hash_text("mapping_hash", self.mapping_hash)
        _hash_text("snapshot_hash", self.snapshot_hash)
        if not self.files:
            raise ValueError("corpus snapshot cannot be empty")
        expected_paths = tuple(sorted(entry.file_path for entry in self.files))
        if tuple(entry.file_path for entry in self.files) != expected_paths:
            raise ValueError("corpus entries must be sorted by canonical path")
        symbols: set[str] = set()
        for entry in self.files:
            entry.verify(root=root)
            if entry.symbol in symbols:
                raise ValueError("corpus entries must have unique filename symbols")
            symbols.add(entry.symbol)
        if self.snapshot_hash != _hash(self.payload()):
            raise ValueError("snapshot_hash does not match canonical corpus snapshot")


def build_corpus_snapshot(contract: LocalParquetFileContract, *, max_files: int) -> CorpusSnapshot:
    """Enumerate a single root non-recursively and read only Parquet footers."""
    contract.verify()
    _positive_limit("max_files", max_files, 5_000)
    root = Path(contract.root).resolve()
    if not root.is_dir():
        raise ValueError("contract root must be an existing directory")
    candidates: list[Path] = []
    for entry in root.iterdir():
        # A malformed sibling with a parquet suffix is ambiguous and blocks the campaign.
        if entry.suffix.lower() != ".parquet":
            continue
        if entry.is_symlink() or not entry.is_file() or _FILE_RE.fullmatch(entry.name) is None:
            raise ValueError("every direct child parquet file must be a non-symlink six-digit .parquet filename")
        candidates.append(entry.resolve())
    candidates.sort(key=lambda item: str(item))
    if not candidates:
        raise ValueError("contract root has no direct six-digit parquet files")
    if len(candidates) > max_files:
        raise ValueError("contract root exceeds max_files")
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - environment specific
        raise RuntimeError("pyarrow is required for corpus footer snapshots") from exc

    entries: list[CorpusFileEntry] = []
    for path in candidates:
        entries.append(read_corpus_file_footer(contract, path))
    files = tuple(sorted(entries, key=lambda item: item.file_path))
    snapshot = CorpusSnapshot(
        root=str(root), source_id=contract.source_id, contract_hash=contract.contract_hash,
        mapping_hash=_market_mapping(contract).mapping_hash, files=files,
        snapshot_hash="sha256:" + "0" * 64,
    )
    result = CorpusSnapshot(**{**snapshot.__dict__, "snapshot_hash": _hash(snapshot.payload())})
    result.verify()
    return result


def read_corpus_file_footer(contract: LocalParquetFileContract, explicit_file: str | Path) -> CorpusFileEntry:
    """Read only one direct-child file's footer and bind it to the contract.

    This helper intentionally does not enumerate the root or read Parquet row
    data.  It is shared by snapshot creation and run-time pre/post audit checks.
    """
    contract.verify()
    root, path = Path(contract.root).resolve(), Path(explicit_file).resolve()
    match = _FILE_RE.fullmatch(path.name)
    if path.suffix.lower() != ".parquet" or not path.is_file() or path.is_symlink() or path.parent != root or match is None:
        raise ValueError("explicit footer file must be a direct non-symlink six-digit parquet child")
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - environment specific
        raise RuntimeError("pyarrow is required for corpus footer snapshots") from exc
    parquet = pq.ParquetFile(path)
    required_columns = {contract.date_column, contract.open_column, contract.high_column, contract.low_column, contract.close_column, contract.volume_column}
    if not required_columns <= set(parquet.schema.names):
        raise ValueError("corpus parquet footer is missing contract-mapped columns")
    metadata = parquet.metadata
    result = CorpusFileEntry(
        file_path=str(path), symbol=match.group("symbol"), file_size=path.stat().st_size,
        # ``ParquetSchema.__str__`` embeds a process-local object address; the
        # Arrow schema text is stable for equivalent footer schemas.
        footer_schema=str(parquet.schema_arrow), num_rows=metadata.num_rows,
        row_group_count=metadata.num_row_groups,
        row_group_rows=tuple(metadata.row_group(index).num_rows for index in range(metadata.num_row_groups)),
    )
    result.verify(root=root)
    return result


@dataclass(frozen=True)
class CampaignContract:
    """A non-formal, no-mutation campaign configuration bound to one snapshot."""

    snapshot_hash: str
    max_rows_per_file: int
    max_issues_per_file: int
    write_policy: str
    campaign_hash: str
    is_formal_registration: bool = False
    schema_version: str = QUALITY_CAMPAIGN_SCHEMA_VERSION

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "snapshot_hash": self.snapshot_hash,
            "max_rows_per_file": self.max_rows_per_file,
            "max_issues_per_file": self.max_issues_per_file,
            "write_policy": self.write_policy,
            "is_formal_registration": self.is_formal_registration,
        }

    def verify(self) -> None:
        if self.schema_version != QUALITY_CAMPAIGN_SCHEMA_VERSION or self.is_formal_registration:
            raise ValueError("campaign contract must remain a non-formal draft")
        _hash_text("campaign snapshot_hash", self.snapshot_hash)
        _hash_text("campaign_hash", self.campaign_hash)
        _positive_limit("max_rows_per_file", self.max_rows_per_file, 10_000)
        _positive_limit("max_issues_per_file", self.max_issues_per_file, 1_000)
        if self.write_policy != WRITE_POLICY:
            raise ValueError("campaign write_policy must be no_source_mutation")
        if self.campaign_hash != _hash(self.payload()):
            raise ValueError("campaign_hash does not match canonical campaign contract")


def make_campaign_contract(
    snapshot: CorpusSnapshot, *, max_rows_per_file: int, max_issues_per_file: int,
) -> CampaignContract:
    snapshot.verify()
    provisional = CampaignContract(
        snapshot_hash=snapshot.snapshot_hash, max_rows_per_file=max_rows_per_file,
        max_issues_per_file=max_issues_per_file, write_policy=WRITE_POLICY,
        campaign_hash="sha256:" + "0" * 64,
    )
    result = CampaignContract(**{**provisional.__dict__, "campaign_hash": _hash(provisional.payload())})
    result.verify()
    return result


def _issue_code_counts(reports: Iterable[QualityAuditReport]) -> tuple[tuple[str, int], ...]:
    counts: dict[str, int] = {}
    for report in reports:
        for issue in report.issues:
            counts[issue.issue_code] = counts.get(issue.issue_code, 0) + 1
    return tuple(sorted(counts.items()))


@dataclass(frozen=True)
class AggregatedCampaignReport:
    """One-to-one quality results for a snapshot, held entirely in memory."""

    snapshot_hash: str
    campaign_hash: str
    source_id: str
    reports: tuple[QualityAuditReport, ...]
    total_rows_scanned: int
    total_valid_rows: int
    total_issues_encountered: int
    retained_issue_code_counts: tuple[tuple[str, int], ...]
    status: str
    aggregate_hash: str
    schema_version: str = QUALITY_CAMPAIGN_SCHEMA_VERSION

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "snapshot_hash": self.snapshot_hash,
            "campaign_hash": self.campaign_hash,
            "source_id": self.source_id,
            "report_hashes": [report.report_hash for report in self.reports],
            "total_rows_scanned": self.total_rows_scanned,
            "total_valid_rows": self.total_valid_rows,
            "total_issues_encountered": self.total_issues_encountered,
            "retained_issue_code_counts": [list(item) for item in self.retained_issue_code_counts],
            "status": self.status,
        }

    def verify(self, *, snapshot: CorpusSnapshot, campaign: CampaignContract) -> None:
        snapshot.verify(); campaign.verify()
        if self.schema_version != QUALITY_CAMPAIGN_SCHEMA_VERSION:
            raise ValueError("unexpected aggregate report schema_version")
        if self.snapshot_hash != snapshot.snapshot_hash or self.campaign_hash != campaign.campaign_hash:
            raise ValueError("aggregate report does not bind supplied snapshot/campaign")
        if campaign.snapshot_hash != snapshot.snapshot_hash:
            raise ValueError("campaign does not bind supplied snapshot")
        _hash_text("aggregate_hash", self.aggregate_hash)
        expected_paths = tuple(entry.file_path for entry in snapshot.files)
        if tuple(report.file_path for report in self.reports) != expected_paths:
            raise ValueError("aggregate reports must be complete, unique, and sorted exactly as snapshot files")
        if not isinstance(self.source_id, str) or not self.source_id or self.source_id != self.source_id.strip():
            raise ValueError("aggregate source_id must be a non-empty trimmed string")
        for entry, report in zip(snapshot.files, self.reports):
            report.verify()
            if (
                report.source_id != self.source_id or report.source_id != snapshot.source_id or report.symbol != entry.symbol
                or report.contract_hash != snapshot.contract_hash or report.mapping_hash != snapshot.mapping_hash
                or report.file_size != entry.file_size or report.file_path != entry.file_path
            ):
                raise ValueError("aggregate report has mixed source, file, contract, mapping, or size")
        for name in ("total_rows_scanned", "total_valid_rows", "total_issues_encountered"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative non-boolean integer")
        if self.total_rows_scanned != sum(report.rows_scanned for report in self.reports) or self.total_valid_rows != sum(report.valid_rows for report in self.reports) or self.total_issues_encountered != sum(report.issues_encountered for report in self.reports):
            raise ValueError("aggregate counts do not match reports")
        if self.retained_issue_code_counts != _issue_code_counts(self.reports):
            raise ValueError("aggregate retained issue codes do not match reports")
        if any(not isinstance(code, str) or type(count) is not int or count < 1 for code, count in self.retained_issue_code_counts):
            raise ValueError("aggregate issue code counts are invalid")
        admitted = all(
            not report.truncated and not report.truncated_issues and not report.issues
            and report.rows_scanned == entry.num_rows and report.valid_rows == entry.num_rows
            for entry, report in zip(snapshot.files, self.reports)
        )
        expected_status = "admitted" if admitted else "blocked_with_quarantine"
        if self.status != expected_status:
            raise ValueError("aggregate status does not match admission conditions")
        if self.aggregate_hash != _hash(self.payload()):
            raise ValueError("aggregate_hash does not match canonical aggregate report")


def _aggregate_payload_with_source(
    snapshot: CorpusSnapshot, campaign: CampaignContract, reports: tuple[QualityAuditReport, ...], source_id: str,
) -> AggregatedCampaignReport:
    """Build only after validating all report attribution against explicit source."""
    if not isinstance(source_id, str) or not source_id:
        raise ValueError("campaign source_id must be non-empty")
    expected_paths = tuple(entry.file_path for entry in snapshot.files)
    if tuple(report.file_path for report in reports) != expected_paths:
        raise ValueError("aggregate reports must be complete, unique, and sorted exactly as snapshot files")
    for entry, report in zip(snapshot.files, reports):
        report.verify()
        if report.source_id != source_id or report.symbol != entry.symbol or report.contract_hash != snapshot.contract_hash or report.mapping_hash != snapshot.mapping_hash or report.file_size != entry.file_size:
            raise ValueError("aggregate report has mixed source, file, contract, mapping, or size")
    provisional = AggregatedCampaignReport(
        snapshot_hash=snapshot.snapshot_hash, campaign_hash=campaign.campaign_hash, source_id=source_id, reports=reports,
        total_rows_scanned=sum(report.rows_scanned for report in reports),
        total_valid_rows=sum(report.valid_rows for report in reports),
        total_issues_encountered=sum(report.issues_encountered for report in reports),
        retained_issue_code_counts=_issue_code_counts(reports),
        status="admitted" if all(not report.truncated and not report.truncated_issues and not report.issues and report.rows_scanned == entry.num_rows and report.valid_rows == entry.num_rows for entry, report in zip(snapshot.files, reports)) else "blocked_with_quarantine",
        aggregate_hash="sha256:" + "0" * 64,
    )
    return provisional


def aggregate_campaign_reports(
    snapshot: CorpusSnapshot, campaign: CampaignContract, contract: LocalParquetFileContract,
    reports: Iterable[QualityAuditReport],
) -> AggregatedCampaignReport:
    """Aggregate explicit reports; missing, duplicate, foreign, or stale reports fail closed."""
    snapshot.verify(); campaign.verify(); contract.verify()
    if snapshot.source_id != contract.source_id or snapshot.contract_hash != contract.contract_hash or snapshot.mapping_hash != _market_mapping(contract).mapping_hash or snapshot.root != str(Path(contract.root).resolve()):
        raise ValueError("snapshot does not match explicit local market contract")
    if campaign.snapshot_hash != snapshot.snapshot_hash:
        raise ValueError("campaign does not bind supplied snapshot")
    values = tuple(sorted(tuple(reports), key=lambda report: report.file_path))
    provisional = _aggregate_payload_with_source(snapshot, campaign, values, contract.source_id)
    result = AggregatedCampaignReport(**{**provisional.__dict__, "aggregate_hash": _hash(provisional.payload())})
    # Inline validation carries the explicit source, avoiding any source inference.
    _verify_aggregate(result, snapshot=snapshot, campaign=campaign, source_id=contract.source_id)
    return result


def _verify_aggregate(
    report: AggregatedCampaignReport, *, snapshot: CorpusSnapshot, campaign: CampaignContract, source_id: str,
) -> None:
    """Full verifier used by the construction path and explicit callers."""
    snapshot.verify(); campaign.verify()
    if report.schema_version != QUALITY_CAMPAIGN_SCHEMA_VERSION or report.snapshot_hash != snapshot.snapshot_hash or report.campaign_hash != campaign.campaign_hash or campaign.snapshot_hash != snapshot.snapshot_hash:
        raise ValueError("aggregate report does not bind supplied snapshot/campaign")
    if report.source_id != source_id or report.source_id != snapshot.source_id:
        raise ValueError("aggregate report source does not match explicit contract")
    _hash_text("aggregate_hash", report.aggregate_hash)
    if tuple(item.file_path for item in report.reports) != tuple(item.file_path for item in snapshot.files):
        raise ValueError("aggregate reports must be complete, unique, and sorted exactly as snapshot files")
    for entry, item in zip(snapshot.files, report.reports):
        item.verify()
        if item.source_id != source_id or item.symbol != entry.symbol or item.file_path != entry.file_path or item.contract_hash != snapshot.contract_hash or item.mapping_hash != snapshot.mapping_hash or item.file_size != entry.file_size:
            raise ValueError("aggregate report has mixed source, file, contract, mapping, or size")
    if any(type(getattr(report, name)) is not int or getattr(report, name) < 0 for name in ("total_rows_scanned", "total_valid_rows", "total_issues_encountered")):
        raise ValueError("aggregate counts must be non-negative non-boolean integers")
    if report.total_rows_scanned != sum(item.rows_scanned for item in report.reports) or report.total_valid_rows != sum(item.valid_rows for item in report.reports) or report.total_issues_encountered != sum(item.issues_encountered for item in report.reports):
        raise ValueError("aggregate counts do not match reports")
    if report.retained_issue_code_counts != _issue_code_counts(report.reports):
        raise ValueError("aggregate retained issue codes do not match reports")
    admitted = all(not item.truncated and not item.truncated_issues and not item.issues and item.rows_scanned == entry.num_rows and item.valid_rows == entry.num_rows for entry, item in zip(snapshot.files, report.reports))
    if report.status != ("admitted" if admitted else "blocked_with_quarantine"):
        raise ValueError("aggregate status does not match admission conditions")
    if report.aggregate_hash != _hash(report.payload()):
        raise ValueError("aggregate_hash does not match canonical aggregate report")


def verify_aggregated_campaign_report(
    report: AggregatedCampaignReport, *, snapshot: CorpusSnapshot, campaign: CampaignContract,
    contract: LocalParquetFileContract,
) -> None:
    """Verify a report with its explicit contract; no source is guessed from hashes."""
    contract.verify()
    if snapshot.source_id != contract.source_id or snapshot.contract_hash != contract.contract_hash or snapshot.mapping_hash != _market_mapping(contract).mapping_hash:
        raise ValueError("snapshot does not match explicit local market contract")
    _verify_aggregate(report, snapshot=snapshot, campaign=campaign, source_id=contract.source_id)


def run_campaign(
    snapshot: CorpusSnapshot, campaign: CampaignContract, contract: LocalParquetFileContract,
) -> AggregatedCampaignReport:
    """Explicit full-corpus runner.  It is deliberately absent from the CLI."""
    snapshot.verify(); campaign.verify(); contract.verify()
    if snapshot.source_id != contract.source_id or snapshot.contract_hash != contract.contract_hash or snapshot.mapping_hash != _market_mapping(contract).mapping_hash or snapshot.root != str(Path(contract.root).resolve()):
        raise ValueError("snapshot does not match explicit local market contract")
    reports = tuple(
        audit_local_market_file(entry.file_path, contract, campaign.max_rows_per_file, campaign.max_issues_per_file)
        for entry in snapshot.files
    )
    return aggregate_campaign_reports(snapshot, campaign, contract, reports)


def audit_snapshot_file(
    snapshot: CorpusSnapshot, campaign: CampaignContract, contract: LocalParquetFileContract,
    explicit_file: str | Path,
) -> QualityAuditReport:
    """Explicit one-file runner used by the CLI; it cannot expand to the corpus."""
    snapshot.verify(); campaign.verify(); contract.verify()
    path = Path(explicit_file).resolve()
    entry = next((item for item in snapshot.files if item.file_path == str(path)), None)
    if entry is None:
        raise ValueError("audit_file must be one explicit file in the supplied corpus snapshot")
    if snapshot.source_id != contract.source_id or snapshot.contract_hash != contract.contract_hash or snapshot.mapping_hash != _market_mapping(contract).mapping_hash:
        raise ValueError("snapshot does not match explicit local market contract")
    return audit_local_market_file(path, contract, campaign.max_rows_per_file, campaign.max_issues_per_file)
