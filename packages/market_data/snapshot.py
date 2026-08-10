"""Content-addressed manifests for strategy-test market-data snapshots."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4


_SOURCE_CHECK_REUSE_TOKENS: dict[str, dict[str, str]] = {}


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_paths(source: Any, symbol: str) -> list[Path]:
    """Return exactly the files a local/composite source can use for a symbol."""
    datasets = tuple(getattr(source, "datasets", ()))
    if not datasets:
        return [source.source_path(symbol)]
    base = next(
        (
            source.root / dataset / f"{symbol}.parquet"
            for dataset in datasets
            if dataset != "tushare_incremental_cache"
            and (source.root / dataset / f"{symbol}.parquet").is_file()
        ),
        None,
    )
    overlay = source.root / "tushare_incremental_cache" / f"{symbol}.parquet"
    paths = ([base] if base else []) + ([overlay] if overlay.is_file() and overlay != base else [])
    if not paths:
        raise FileNotFoundError(symbol)
    return paths


def _snapshot_identity(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": payload.get("schema_version"),
        "mode": payload.get("mode"),
        "dataset": payload.get("dataset"),
        "symbols": payload.get("symbols"),
        "files": payload.get("files"),
    }


def _snapshot_id(identity: dict[str, Any]) -> str:
    return "sha256:" + sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _manifest_fingerprint(manifest_path: Path, payload: dict[str, Any]) -> str:
    """Bind an in-process reuse token to snapshot identity and recorded roots.

    The path itself is deliberately excluded: campaign derivation copies the
    same immutable manifest into a new Campaign directory before consuming the
    verification token.
    """
    return "sha256:" + sha256(
        json.dumps(
            {
                "snapshot_id": _snapshot_id(_snapshot_identity(payload)),
                "roots": payload.get("roots"),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def build_strong_snapshot(
    source: Any,
    symbols: Iterable[str],
    output: Path,
    *,
    extra_sources: Iterable[tuple[str, Any, Iterable[str]]] = (),
) -> dict[str, Any]:
    """Hash every selected Parquet file and atomically persist the manifest."""
    roots: dict[str, Path] = {"market": Path(source.root).resolve()}
    selected: list[tuple[str, Path, Path]] = []
    for symbol in sorted(set(symbols)):
        selected.extend(("market", path, roots["market"]) for path in _source_paths(source, symbol))
    for role, extra_source, extra_symbols in extra_sources:
        root = Path(extra_source.root).resolve()
        roots[role] = root
        for symbol in sorted(set(extra_symbols)):
            selected.extend((role, path, root) for path in _source_paths(extra_source, symbol))

    files = []
    for role, path, root in sorted(selected, key=lambda item: (item[0], str(item[1]))):
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"快照文件越界: {resolved}")
        files.append(
            {
                "role": role,
                "logical_path": resolved.relative_to(root).as_posix(),
                "size": resolved.stat().st_size,
                "sha256": _file_sha256(resolved),
            }
        )
    identity = {
        "schema_version": "dataset-snapshot/v1",
        "mode": "strong_content_sha256",
        "dataset": str(getattr(source, "dataset", "unknown")),
        "symbols": sorted(set(symbols)),
        "files": files,
    }
    snapshot_id = _snapshot_id(identity)
    payload = {
        **identity,
        "dataset_snapshot_id": snapshot_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "roots": {role: str(root) for role, root in roots.items()},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(output)
    return payload


def verify_strong_snapshot(manifest_path: Path) -> dict[str, Any]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    failures = []
    identity = _snapshot_identity(payload)
    expected_snapshot_id = _snapshot_id(identity)
    if payload.get("dataset_snapshot_id") != expected_snapshot_id:
        failures.append({"reason": "snapshot_identity_mismatch"})
    if identity["schema_version"] != "dataset-snapshot/v1" or identity["mode"] != "strong_content_sha256":
        failures.append({"reason": "unsupported_snapshot_identity"})
    if not isinstance(identity["symbols"], list) or not isinstance(identity["files"], list):
        failures.append({"reason": "snapshot_identity_shape_invalid"})
    for item in payload.get("files", []):
        try:
            root = Path(payload["roots"][item["role"]]).resolve()
        except (KeyError, TypeError):
            failures.append({"logical_path": item.get("logical_path"), "reason": "missing_snapshot_root"})
            continue
        path = (root / item["logical_path"]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            failures.append({"logical_path": item["logical_path"], "reason": "missing_or_out_of_scope"})
            continue
        if path.stat().st_size != item["size"] or _file_sha256(path) != item["sha256"]:
            failures.append({"logical_path": item["logical_path"], "reason": "content_mismatch"})
    return {
        "status": "valid" if not failures else "invalid",
        "dataset_snapshot_id": payload.get("dataset_snapshot_id"),
        "expected_dataset_snapshot_id": expected_snapshot_id,
        "files_checked": len(payload.get("files", [])),
        "failures": failures,
    }


def verify_source_against_strong_snapshot(
    source: Any,
    manifest_path: Path,
    *,
    issue_reuse_token: bool = False,
) -> dict[str, Any]:
    """Bind an execution source to the exact root/dataset recorded in a snapshot.

    A valid manifest alone is insufficient if the runner can point at another
    cache.  This check combines content verification with source-root and
    composite-dataset identity checks before a frozen campaign is allowed to
    label outcomes with the snapshot id.
    """
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    failures: list[dict[str, str]] = []
    actual_root = Path(getattr(source, "root", "")).resolve()
    roots = payload.get("roots", {})
    for role in ("market", "benchmark"):
        expected_text = roots.get(role)
        if expected_text is None:
            continue
        if actual_root != Path(expected_text).resolve():
            failures.append({"role": role, "reason": "source_root_mismatch"})
    expected_dataset = payload.get("dataset")
    actual_dataset = getattr(source, "dataset", None)
    if expected_dataset and actual_dataset and expected_dataset != actual_dataset:
        failures.append({"role": "market", "reason": "source_dataset_mismatch"})
    content_check = verify_strong_snapshot(manifest_path)
    failures.extend(content_check["failures"])
    result = {
        "status": "valid" if not failures else "invalid",
        "dataset_snapshot_id": payload.get("dataset_snapshot_id"),
        "snapshot_manifest_fingerprint": _manifest_fingerprint(manifest_path, payload),
        "source_root": str(actual_root),
        "source_dataset": actual_dataset,
        "files_checked": content_check["files_checked"],
        "failures": failures,
    }
    if issue_reuse_token and result["status"] == "valid":
        token = uuid4().hex
        _SOURCE_CHECK_REUSE_TOKENS[token] = {
            "manifest_fingerprint": result["snapshot_manifest_fingerprint"],
            "dataset_snapshot_id": str(result["dataset_snapshot_id"]),
            "source_root": result["source_root"],
            "source_dataset": str(result["source_dataset"]),
        }
        result["_one_time_reuse_token"] = token
    return result


def consume_source_snapshot_reuse_token(source: Any, manifest_path: Path, verification: dict[str, Any]) -> dict[str, Any]:
    """Consume a full-check token without rehashing every frozen source file.

    The token is created only by a successful strong-content check in this
    process.  Before accepting it, this function rebinds it to the current
    source root/dataset and recomputed manifest identity.  It is one-time so a
    prior validation cannot be replayed for a later campaign invocation.
    """
    token = verification.get("_one_time_reuse_token")
    if not isinstance(token, str):
        return {"status": "invalid", "failures": [{"reason": "missing_source_check_reuse_token"}]}
    stored = _SOURCE_CHECK_REUSE_TOKENS.pop(token, None)
    if stored is None:
        return {"status": "invalid", "failures": [{"reason": "unknown_or_reused_source_check_token"}]}
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual_root = str(Path(getattr(source, "root", "")).resolve())
    actual_dataset = str(getattr(source, "dataset", None))
    expected = {
        "manifest_fingerprint": _manifest_fingerprint(manifest_path, payload),
        "dataset_snapshot_id": str(payload.get("dataset_snapshot_id")),
        "source_root": actual_root,
        "source_dataset": actual_dataset,
    }
    failures: list[dict[str, str]] = []
    if verification.get("status") != "valid":
        failures.append({"reason": "source_check_not_valid"})
    for key, value in expected.items():
        if stored.get(key) != value or verification.get({
            "manifest_fingerprint": "snapshot_manifest_fingerprint",
            "dataset_snapshot_id": "dataset_snapshot_id",
            "source_root": "source_root",
            "source_dataset": "source_dataset",
        }[key]) != value:
            failures.append({"reason": f"source_check_{key}_mismatch"})
    identity = _snapshot_identity(payload)
    if payload.get("dataset_snapshot_id") != _snapshot_id(identity):
        failures.append({"reason": "snapshot_identity_mismatch"})
    return {
        "status": "valid" if not failures else "invalid",
        "dataset_snapshot_id": payload.get("dataset_snapshot_id"),
        "source_root": actual_root,
        "source_dataset": actual_dataset,
        "failures": failures,
    }
