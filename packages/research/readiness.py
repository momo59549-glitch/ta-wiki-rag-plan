"""Build and verify code snapshots and strategy-test readiness reports."""
from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import subprocess
from typing import Any

from packages.market_data import consume_source_snapshot_reuse_token, load_point_in_time_universe, load_universe_memberships, verify_source_against_strong_snapshot, verify_strong_snapshot
from packages.research.protocol import verify_experiment_protocol


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _code_files(project_root: Path) -> list[Path]:
    candidates = []
    for directory in ("packages", "apps", "scripts"):
        root = project_root / directory
        if root.is_dir():
            candidates.extend(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in {".py", ".ps1"})
    candidates.extend(path for path in (project_root / "pyproject.toml",) if path.is_file())
    return sorted(set(candidates))


def build_code_snapshot(project_root: Path, output: Path) -> dict[str, Any]:
    project_root = project_root.resolve()
    files = [{"path": path.relative_to(project_root).as_posix(), "size": path.stat().st_size, "sha256": _sha256(path)} for path in _code_files(project_root)]
    identity = {"schema_version": "code-snapshot/v1", "files": files}
    code_snapshot_id = "sha256:" + sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project_root, capture_output=True, text=True, check=True).stdout.strip()
        dirty_entries = len(subprocess.run(["git", "status", "--porcelain"], cwd=project_root, capture_output=True, text=True, check=True).stdout.splitlines())
    except (OSError, subprocess.CalledProcessError):
        commit, dirty_entries = None, None
    payload = {**identity, "code_snapshot_id": code_snapshot_id, "created_at": datetime.now(timezone.utc).isoformat(), "git_commit": commit, "git_dirty_entries": dirty_entries}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def verify_code_snapshot(project_root: Path, manifest_path: Path) -> dict[str, Any]:
    project_root = project_root.resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    failures = []
    identity = {"schema_version": payload.get("schema_version"), "files": payload.get("files")}
    expected_id = "sha256:" + sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if payload.get("schema_version") != "code-snapshot/v1" or payload.get("code_snapshot_id") != expected_id:
        failures.append({"path": str(manifest_path), "reason": "snapshot_identity_mismatch"})
    recorded_paths = [str(item.get("path")) for item in payload.get("files", [])]
    if len(recorded_paths) != len(set(recorded_paths)):
        failures.append({"path": str(manifest_path), "reason": "duplicate_snapshot_path"})
    for item in payload.get("files", []):
        path = (project_root / item["path"]).resolve()
        if not path.is_relative_to(project_root) or not path.is_file():
            failures.append({"path": item["path"], "reason": "missing_or_out_of_scope"})
        elif path.stat().st_size != item["size"] or _sha256(path) != item["sha256"]:
            failures.append({"path": item["path"], "reason": "content_mismatch"})
    actual_paths = {path.relative_to(project_root).as_posix() for path in _code_files(project_root)}
    for path in sorted(actual_paths - set(recorded_paths)):
        failures.append({"path": path, "reason": "untracked_code_file"})
    return {"status": "valid" if not failures else "invalid", "code_snapshot_id": payload.get("code_snapshot_id"), "files_checked": len(payload.get("files", [])), "failures": failures}


def evaluate_strategy_readiness(
    *,
    project_root: Path,
    source: Any,
    universe_manifest: Path,
    as_of: date,
    dataset_snapshot_path: Path,
    code_snapshot_path: Path,
    protocol_path: Path,
    data_quality_path: Path,
    output: Path,
    source_check: dict[str, Any] | None = None,
) -> dict[str, Any]:
    active_symbols, universe_meta = load_point_in_time_universe(universe_manifest, as_of)
    memberships = load_universe_memberships(universe_manifest)
    available = set(source.symbols())
    missing_active = sorted(set(active_symbols) - available)
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    protocol_check = verify_experiment_protocol(protocol)
    data_check = verify_strong_snapshot(dataset_snapshot_path)
    source_check = (
        consume_source_snapshot_reuse_token(source, dataset_snapshot_path, source_check)
        if source_check is not None
        else verify_source_against_strong_snapshot(source, dataset_snapshot_path)
    )
    code_check = verify_code_snapshot(project_root, code_snapshot_path)
    dependencies = {name: bool(importlib.util.find_spec(name)) for name in ("vectorbt", "backtrader", "skfolio", "statsmodels", "anthropic")}
    dataset_manifest = json.loads(dataset_snapshot_path.read_text(encoding="utf-8"))
    data_quality = json.loads(data_quality_path.read_text(encoding="utf-8"))
    checks = {
        "dependencies_available": all(dependencies.values()),
        "point_in_time_universe": universe_meta.get("status") == "point_in_time",
        "delisted_members_present": any(item.active_to is not None for rows in memberships.values() for item in rows),
        "active_price_coverage_complete": not missing_active,
        "strong_dataset_snapshot_valid": data_check.get("status") == "valid",
        "execution_source_bound": source_check.get("status") == "valid",
        "market_data_quality_passed": data_quality.get("status") == "passed",
        "code_snapshot_valid": code_check.get("status") == "valid",
        "protocol_preregistered_and_ready": protocol.get("status") == "preregistered" and protocol.get("readiness", {}).get("status") == "ready",
        "protocol_integrity_valid": protocol_check.get("status") == "valid",
        "protocol_dataset_bound": protocol.get("dataset_snapshot_id") == data_check.get("dataset_snapshot_id"),
        "protocol_code_bound": protocol.get("code_version") == code_check.get("code_snapshot_id"),
        "benchmark_in_snapshot": any(item.get("role") == "benchmark" for item in dataset_manifest.get("files", [])),
        "lockbox_sealed": bool(protocol.get("periods", {}).get("final_lockbox_start") and protocol.get("periods", {}).get("research_end") < protocol.get("periods", {}).get("final_lockbox_start")),
        "cost_stress_preregistered": len(protocol.get("execution", {}).get("stress_cost_scenarios", [])) >= 2,
        "trial_budget_bounded": 1 <= int(protocol.get("validation", {}).get("max_candidate_trials", 0)) <= 20,
    }
    payload = {
        "schema_version": "strategy-test-readiness/v1",
        "status": "ready" if all(checks.values()) else "not_ready",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "dependencies": dependencies,
        "universe": {**universe_meta, "delisted_members": sum(item.active_to is not None for rows in memberships.values() for item in rows), "missing_active_symbols": missing_active},
        "dataset_snapshot": data_check,
        "execution_source": source_check,
        "market_data_quality": data_quality,
        "code_snapshot": code_check,
        "protocol_id": protocol.get("protocol_id"),
        "protocol_integrity": protocol_check,
        "guardrail": "This report authorizes framework-level strategy testing only; it does not publish a rule or consume the final lockbox.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
