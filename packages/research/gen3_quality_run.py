"""Resumable, no-source-mutation execution for a bounded quality campaign.

The only mutable state is a write-once run directory under an explicit workspace
output root.  Completed work is reconstructed from immutable per-symbol report
documents; no mutable counter, ledger, source file, or cache is updated.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
from typing import Mapping

from .gen3_local_market import LocalParquetFileContract, _market_mapping
from .gen3_policy import DataClass
from .gen3_quality import DataQualityIssue, QualityAuditReport
from .gen3_quality_campaign import (
    CampaignContract,
    CorpusFileEntry,
    CorpusSnapshot,
    aggregate_campaign_reports,
    audit_snapshot_file,
    build_corpus_snapshot,
    make_campaign_contract,
    read_corpus_file_footer,
)


QUALITY_RUN_SCHEMA_VERSION = "gen3-quality-run-draft/v1"
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _hash(value: object) -> str:
    return "sha256:" + sha256(_canonical_json(value)).hexdigest()


def _require_fields(value: object, fields: set[str], name: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"{name} must have exactly the strict schema fields")
    return value


def _hash_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{name} must match sha256:<64 lowercase hex>")
    return value


def _date(value: object, name: str) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        raise ValueError(f"{name} must be an ISO date or null")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO date or null") from exc


def _int(name: str, value: object, minimum: int = 0, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        if maximum is None:
            raise ValueError(f"{name} must be a non-boolean integer >= {minimum}")
        raise ValueError(f"{name} must be a non-boolean integer from {minimum} through {maximum}")
    return value


def _text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty trimmed string")
    return value


def _entry_data(entry: CorpusFileEntry) -> dict[str, object]:
    return entry.payload()


def snapshot_data(snapshot: CorpusSnapshot) -> dict[str, object]:
    snapshot.verify()
    return {
        "schema_version": snapshot.schema_version, "root": snapshot.root, "source_id": snapshot.source_id,
        "contract_hash": snapshot.contract_hash, "mapping_hash": snapshot.mapping_hash,
        "files": [_entry_data(entry) for entry in snapshot.files], "snapshot_hash": snapshot.snapshot_hash,
    }


def snapshot_from_data(value: object) -> CorpusSnapshot:
    data = _require_fields(value, {"schema_version", "root", "source_id", "contract_hash", "mapping_hash", "files", "snapshot_hash"}, "snapshot")
    if not isinstance(data["files"], list):
        raise ValueError("snapshot files must be a list")
    entries: list[CorpusFileEntry] = []
    for raw in data["files"]:
        item = _require_fields(raw, {"file_path", "symbol", "file_size", "footer_schema", "num_rows", "row_group_count", "row_group_rows"}, "snapshot file entry")
        if not isinstance(item["row_group_rows"], list):
            raise ValueError("snapshot row_group_rows must be a list")
        footer_schema = item["footer_schema"]
        if not isinstance(footer_schema, str) or not footer_schema:
            raise ValueError("footer_schema must be a non-empty string")
        entries.append(CorpusFileEntry(
            file_path=_text("file_path", item["file_path"]), symbol=_text("symbol", item["symbol"]),
            file_size=_int("file_size", item["file_size"], 1), footer_schema=footer_schema,
            num_rows=_int("num_rows", item["num_rows"]), row_group_count=_int("row_group_count", item["row_group_count"], 1),
            row_group_rows=tuple(_int("row_group_rows", number) for number in item["row_group_rows"]),
        ))
    result = CorpusSnapshot(
        root=_text("root", data["root"]), source_id=_text("source_id", data["source_id"]),
        contract_hash=_hash_text("contract_hash", data["contract_hash"]), mapping_hash=_hash_text("mapping_hash", data["mapping_hash"]),
        files=tuple(entries), snapshot_hash=_hash_text("snapshot_hash", data["snapshot_hash"]),
        schema_version=_text("schema_version", data["schema_version"]),
    )
    result.verify()
    return result


def campaign_data(campaign: CampaignContract) -> dict[str, object]:
    campaign.verify()
    return campaign.payload() | {"campaign_hash": campaign.campaign_hash}


def campaign_from_data(value: object) -> CampaignContract:
    data = _require_fields(value, {"schema_version", "snapshot_hash", "max_rows_per_file", "max_issues_per_file", "write_policy", "is_formal_registration", "campaign_hash"}, "campaign")
    if type(data["is_formal_registration"]) is not bool:
        raise ValueError("campaign is_formal_registration must be bool")
    result = CampaignContract(
        snapshot_hash=_hash_text("snapshot_hash", data["snapshot_hash"]),
        max_rows_per_file=_int("max_rows_per_file", data["max_rows_per_file"], 1, 10_000),
        max_issues_per_file=_int("max_issues_per_file", data["max_issues_per_file"], 1, 1_000),
        write_policy=_text("write_policy", data["write_policy"]), campaign_hash=_hash_text("campaign_hash", data["campaign_hash"]),
        is_formal_registration=data["is_formal_registration"], schema_version=_text("schema_version", data["schema_version"]),
    )
    result.verify()
    return result


def _issue_data(issue: DataQualityIssue) -> dict[str, object]:
    issue.verify()
    return {
        "source_id": issue.source_id, "symbol": issue.symbol, "session": issue.session.isoformat() if issue.session else None,
        "row_number": issue.row_number, "issue_code": issue.issue_code, "original_row_hash": issue.original_row_hash,
        "evidence_hash": issue.evidence_hash, "details_code": issue.details_code, "issue_hash": issue.issue_hash,
        "domain": issue.domain.value, "schema_version": issue.schema_version,
    }


def _issue_from_data(value: object) -> DataQualityIssue:
    data = _require_fields(value, {"source_id", "symbol", "session", "row_number", "issue_code", "original_row_hash", "evidence_hash", "details_code", "issue_hash", "domain", "schema_version"}, "quality issue")
    if data["original_row_hash"] is not None:
        _hash_text("original_row_hash", data["original_row_hash"])
    try:
        domain = DataClass(data["domain"])
    except (TypeError, ValueError) as exc:
        raise ValueError("quality issue domain is invalid") from exc
    result = DataQualityIssue(
        source_id=_text("source_id", data["source_id"]), symbol=_text("symbol", data["symbol"]),
        session=_date(data["session"], "session"), row_number=_int("row_number", data["row_number"], 1),
        issue_code=_text("issue_code", data["issue_code"]), original_row_hash=data["original_row_hash"],
        evidence_hash=_hash_text("evidence_hash", data["evidence_hash"]), details_code=_text("details_code", data["details_code"]),
        issue_hash=_hash_text("issue_hash", data["issue_hash"]), domain=domain,
        schema_version=_text("schema_version", data["schema_version"]),
    )
    result.verify()
    return result


def report_data(report: QualityAuditReport) -> dict[str, object]:
    report.verify()
    return {
        "source_id": report.source_id, "symbol": report.symbol, "file_path": report.file_path,
        "contract_hash": report.contract_hash, "mapping_hash": report.mapping_hash, "file_size": report.file_size,
        "rows_scanned": report.rows_scanned, "valid_rows": report.valid_rows, "truncated": report.truncated,
        "issues": [_issue_data(issue) for issue in report.issues], "issues_encountered": report.issues_encountered,
        "truncated_issues": report.truncated_issues, "min_session": report.min_session.isoformat() if report.min_session else None,
        "max_session": report.max_session.isoformat() if report.max_session else None, "status": report.status,
        "report_hash": report.report_hash, "schema_version": report.schema_version,
    }


def report_from_data(value: object) -> QualityAuditReport:
    fields = {"source_id", "symbol", "file_path", "contract_hash", "mapping_hash", "file_size", "rows_scanned", "valid_rows", "truncated", "issues", "issues_encountered", "truncated_issues", "min_session", "max_session", "status", "report_hash", "schema_version"}
    data = _require_fields(value, fields, "quality report")
    if type(data["truncated"]) is not bool or type(data["truncated_issues"]) is not bool or not isinstance(data["issues"], list):
        raise ValueError("quality report bool/list fields are invalid")
    result = QualityAuditReport(
        source_id=_text("source_id", data["source_id"]), symbol=_text("symbol", data["symbol"]), file_path=_text("file_path", data["file_path"]),
        contract_hash=_hash_text("contract_hash", data["contract_hash"]), mapping_hash=_hash_text("mapping_hash", data["mapping_hash"]), file_size=_int("file_size", data["file_size"], 1),
        rows_scanned=_int("rows_scanned", data["rows_scanned"]), valid_rows=_int("valid_rows", data["valid_rows"]), truncated=data["truncated"],
        issues=tuple(_issue_from_data(item) for item in data["issues"]), issues_encountered=_int("issues_encountered", data["issues_encountered"]),
        truncated_issues=data["truncated_issues"], min_session=_date(data["min_session"], "min_session"), max_session=_date(data["max_session"], "max_session"),
        status=_text("status", data["status"]), report_hash=_hash_text("report_hash", data["report_hash"]), schema_version=_text("schema_version", data["schema_version"]),
    )
    result.verify()
    return result


@dataclass(frozen=True)
class QualityRunManifest:
    run_id: str
    snapshot_hash: str
    campaign_hash: str
    source_id: str
    source_root: str
    write_policy: str
    manifest_hash: str
    schema_version: str = QUALITY_RUN_SCHEMA_VERSION

    def payload(self) -> dict[str, object]:
        return {"schema_version": self.schema_version, "run_id": self.run_id, "snapshot_hash": self.snapshot_hash, "campaign_hash": self.campaign_hash, "source_id": self.source_id, "source_root": self.source_root, "write_policy": self.write_policy}

    def verify(self) -> None:
        if self.schema_version != QUALITY_RUN_SCHEMA_VERSION:
            raise ValueError("unexpected run manifest schema_version")
        for name in ("run_id", "source_id", "source_root", "write_policy"):
            _text(name, getattr(self, name))
        if not re.fullmatch(r"quality-run-[0-9a-f]{64}", self.run_id):
            raise ValueError("run_id format is invalid")
        _hash_text("snapshot_hash", self.snapshot_hash); _hash_text("campaign_hash", self.campaign_hash); _hash_text("manifest_hash", self.manifest_hash)
        if self.write_policy != "no_source_mutation":
            raise ValueError("run manifest write_policy must be no_source_mutation")
        if self.manifest_hash != _hash(self.payload()):
            raise ValueError("run manifest hash does not match canonical payload")


def _manifest_data(manifest: QualityRunManifest) -> dict[str, object]:
    manifest.verify()
    return manifest.payload() | {"manifest_hash": manifest.manifest_hash}


def _manifest_from_data(value: object) -> QualityRunManifest:
    data = _require_fields(value, {"schema_version", "run_id", "snapshot_hash", "campaign_hash", "source_id", "source_root", "write_policy", "manifest_hash"}, "run manifest")
    result = QualityRunManifest(
        run_id=_text("run_id", data["run_id"]), snapshot_hash=_hash_text("snapshot_hash", data["snapshot_hash"]), campaign_hash=_hash_text("campaign_hash", data["campaign_hash"]),
        source_id=_text("source_id", data["source_id"]), source_root=_text("source_root", data["source_root"]), write_policy=_text("write_policy", data["write_policy"]),
        manifest_hash=_hash_text("manifest_hash", data["manifest_hash"]), schema_version=_text("schema_version", data["schema_version"]),
    )
    result.verify(); return result


def derive_run_id(snapshot_hash: str, campaign_hash: str) -> str:
    _hash_text("snapshot_hash", snapshot_hash); _hash_text("campaign_hash", campaign_hash)
    return "quality-run-" + sha256(_canonical_json({"snapshot_hash": snapshot_hash, "campaign_hash": campaign_hash})).hexdigest()


def _safe_output_root(output_root: str | Path, allowed_output_root: str | Path, contract: LocalParquetFileContract) -> Path:
    contract.verify()
    output, allowed, source = Path(output_root).resolve(), Path(allowed_output_root).resolve(), Path(contract.root).resolve()
    try:
        output.relative_to(allowed)
    except ValueError as exc:
        raise ValueError("workspace output root escapes allowed_output_root") from exc
    if output == source or output.is_relative_to(source):
        raise ValueError("workspace output root must not be inside source root")
    return output


def _atomic_write_once(path: Path, content: bytes) -> None:
    if path.exists():
        raise ValueError("write-once target already exists")
    temporary = path.with_name(path.name + ".tmp")
    if temporary.exists():
        raise ValueError("orphan temporary artifact blocks write-once publication")
    descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content); handle.flush(); os.fsync(handle.fileno())
        # A hard link is atomic and refuses to replace an existing target.
        os.link(temporary, path)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                # A failed cleanup must not replace the primary write/link error.
                if sys.exc_info()[0] is None:
                    raise


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON artifact: {path.name}") from exc


def _write_json_once(path: Path, value: object) -> None:
    _atomic_write_once(path, _canonical_json(value))


def _bind_snapshot(snapshot: CorpusSnapshot, campaign: CampaignContract, contract: LocalParquetFileContract) -> None:
    snapshot.verify(); campaign.verify(); contract.verify()
    if snapshot.source_id != contract.source_id or snapshot.root != str(Path(contract.root).resolve()) or snapshot.contract_hash != contract.contract_hash or snapshot.mapping_hash != _market_mapping(contract).mapping_hash:
        raise ValueError("snapshot source/root/contract/mapping does not match explicit local contract")
    if campaign.snapshot_hash != snapshot.snapshot_hash:
        raise ValueError("campaign does not bind snapshot")


def prepare_quality_run(
    snapshot: CorpusSnapshot, campaign: CampaignContract, contract: LocalParquetFileContract,
    *, workspace_output_root: str | Path, allowed_output_root: str | Path,
) -> Path:
    """Create one new run directory and immutable metadata documents exactly once."""
    _bind_snapshot(snapshot, campaign, contract)
    output = _safe_output_root(workspace_output_root, allowed_output_root, contract)
    run_id = derive_run_id(snapshot.snapshot_hash, campaign.campaign_hash)
    run_dir = output / run_id
    if run_dir.exists():
        raise ValueError("quality run identity already exists; prepare is write-once")
    manifest0 = QualityRunManifest(run_id, snapshot.snapshot_hash, campaign.campaign_hash, contract.source_id, str(Path(contract.root).resolve()), "no_source_mutation", "sha256:" + "0" * 64)
    manifest = QualityRunManifest(**{**manifest0.__dict__, "manifest_hash": _hash(manifest0.payload())}); manifest.verify()
    output.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir()
    try:
        (run_dir / "reports").mkdir()
        _write_json_once(run_dir / "snapshot.json", snapshot_data(snapshot))
        _write_json_once(run_dir / "campaign.json", campaign_data(campaign))
        _write_json_once(run_dir / "run_manifest.json", _manifest_data(manifest))
    except BaseException:
        # Preserve the failed write-once directory for fail-closed inspection.
        raise
    return run_dir


def _load_run(
    run_dir: str | Path, contract: LocalParquetFileContract, *, allowed_output_root: str | Path,
) -> tuple[Path, CorpusSnapshot, CampaignContract, QualityRunManifest]:
    contract.verify()
    root = Path(run_dir).resolve(); allowed = Path(allowed_output_root).resolve()
    try:
        root.relative_to(allowed)
    except ValueError as exc:
        raise ValueError("run directory escapes allowed_output_root") from exc
    if not root.is_dir() or not (root / "reports").is_dir():
        raise ValueError("quality run directory is incomplete")
    snapshot = snapshot_from_data(_read_json(root / "snapshot.json"))
    campaign = campaign_from_data(_read_json(root / "campaign.json"))
    manifest = _manifest_from_data(_read_json(root / "run_manifest.json"))
    _bind_snapshot(snapshot, campaign, contract)
    if root.name != derive_run_id(snapshot.snapshot_hash, campaign.campaign_hash) or manifest.run_id != root.name or manifest.snapshot_hash != snapshot.snapshot_hash or manifest.campaign_hash != campaign.campaign_hash or manifest.source_id != contract.source_id or manifest.source_root != str(Path(contract.root).resolve()):
        raise ValueError("quality run directory identity or manifest binding is invalid")
    return root, snapshot, campaign, manifest


def _report_path(reports_dir: Path, entry: CorpusFileEntry) -> Path:
    return reports_dir / f"{entry.symbol}.json"


def _read_completed_reports(run_dir: Path, snapshot: CorpusSnapshot, campaign: CampaignContract, contract: LocalParquetFileContract) -> tuple[QualityAuditReport, ...]:
    reports_dir = run_dir / "reports"
    expected = {f"{entry.symbol}.json": entry for entry in snapshot.files}
    reports: dict[str, QualityAuditReport] = {}
    for path in reports_dir.iterdir():
        if path.is_dir() or path.is_symlink() or path.name.endswith(".tmp"):
            raise ValueError("orphan temporary or non-file report artifact blocks run")
        entry = expected.get(path.name)
        if entry is None:
            raise ValueError("unexpected report artifact blocks run")
        report = report_from_data(_read_json(path))
        if report.source_id != contract.source_id or report.symbol != entry.symbol or report.file_path != entry.file_path or report.contract_hash != snapshot.contract_hash or report.mapping_hash != snapshot.mapping_hash or report.file_size != entry.file_size:
            raise ValueError("stored report does not bind snapshot entry/contract/campaign")
        reports[entry.symbol] = report
    return tuple(reports[entry.symbol] for entry in snapshot.files if entry.symbol in reports)


@dataclass(frozen=True)
class QualityRunStatus:
    run_dir: str
    snapshot_hash: str
    campaign_hash: str
    completed_files: int
    total_files: int
    status: str
    aggregate_hash: str | None

    def as_dict(self) -> dict[str, object]:
        return {"run_dir": self.run_dir, "snapshot_hash": self.snapshot_hash, "campaign_hash": self.campaign_hash, "completed_files": self.completed_files, "total_files": self.total_files, "status": self.status, "aggregate_hash": self.aggregate_hash}


def _status(run_dir: Path, snapshot: CorpusSnapshot, campaign: CampaignContract, contract: LocalParquetFileContract) -> QualityRunStatus:
    reports = _read_completed_reports(run_dir, snapshot, campaign, contract)
    complete = len(reports)
    if complete == 0:
        status, aggregate_hash = "waiting", None
    elif complete < len(snapshot.files):
        status, aggregate_hash = "accumulating", None
    else:
        aggregate = aggregate_campaign_reports(snapshot, campaign, contract, reports)
        status, aggregate_hash = ("complete_admitted" if aggregate.status == "admitted" else "complete_blocked"), aggregate.aggregate_hash
    return QualityRunStatus(str(run_dir), snapshot.snapshot_hash, campaign.campaign_hash, complete, len(snapshot.files), status, aggregate_hash)


def quality_run_status(run_dir: str | Path, contract: LocalParquetFileContract, *, allowed_output_root: str | Path) -> QualityRunStatus:
    root, snapshot, campaign, _ = _load_run(run_dir, contract, allowed_output_root=allowed_output_root)
    if (root / ".execute.lock").exists():
        raise ValueError("residual execute lock blocks status")
    return _status(root, snapshot, campaign, contract)


def _acquire_lock(path: Path) -> None:
    if path.exists():
        raise ValueError("residual execute lock blocks run")
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(b"quality-run-lock\n"); handle.flush(); os.fsync(handle.fileno())


def _publish_report(reports_dir: Path, entry: CorpusFileEntry, report: QualityAuditReport, snapshot: CorpusSnapshot, campaign: CampaignContract, contract: LocalParquetFileContract) -> None:
    path = _report_path(reports_dir, entry)
    payload = report_data(report)
    if path.exists():
        existing = report_from_data(_read_json(path))
        if report_data(existing) != payload:
            raise ValueError("existing report content differs from re-audited source")
        return
    _write_json_once(path, payload)


def execute_quality_run(
    run_dir: str | Path, contract: LocalParquetFileContract, *, allowed_output_root: str | Path,
    max_files_this_run: int,
) -> QualityRunStatus:
    """Read at most 100 pending source files under a single per-run exclusive lock."""
    _int("max_files_this_run", max_files_this_run, 1, 100)
    root, snapshot, campaign, _ = _load_run(run_dir, contract, allowed_output_root=allowed_output_root)
    lock = root / ".execute.lock"; acquired = False
    try:
        _acquire_lock(lock); acquired = True
        completed = _read_completed_reports(root, snapshot, campaign, contract)
        completed_symbols = {report.symbol for report in completed}
        pending = [entry for entry in snapshot.files if entry.symbol not in completed_symbols][:max_files_this_run]
        for entry in pending:
            if read_corpus_file_footer(contract, entry.file_path) != entry:
                raise ValueError("source footer no longer matches snapshot before audit")
            report = audit_snapshot_file(snapshot, campaign, contract, entry.file_path)
            if read_corpus_file_footer(contract, entry.file_path) != entry:
                raise ValueError("source footer changed during audit; report was not published")
            _publish_report(root / "reports", entry, report, snapshot, campaign, contract)
        return _status(root, snapshot, campaign, contract)
    finally:
        if acquired:
            try:
                lock.unlink()
            except OSError:
                if sys.exc_info()[0] is None:
                    raise


def prepare_from_contract(
    contract: LocalParquetFileContract, *, max_files: int, max_rows_per_file: int, max_issues_per_file: int,
    workspace_output_root: str | Path, allowed_output_root: str | Path,
) -> tuple[Path, CorpusSnapshot, CampaignContract]:
    """CLI convenience: footer snapshot plus a draft campaign, never a row audit."""
    snapshot = build_corpus_snapshot(contract, max_files=max_files)
    campaign = make_campaign_contract(snapshot, max_rows_per_file=max_rows_per_file, max_issues_per_file=max_issues_per_file)
    return prepare_quality_run(snapshot, campaign, contract, workspace_output_root=workspace_output_root, allowed_output_root=allowed_output_root), snapshot, campaign
