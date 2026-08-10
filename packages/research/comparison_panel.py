"""Strong-snapshot-bound, symbol-sharded market panel for candidate comparison."""
from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from packages.market_data import verify_source_against_strong_snapshot
from packages.research.execution import ExecutionConfig, assess_execution
from packages.research.json_store import write_json, write_jsonl
from packages.research.readiness import build_code_snapshot
from packages.research.run_artifacts import canonical_hash, file_hash


PANEL_SCHEMA = "comparison-market-panel/v4"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REQUIRED_FIELDS = (
    "symbol", "date", "open", "close", "prev_close", "volume", "amount", "is_st",
    "tradeable_open", "tradeable_close", "open_reason_codes", "close_reason_codes",
)


def _identity(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: payload[key] for key in ("schema_version", "builder", "builder_code_snapshot_id", "builder_code_snapshot", "dataset_snapshot_id", "source_snapshot_fingerprint", "source_root", "source_dataset", "oos", "symbols", "required_fields", "execution_policy", "skipped_initial_rows", "shards")}


def build_comparison_panel(source: Any, snapshot_manifest: Path, symbols: Iterable[str], *, start: date, end: date, lockbox_start: date, output_dir: Path) -> Path:
    """Build one atomic shard per symbol from an already frozen strong source."""
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"comparison panel already exists: {output_dir}")
    if not start <= end < lockbox_start:
        raise ValueError("panel range must be OOS-only and end before final lockbox")
    source_check = verify_source_against_strong_snapshot(source, snapshot_manifest)
    if source_check["status"] != "valid":
        raise ValueError("panel source does not match shared strong snapshot")
    snapshot = json.loads(snapshot_manifest.read_text(encoding="utf-8"))
    selected = sorted(set(symbols))
    if selected != sorted(set(snapshot.get("symbols", []))):
        raise ValueError("panel symbols differ from shared dataset snapshot")
    builder_snapshot_path = output_dir / "builder_code_snapshot.json"
    builder_snapshot = build_code_snapshot(REPOSITORY_ROOT, builder_snapshot_path)
    shards = []; skipped_manifest = []
    policy = {
        "implementation": "packages.research.execution.assess_execution",
        "entry": {"side": "buy", "price_at": "open", "require_session_liquidity": False},
        "exit": {"side": "sell", "price_at": "close", "require_session_liquidity": True},
        "config": {"skip_untradeable": True, "limit_tolerance": 0.001, "main_board_limit": 0.10, "st_limit": 0.05, "chinext_limit": 0.20, "star_limit": 0.20},
    }
    config = ExecutionConfig()
    for symbol in selected:
        # Load through OOS end but retain the immediately preceding close so
        # the first OOS row has an auditable price-limit reference.
        candles = source.load(symbol, end=end)
        rows = []; skipped = []
        previous_date = None
        previous_close = None
        for candle in candles:
            day = candle.timestamp.date()
            if previous_date is not None and day <= previous_date:
                raise ValueError(f"source dates are not strictly increasing: {symbol}")
            if candle.prev_close is None and previous_close is not None:
                raise ValueError(f"source prev_close missing inside series: {symbol} {day}")
            if candle.prev_close is not None and previous_close is not None and abs(float(candle.prev_close) - previous_close) > max(1e-10, abs(previous_close) * 1e-10):
                raise ValueError(f"source prev_close inconsistent: {symbol} {day}")
            if day < start:
                previous_date, previous_close = day, float(candle.close)
                continue
            if candle.prev_close is None:
                if previous_date is not None:
                    raise ValueError(f"source prev_close missing inside series: {symbol} {day}")
                close_reference = float(candle.close)
                if not isfinite(close_reference) or close_reference <= 0:
                    raise ValueError(f"source first available close invalid: {symbol} {day}")
                skipped.append({"date": day.isoformat(), "reason": "missing_prev_close_first_available_bar", "close_reference": close_reference})
                previous_date, previous_close = day, close_reference
                continue
            bar = {"date": day.isoformat(), "open": candle.open, "close": candle.close, "prev_close": candle.prev_close,
                   "volume": candle.volume, "amount": candle.amount, "is_st": candle.is_st}
            opening = assess_execution(bar, symbol=symbol, side="buy", price_at="open", require_session_liquidity=False, config=config)
            closing = assess_execution(bar, symbol=symbol, side="sell", price_at="close", require_session_liquidity=True, config=config)
            rows.append({"symbol": symbol, "date": day.isoformat(), "open": candle.open, "close": candle.close,
                         "prev_close": candle.prev_close, "volume": candle.volume, "amount": candle.amount, "is_st": candle.is_st,
                         "tradeable_open": opening.executable, "tradeable_close": closing.executable,
                         "open_reason_codes": list(opening.reason_codes), "close_reason_codes": list(closing.reason_codes)})
            previous_date, previous_close = day, float(candle.close)
        shard = output_dir / "symbols" / f"{symbol}.jsonl"
        write_jsonl(shard, rows)
        skipped_record = {"count": len(skipped), "dates": [item["date"] for item in skipped],
                          "reason": skipped[0]["reason"] if skipped else None, "close_references": [item["close_reference"] for item in skipped]}
        shards.append({"symbol": symbol, "path": shard.relative_to(output_dir).as_posix(), "rows": len(rows),
                       "first_date": rows[0]["date"] if rows else None, "last_date": rows[-1]["date"] if rows else None,
                       "skipped_initial_rows": skipped_record, "sha256": file_hash(shard)})
        skipped_manifest.extend({"symbol": symbol, **item} for item in skipped)
    identity = {"schema_version": PANEL_SCHEMA, "builder": "packages.research.comparison_panel.build_comparison_panel",
                "builder_code_snapshot_id": builder_snapshot["code_snapshot_id"],
                "builder_code_snapshot": {"path": "builder_code_snapshot.json", "sha256": file_hash(builder_snapshot_path)},
                "dataset_snapshot_id": snapshot["dataset_snapshot_id"], "source_snapshot_fingerprint": source_check.get("snapshot_manifest_fingerprint"),
                "source_root": source_check["source_root"], "source_dataset": source_check["source_dataset"],
                "oos": {"start": start.isoformat(), "end": end.isoformat(), "lockbox_start": lockbox_start.isoformat()},
                "symbols": selected, "required_fields": list(REQUIRED_FIELDS), "execution_policy": policy,
                "skipped_initial_rows": {"count": len(skipped_manifest), "rows": skipped_manifest}, "shards": shards}
    manifest = {**identity, "panel_id": canonical_hash(identity), "created_at": datetime.now(timezone.utc)}
    manifest_path = output_dir / "panel_manifest.json"
    write_json(manifest_path, manifest)
    return manifest_path


class ShardedPanel:
    """Verified panel index that keeps only the small shard manifest in memory."""
    def __init__(self, manifest_path: Path, *, expected_snapshot_id: str, expected_oos: Mapping[str, str]):
        self.manifest_path = manifest_path.resolve()
        self.root = self.manifest_path.parent
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if self.manifest.get("panel_id") != canonical_hash(_identity(self.manifest)):
            raise ValueError("comparison panel manifest identity mismatch")
        if self.manifest.get("schema_version") != PANEL_SCHEMA:
            raise ValueError("unsupported comparison panel schema")
        if self.manifest.get("builder") != "packages.research.comparison_panel.build_comparison_panel" or not str(self.manifest.get("source_snapshot_fingerprint", "")).startswith("sha256:"):
            raise ValueError("comparison panel lacks trusted builder/source attestation")
        snapshot_record = self.manifest.get("builder_code_snapshot", {})
        snapshot_path = (self.root / str(snapshot_record.get("path", ""))).resolve()
        if (snapshot_record.get("path") != "builder_code_snapshot.json" or not snapshot_path.is_relative_to(self.root)
                or snapshot_path != (self.root / "builder_code_snapshot.json").resolve() or not snapshot_path.is_file()):
            raise ValueError("comparison panel builder code snapshot path invalid")
        if file_hash(snapshot_path) != snapshot_record.get("sha256"):
            raise ValueError("comparison panel builder code snapshot hash mismatch")
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8")); snapshot_identity = {"schema_version": snapshot.get("schema_version"), "files": snapshot.get("files")}
        snapshot_id = "sha256:" + sha256(json.dumps(snapshot_identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if snapshot.get("code_snapshot_id") != snapshot_id or snapshot.get("code_snapshot_id") != self.manifest.get("builder_code_snapshot_id"):
            raise ValueError("comparison panel builder code snapshot identity mismatch")
        if self.manifest.get("dataset_snapshot_id") != expected_snapshot_id:
            raise ValueError("comparison panel dataset snapshot mismatch")
        panel_oos = self.manifest.get("oos", {})
        if any(panel_oos.get(key) != expected_oos.get(key) for key in ("start", "end", "lockbox_start")):
            raise ValueError("comparison panel OOS boundary mismatch")
        if not panel_oos.get("lockbox_start") or panel_oos["end"] >= panel_oos["lockbox_start"]:
            raise ValueError("comparison panel crosses final lockbox")
        self._shards = {str(item["symbol"]): dict(item) for item in self.manifest.get("shards", [])}
        if len(self._shards) != len(self.manifest.get("shards", [])) or sorted(self._shards) != self.manifest.get("symbols"):
            raise ValueError("comparison panel has duplicate/mismatched symbol shards")
        for symbol, item in self._shards.items():
            path = (self.root / item["path"]).resolve()
            if not path.is_relative_to(self.root) or path != (self.root / "symbols" / f"{symbol}.jsonl").resolve() or not path.is_file():
                raise ValueError("comparison panel shard path invalid")
            if file_hash(path) != item.get("sha256"):
                raise ValueError("comparison panel shard hash mismatch")
        skipped_rows = []
        for symbol, item in sorted(self._shards.items()):
            skipped = item.get("skipped_initial_rows")
            if not isinstance(skipped, Mapping) or set(skipped) != {"count", "dates", "reason", "close_references"}:
                raise ValueError("comparison panel skipped-initial metadata missing")
            count = int(skipped["count"]); dates = skipped["dates"]; references = skipped["close_references"]
            if count not in (0, 1) or len(dates) != count or len(references) != count or skipped["reason"] != ("missing_prev_close_first_available_bar" if count else None):
                raise ValueError("comparison panel skipped-initial metadata invalid")
            for day, reference in zip(dates, references):
                if not self.manifest["oos"]["start"] <= day <= self.manifest["oos"]["end"]:
                    raise ValueError("comparison panel skipped-initial date outside OOS")
                if not isfinite(float(reference)) or float(reference) <= 0:
                    raise ValueError("comparison panel skipped-initial close reference invalid")
                if item.get("first_date") is not None and day >= item["first_date"]:
                    raise ValueError("comparison panel skipped-initial date is not before retained rows")
                skipped_rows.append({"symbol": symbol, "date": day, "reason": skipped["reason"], "close_reference": float(reference)})
        summary = self.manifest.get("skipped_initial_rows")
        if not isinstance(summary, Mapping) or summary.get("count") != len(skipped_rows) or summary.get("rows") != skipped_rows:
            raise ValueError("comparison panel skipped-initial manifest summary mismatch")

    @property
    def symbols(self) -> list[str]:
        return sorted(self._shards)

    def iter_symbol(self, symbol: str) -> Iterator[dict[str, Any]]:
        item = self._shards.get(symbol)
        if item is None:
            raise KeyError(symbol)
        path = self.root / item["path"]
        count = 0; previous_date = None; first_date = None; previous_close = None; seen = set()
        skipped = item["skipped_initial_rows"]
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip(): continue
                row = json.loads(line); count += 1
                if set(REQUIRED_FIELDS) - set(row): raise ValueError("comparison panel required fields missing")
                day = str(row["date"]); first_date = first_date or day; key = (symbol, day)
                if str(row["symbol"]) != symbol or key in seen: raise ValueError("comparison panel duplicate symbol+date")
                if previous_date is not None and day <= previous_date: raise ValueError("comparison panel dates are not strictly increasing")
                if day < self.manifest["oos"]["start"] or day > self.manifest["oos"]["end"] or day >= self.manifest["oos"]["lockbox_start"]:
                    raise ValueError("comparison panel row outside OOS/lockbox boundary")
                for field in ("open", "close"):
                    try: value = float(row[field])
                    except (TypeError, ValueError): raise ValueError("comparison panel invalid price")
                    if not isfinite(value) or value <= 0: raise ValueError("comparison panel invalid price")
                try: prior = float(row["prev_close"])
                except (TypeError, ValueError): raise ValueError("comparison panel prev_close missing")
                if not isfinite(prior) or prior <= 0: raise ValueError("comparison panel prev_close missing")
                if count == 1 and int(skipped["count"]) == 1 and abs(prior - float(skipped["close_references"][0])) > max(1e-10, abs(float(skipped["close_references"][0])) * 1e-10):
                    raise ValueError("comparison panel first retained prev_close differs from skipped source close")
                if previous_close is not None and abs(prior - previous_close) > max(1e-10, abs(previous_close) * 1e-10): raise ValueError("comparison panel prev_close inconsistent")
                open_assessment = assess_execution(row, symbol=symbol, side="buy", price_at="open", require_session_liquidity=False)
                close_assessment = assess_execution(row, symbol=symbol, side="sell", price_at="close", require_session_liquidity=True)
                if bool(row["tradeable_open"]) != open_assessment.executable or list(row["open_reason_codes"]) != list(open_assessment.reason_codes):
                    raise ValueError("comparison panel open execution semantics mismatch")
                if bool(row["tradeable_close"]) != close_assessment.executable or list(row["close_reason_codes"]) != list(close_assessment.reason_codes):
                    raise ValueError("comparison panel close execution semantics mismatch")
                seen.add(key); previous_date = day; previous_close = float(row["close"])
                yield row
        if count != int(item.get("rows", -1)): raise ValueError("comparison panel shard row count mismatch")
        if first_date != item.get("first_date") or previous_date != item.get("last_date"): raise ValueError("comparison panel shard date bounds mismatch")

    def load_symbol(self, symbol: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
        rows = list(self.iter_symbol(symbol))
        return rows, {str(row["date"]): index for index, row in enumerate(rows)}

    def validate_all(self) -> None:
        for symbol in self.symbols:
            for _ in self.iter_symbol(symbol):
                pass

    def trading_dates(self) -> list[str]:
        values: set[str] = set()
        for symbol in self.symbols:
            for row in self.iter_symbol(symbol): values.add(str(row["date"]))
        return sorted(values)
