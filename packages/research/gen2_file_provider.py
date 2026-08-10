"""Explicit-manifest local Parquet provider for the Gen2 future runner.

There is intentionally no market-root discovery: every file is named in a
write-once source-revision manifest and must remain below ``allowed_data_root``.
"""
from __future__ import annotations

from datetime import date
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
import numpy as np

from packages.research.gen2_discovery import canonical_hash
from packages.research.gen2_future_runner import REVISION_SCHEMA

FILE_SCHEMA = "gen2-local-file-ref/v1"
MANIFEST_SCHEMA = "gen2-local-source-revision-manifest/v1"
CALENDAR_SCHEMA = "gen2-local-calendar-manifest/v1"
PIT_SCHEMA = "gen2-local-pit-manifest/v1"


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict): raise ValueError(f"JSON object required: {path}")
    return value


def _sha(path: Path) -> str:
    return "sha256:" + sha256(path.read_bytes()).hexdigest()


def _safe(root: Path, raw: str) -> Path:
    root, resolved = root.resolve(), (root / raw).resolve()
    try: resolved.relative_to(root)
    except ValueError as exc: raise ValueError("manifest file path escapes allowed-data-root") from exc
    return resolved


def _file(root: Path, value: Mapping[str, Any]) -> Path:
    if set(value) != {"schema_version", "path", "size", "sha256"} or value.get("schema_version") != FILE_SCHEMA:
        raise ValueError("local file reference schema invalid")
    if not isinstance(value.get("path"), str) or not isinstance(value.get("size"), int) or value["size"] < 0 or not isinstance(value.get("sha256"), str):
        raise ValueError("local file reference fields invalid")
    return _safe(root, value["path"])


def _normal(frame: pd.DataFrame, fields: tuple[str, ...], label: str) -> pd.DataFrame:
    if "date" in frame.columns:
        frame = frame.set_index("date")
    if not isinstance(frame.index, pd.DatetimeIndex): raise ValueError(f"{label} requires date column/DatetimeIndex")
    frame = frame.copy(); frame.index = pd.to_datetime(frame.index, errors="raise").normalize()
    if not frame.index.is_monotonic_increasing or not frame.index.is_unique or any(field not in frame for field in fields):
        raise ValueError(f"{label} date/field contract invalid")
    price_fields = ("open", "close") if label == "benchmark" else ("open", "high", "low", "close", "prev_close")
    for field in price_fields:
        values = pd.to_numeric(frame[field], errors="coerce")
        if (~np.isfinite(values) | (values <= 0)).any():
            raise ValueError(f"{label}.{field} must be finite positive")
        frame[field] = values.astype(float)
    if label != "benchmark":
        if (frame["high"] < frame[["open", "close"]].max(axis=1)).any() or (frame["low"] > frame[["open", "close"]].min(axis=1)).any():
            raise ValueError("asset OHLC structural range invalid")
        for field in ("volume", "amount"):
            values = pd.to_numeric(frame[field], errors="coerce")
            if (~np.isfinite(values) | (values < 0)).any():
                raise ValueError(f"asset.{field} must be finite nonnegative")
            frame[field] = values.astype(float)
        raw = frame["is_st"]
        if raw.isna().any() or not raw.map(lambda x: isinstance(x, (bool, np.bool_, int, np.integer)) and x in (False, True, 0, 1)).all():
            raise ValueError("asset.is_st must be boolean/0-1")
        frame["is_st"] = raw.astype(bool)
    return frame


def write_source_revision_manifest(path: Path, payload: Mapping[str, Any]) -> Path:
    """Write one explicit manifest; callers must supply every file reference."""
    if path.exists():
        raise FileExistsError(f"write-once source revision manifest exists: {path}")
    value = dict(payload)
    if value.get("schema_version") != MANIFEST_SCHEMA:
        raise ValueError("source revision manifest schema invalid")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path


class LocalParquetFutureSource:
    """Provider whose identity comes only from one explicit revision manifest."""
    def __init__(self, manifest_path: Path, *, allowed_data_root: Path):
        self.manifest_path, self.root = manifest_path.resolve(), allowed_data_root.resolve()
        self.manifest = _read(self.manifest_path)
        self._verified = False; self._assets: dict[str, pd.DataFrame] = {}; self._benchmark: pd.DataFrame | None = None; self._calendar: pd.DatetimeIndex | None = None; self._pit: dict[str, list[str]] | None = None
        self._validate_shape()

    def _validate_shape(self) -> None:
        required = {"schema_version", "parent_revision_hash", "parent_available_through", "available_from", "available_through", "asset_dataset_id", "benchmark_dataset_id", "calendar_id", "pit_lineage_id", "asset_files", "benchmark_file", "calendar_manifest", "pit_manifest", "historical_prefix_hash", "created_at"}
        if set(self.manifest) != required or self.manifest.get("schema_version") != MANIFEST_SCHEMA: raise ValueError("source revision manifest schema invalid")
        if not all(isinstance(self.manifest.get(key), str) and self.manifest[key] for key in ("available_from", "available_through", "asset_dataset_id", "benchmark_dataset_id", "calendar_id", "pit_lineage_id", "created_at")): raise ValueError("source revision manifest fields invalid")
        if not isinstance(self.manifest["asset_files"], list) or not self.manifest["asset_files"]: raise ValueError("source revision requires explicit asset files")
        for item in self.manifest["asset_files"]:
            if set(item) != {"symbol", "file"} or not isinstance(item["symbol"], str): raise ValueError("asset manifest entry invalid")
            _file(self.root, item["file"])
        if len({item["symbol"] for item in self.manifest["asset_files"]}) != len(self.manifest["asset_files"]): raise ValueError("duplicate asset symbol manifest")
        for key in ("benchmark_file", "calendar_manifest", "pit_manifest"): _file(self.root, self.manifest[key])
        if self.manifest["parent_revision_hash"] is None:
            if self.manifest["parent_available_through"] is not None: raise ValueError("first revision parent range invalid")
        elif not isinstance(self.manifest["parent_revision_hash"], str) or not isinstance(self.manifest["parent_available_through"], str): raise ValueError("revision parent fields invalid")

    def _verify_file(self, ref: Mapping[str, Any]) -> Path:
        path = _file(self.root, ref)
        if not path.is_file() or path.stat().st_size != ref["size"] or _sha(path) != ref["sha256"]: raise ValueError("manifest file size/hash mismatch")
        return path

    def _ensure(self) -> None:
        if self._verified: return
        assets = {}
        for item in self.manifest["asset_files"]:
            assets[item["symbol"]] = _normal(pd.read_parquet(self._verify_file(item["file"])), ("open", "high", "low", "close", "prev_close", "volume", "amount", "is_st"), f"asset:{item['symbol']}")
        benchmark = _normal(pd.read_parquet(self._verify_file(self.manifest["benchmark_file"])), ("open", "close"), "benchmark")
        cal = _read(self._verify_file(self.manifest["calendar_manifest"])); pit = _read(self._verify_file(self.manifest["pit_manifest"]))
        if set(cal) != {"schema_version", "dates"} or cal.get("schema_version") != CALENDAR_SCHEMA or not isinstance(cal["dates"], list): raise ValueError("calendar manifest schema invalid")
        calendar = pd.DatetimeIndex(pd.to_datetime(cal["dates"], errors="raise")).normalize()
        if not calendar.is_monotonic_increasing or not calendar.is_unique: raise ValueError("calendar manifest dates invalid")
        if set(pit) != {"schema_version", "memberships"} or pit.get("schema_version") != PIT_SCHEMA or not isinstance(pit["memberships"], dict): raise ValueError("PIT manifest schema invalid")
        for day, symbols in pit["memberships"].items():
            date.fromisoformat(day)
            if not isinstance(symbols, list) or symbols != sorted(set(symbols)) or any(not isinstance(x, str) or not x for x in symbols): raise ValueError("PIT membership invalid")
            if pd.Timestamp(day) not in calendar: raise ValueError("PIT membership date absent from calendar")
            if any(symbol not in assets for symbol in symbols): raise ValueError("PIT membership has unknown asset symbol")
        through = date.fromisoformat(self.manifest["available_through"])
        if any(x.date() > through for x in calendar) or any(x.date() > through for x in benchmark.index) or any(x.date() > through for f in assets.values() for x in f.index) or any(date.fromisoformat(day) > through for day in pit["memberships"]): raise ValueError("manifest contains rows after available_through")
        self._assets, self._benchmark, self._calendar, self._pit, self._verified = assets, benchmark, calendar, pit["memberships"], True
        if self._revision()["historical_prefix_hash"] != self.manifest["historical_prefix_hash"]:
            raise ValueError("manifest historical prefix proof does not match actual files")

    def _revision(self) -> dict[str, Any]:
        # Before outcome reads this is a manifest identity.  After _ensure it
        # additionally reflects verified canonical data prefixes.
        asset_refs = [{"symbol": x["symbol"], "sha256": x["file"]["sha256"]} for x in sorted(self.manifest["asset_files"], key=lambda x:x["symbol"])]
        if self._verified:
            def content_until(limit: date | None):
                select = lambda f: f if limit is None else f[f.index.date <= limit]
                return {"assets": {s: canonical_hash({"rows": select(f).reset_index().astype(str).to_dict("records")}) for s, f in self._assets.items()}, "benchmark": canonical_hash({"rows": select(self._benchmark).reset_index().astype(str).to_dict("records")}), "calendar": [x.date().isoformat() for x in self._calendar if limit is None or x.date() <= limit], "pit": {d:v for d,v in self._pit.items() if limit is None or date.fromisoformat(d) <= limit}}
            prefix = content_until(None); content = canonical_hash(prefix); calendar_hash = canonical_hash({"dates": prefix["calendar"]})
            historical = canonical_hash(content_until(date.fromisoformat(self.manifest["parent_available_through"]))) if self.manifest["parent_available_through"] else canonical_hash({"prefix": "empty"})
        else:
            content = canonical_hash({"asset_refs": asset_refs, "benchmark": self.manifest["benchmark_file"]["sha256"], "calendar": self.manifest["calendar_manifest"]["sha256"], "pit": self.manifest["pit_manifest"]["sha256"]}); calendar_hash = canonical_hash({"manifest": self.manifest["calendar_manifest"]["sha256"]})
        identity = {"schema_version": REVISION_SCHEMA, "parent_revision_hash": self.manifest["parent_revision_hash"], "available_from": self.manifest["available_from"], "available_through": self.manifest["available_through"], "asset_dataset_id": self.manifest["asset_dataset_id"], "benchmark_dataset_id": self.manifest["benchmark_dataset_id"], "calendar_id": self.manifest["calendar_id"], "pit_lineage_id": self.manifest["pit_lineage_id"], "asset_snapshot_hash": canonical_hash({"files": asset_refs}), "asset_content_hash": content, "benchmark_snapshot_hash": self.manifest["benchmark_file"]["sha256"], "benchmark_content_hash": content, "pit_revision_hash": canonical_hash({"pit": self.manifest["pit_manifest"]["sha256"] if not self._verified else self._pit}), "calendar_prefix_hash": calendar_hash, "prefix_hash": content, "historical_prefix_hash": historical if self._verified else self.manifest["historical_prefix_hash"], "source_completeness_hash": canonical_hash({"files": asset_refs, "benchmark": self.manifest["benchmark_file"]["sha256"], "calendar": self.manifest["calendar_manifest"]["sha256"], "pit": self.manifest["pit_manifest"]["sha256"]}), "created_at": self.manifest["created_at"]}
        return {**identity, "revision_hash": canonical_hash(identity)}

    def identity(self) -> Mapping[str, Any]: return self._revision()
    def calendar(self, start: date, end: date) -> pd.DatetimeIndex:
        self._ensure(); assert self._calendar is not None
        return self._calendar[(self._calendar.date >= start) & (self._calendar.date <= end)]
    def asset_frame(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        self._ensure()
        if symbol not in self._assets: raise ValueError("symbol absent from explicit asset manifest")
        f=self._assets[symbol]; return f[(f.index.date >= start) & (f.index.date <= end)]
    def benchmark_frame(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        self._ensure(); assert self._benchmark is not None
        f=self._benchmark; return f[(f.index.date >= start) & (f.index.date <= end)]


class ManifestPitProvider:
    def __init__(self, source: LocalParquetFutureSource): self.source=source
    def identity(self) -> Mapping[str, Any]: return {"pit_revision_hash": self.source.identity()["pit_revision_hash"]}
    def active_on(self, day: date) -> set[str]:
        self.source._ensure(); assert self.source._pit is not None
        return set(self.source._pit.get(day.isoformat(), []))
