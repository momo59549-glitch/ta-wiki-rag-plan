"""Atomic, integrity-checked sharded artifacts for resumable research runs."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from .json_store import write_json, write_jsonl


SCHEMA = "research-sharded-run/v1"


def _json_default(value: object) -> object:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, tuple):
        return list(value)
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(type(value).__name__)


def canonical_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_json_default)
    return "sha256:" + sha256(payload.encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def checkpoint_hash(payload: Mapping[str, Any]) -> str:
    return canonical_hash({key: value for key, value in payload.items() if key not in {"checkpoint_hash", "updated_at"}})


def commit_hash(payload: Mapping[str, Any]) -> str:
    return canonical_hash({key: value for key, value in payload.items() if key not in {"commit_hash", "committed_at"}})


def shard_path(run_dir: Path, kind: str, batch_index: int) -> Path:
    if kind not in {"observations", "outcomes"}:
        raise ValueError(f"unknown artifact kind: {kind}")
    return run_dir / "shards" / kind / f"batch_{batch_index:06d}.jsonl"


def commit_path(run_dir: Path, batch_index: int) -> Path:
    return run_dir / "shards" / "commits" / f"batch_{batch_index:06d}.json"


def write_batch(
    run_dir: Path,
    batch_index: int,
    symbols: list[str],
    observations: Iterable[object],
    outcomes: Iterable[object],
    identity_hash: str,
    *,
    loaded_symbols: int,
    skipped_symbols: list[str],
    fault_injector=None,
) -> dict[str, Any]:
    """Write two shards then publish one atomic commit marker.

    Files without a valid commit marker are deliberately not visible to readers
    and are overwritten on retry using the same deterministic batch filename.
    """
    observation_path = shard_path(run_dir, "observations", batch_index)
    outcome_path = shard_path(run_dir, "outcomes", batch_index)
    observation_count = write_jsonl(observation_path, observations)
    if fault_injector:
        fault_injector("after_observations_shard", batch_index)
    outcome_count = write_jsonl(outcome_path, outcomes)
    if fault_injector:
        fault_injector("after_outcomes_shard", batch_index)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA,
        "batch_index": batch_index,
        "execution_identity_hash": identity_hash,
        "symbols": symbols,
        "loaded_symbols": loaded_symbols,
        "skipped_symbols": skipped_symbols,
        "observations": {
            "path": observation_path.relative_to(run_dir).as_posix(),
            "count": observation_count,
            "sha256": file_hash(observation_path),
        },
        "outcomes": {
            "path": outcome_path.relative_to(run_dir).as_posix(),
            "count": outcome_count,
            "sha256": file_hash(outcome_path),
        },
        "committed_at": datetime.now(timezone.utc),
    }
    payload["commit_hash"] = commit_hash(payload)
    write_json(commit_path(run_dir, batch_index), payload)
    if fault_injector:
        fault_injector("after_commit_marker", batch_index)
    return payload


def validate_commit(run_dir: Path, payload: Mapping[str, Any], identity_hash: str) -> dict[str, Any]:
    if payload.get("schema_version") != SCHEMA:
        raise ValueError("unsupported research batch commit schema")
    if payload.get("execution_identity_hash") != identity_hash:
        raise ValueError("batch commit execution identity mismatch")
    if payload.get("commit_hash") != commit_hash(payload):
        raise ValueError("batch commit integrity hash mismatch")
    for kind in ("observations", "outcomes"):
        item = payload.get(kind, {})
        path = run_dir / str(item.get("path", ""))
        expected = shard_path(run_dir, kind, int(payload["batch_index"]))
        if path.resolve() != expected.resolve() or not path.is_file():
            raise ValueError(f"{kind} shard missing or path mismatch")
        if file_hash(path) != item.get("sha256"):
            raise ValueError(f"{kind} shard integrity hash mismatch")
        with path.open("r", encoding="utf-8") as handle:
            count = sum(1 for line in handle if line.strip())
        if count != int(item.get("count", -1)):
            raise ValueError(f"{kind} shard row count mismatch")
    return dict(payload)


def load_commits(run_dir: Path, identity_hash: str) -> list[dict[str, Any]]:
    directory = run_dir / "shards" / "commits"
    if not directory.is_dir():
        return []
    commits: list[dict[str, Any]] = []
    for path in sorted(directory.glob("batch_*.json")):
        payload = validate_commit(run_dir, json.loads(path.read_text(encoding="utf-8")), identity_hash)
        if int(payload["batch_index"]) != len(commits):
            raise ValueError("batch commits are not contiguous from zero")
        commits.append(payload)
    return commits


def build_checkpoint(identity: Mapping[str, Any], commits: list[Mapping[str, Any]], *, status: str, started_at: str | datetime) -> dict[str, Any]:
    observation_count = sum(int(item["observations"]["count"]) for item in commits)
    outcome_count = sum(int(item["outcomes"]["count"]) for item in commits)
    symbols_processed = sum(len(item["symbols"]) for item in commits)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA,
        "status": status,
        "execution_identity": dict(identity),
        "execution_identity_hash": canonical_hash(identity),
        "started_at": started_at,
        "updated_at": datetime.now(timezone.utc),
        "committed_batches": len(commits),
        "symbols_processed": symbols_processed,
        "symbols_loaded": sum(int(item["loaded_symbols"]) for item in commits),
        "observations": observation_count,
        "outcomes": outcome_count,
        "skipped_symbols": [symbol for item in commits for symbol in item["skipped_symbols"]],
        "commit_hashes": [item["commit_hash"] for item in commits],
    }
    payload["checkpoint_hash"] = checkpoint_hash(payload)
    return payload


def verify_checkpoint(run_dir: Path, expected_identity: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    path = run_dir / "checkpoint.json"
    if not path.is_file():
        raise ValueError("resumable checkpoint missing")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("checkpoint_hash") != checkpoint_hash(payload):
        raise ValueError("checkpoint integrity hash mismatch")
    if payload.get("execution_identity") != dict(expected_identity) or payload.get("execution_identity_hash") != canonical_hash(expected_identity):
        raise ValueError("checkpoint execution identity mismatch")
    commits = load_commits(run_dir, payload["execution_identity_hash"])
    recorded_count = int(payload.get("committed_batches", -1))
    if recorded_count < 0 or recorded_count > len(commits):
        raise ValueError("checkpoint references unavailable batch commits")
    # A hard stop can leave a valid checkpoint one commit behind.  Validate the
    # recorded prefix exactly, then adopt any contiguous, independently valid
    # tail commits.  This is the only permitted checkpoint reconstruction.
    rebuilt = build_checkpoint(expected_identity, commits[:recorded_count], status=str(payload["status"]), started_at=payload["started_at"])
    for key in ("committed_batches", "symbols_processed", "symbols_loaded", "observations", "outcomes", "skipped_symbols", "commit_hashes"):
        if payload.get(key) != rebuilt.get(key):
            raise ValueError(f"checkpoint state mismatch: {key}")
    if payload.get("status") == "completed" and recorded_count != len(commits):
        raise ValueError("completed checkpoint does not cover every commit")
    effective = build_checkpoint(expected_identity, commits, status=str(payload["status"]), started_at=payload["started_at"])
    return effective, commits


def iter_run_rows(run_dir: Path, kind: str) -> Iterator[dict[str, Any]]:
    """Stream committed shards, falling back to legacy completed monoliths."""
    manifest = run_dir / "artifact_manifest.json"
    if manifest.is_file():
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        identity_hash = str(payload["execution_identity_hash"])
        for commit in load_commits(run_dir, identity_hash):
            path = run_dir / commit[kind]["path"]
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        yield json.loads(line)
        return
    legacy = run_dir / f"{kind}.jsonl"
    if not legacy.is_file():
        raise FileNotFoundError(f"no committed {kind} artifacts: {run_dir}")
    with legacy.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def artifact_exists(run_dir: Path, kind: str) -> bool:
    return (run_dir / "artifact_manifest.json").is_file() or (run_dir / f"{kind}.jsonl").is_file()
