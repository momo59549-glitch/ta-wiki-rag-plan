"""Audit whether a point-in-time universe has corresponding local price files."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path


def audit_universe_price_coverage(manifest_path: Path, dataset_dir: Path | tuple[Path, ...], as_of: date | None = None) -> dict:
    if not manifest_path.is_file():
        raise FileNotFoundError(f"缺少股票池清单: {manifest_path}")
    directories = (dataset_dir,) if isinstance(dataset_dir, Path) else dataset_dir
    existing_directories = tuple(directory for directory in directories if directory.is_dir())
    missing_directories = tuple(directory for directory in directories if not directory.is_dir())
    if not existing_directories:
        raise FileNotFoundError(f"没有可用行情目录: {', '.join(str(item) for item in directories)}")
    rows = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    symbols = {
        str(row["symbol"]).zfill(6) for row in rows
        if as_of is None or (date.fromisoformat(row["active_from"]) <= as_of and (not row.get("active_to") or as_of <= date.fromisoformat(row["active_to"])))
    }
    available = {path.stem for directory in existing_directories for path in directory.glob("*.parquet")}
    missing = sorted(symbols - available)
    return {
        "manifest": str(manifest_path),
        "dataset_dirs": [str(item) for item in directories],
        "available_dataset_dirs": [str(item) for item in existing_directories],
        "missing_optional_dataset_dirs": [str(item) for item in missing_directories],
        "as_of": as_of.isoformat() if as_of else None,
        "universe_symbols": len(symbols),
        "price_files": len(available),
        "missing_price_symbols": missing,
        "coverage_ratio": (len(symbols) - len(missing)) / len(symbols) if symbols else 0.0,
        "status": "complete" if not missing else "incomplete_price_history",
    }
