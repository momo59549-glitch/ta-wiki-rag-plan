"""Content-addressed manifests for strategy-test market-data snapshots."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable


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
    snapshot_id = "sha256:" + sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
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
    for item in payload.get("files", []):
        root = Path(payload["roots"][item["role"]]).resolve()
        path = (root / item["logical_path"]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            failures.append({"logical_path": item["logical_path"], "reason": "missing_or_out_of_scope"})
            continue
        if path.stat().st_size != item["size"] or _file_sha256(path) != item["sha256"]:
            failures.append({"logical_path": item["logical_path"], "reason": "content_mismatch"})
    return {
        "status": "valid" if not failures else "invalid",
        "dataset_snapshot_id": payload.get("dataset_snapshot_id"),
        "files_checked": len(payload.get("files", [])),
        "failures": failures,
    }
