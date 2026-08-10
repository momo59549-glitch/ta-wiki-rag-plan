"""Independent ST-status timeline built from Tushare ``namechange`` records.

The Tushare daily APIs do not return an ST flag, so caches previously wrote
``is_st=False`` unconditionally.  A name history containing ``ST`` / ``*ST`` is
an independent, auditable source for the A-share 5%/10% price-limit split.
The timeline uses the same JSONL schema as the point-in-time universe manifest,
so it can be loaded with :func:`packages.market_data.load_universe_memberships`
and checked with :func:`packages.market_data.active_on`.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path
import time
from typing import Any

import requests

import pandas as pd

from packages.market_data import load_point_in_time_universe
from packages.market_data.local_parquet import LocalParquetMarketData
from packages.market_data.universe import active_on, load_universe_memberships


ST_MARKERS = ("ST", "*ST")


def _request(token: str, api_name: str, params: dict[str, str], timeout_seconds: float, max_retries: int) -> list[dict[str, Any]]:
    payload = {"api_name": api_name, "token": token, "params": params, "fields": ""}
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post("https://api.tushare.pro", json=payload, timeout=timeout_seconds)
            response.raise_for_status()
            body = response.json()
            if body.get("code", 0) != 0:
                raise RuntimeError(f"{api_name} 返回 code={body.get('code')}: {body.get('msg', 'unknown')}")
            data = body.get("data") or {}
            return [dict(zip(data.get("fields") or [], row)) for row in data.get("items") or []]
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt < max_retries:
                time.sleep(attempt)
    raise RuntimeError(f"{api_name} 请求失败，已重试 {max_retries} 次: {last_error}")


def build_st_timeline(
    *,
    token: str,
    universe_manifest: Path,
    output_path: Path,
    timeout_seconds: float = 30,
    max_retries: int = 3,
    delay_seconds: float = 0.2,
    limit: int | None = None,
) -> dict[str, Any]:
    """Fetch namechange history for every universe symbol and write an ST timeline."""
    active, meta = load_point_in_time_universe(universe_manifest, date.max)
    symbols = active[:limit] if limit is not None else active
    # The universe manifest is authoritative for exchange identity.  Inferring
    # ``.SH``/``.SZ`` from a symbol prefix loses support for BJ and future
    # exchange codes, and can silently query the wrong security.
    ts_codes: dict[str, str] = {}
    for line_number, line in enumerate(universe_manifest.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            symbol = str(item["symbol"]).zfill(6)
            ts_code = str(item.get("ts_code") or "").strip()
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"股票池清单第 {line_number} 行无效") from exc
        if ts_code:
            ts_codes[symbol] = ts_code
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    failures: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    for position, symbol in enumerate(symbols, start=1):
        ts_code = ts_codes.get(symbol)
        if not ts_code:
            failures[symbol] = "股票池清单缺少 ts_code，拒绝猜测交易所"
            continue
        try:
            records = _request(token, "namechange", {"ts_code": ts_code}, timeout_seconds, max_retries)
            for item in records:
                name = str(item.get("name") or "")
                if any(marker in name for marker in ST_MARKERS):
                    rows.append({
                        "symbol": symbol,
                        "active_from": item.get("start_date") or "19700101",
                        "active_to": item.get("end_date") or "",
                        "name": name,
                        "source": "tushare_namechange",
                    })
        except Exception as exc:
            failures[symbol] = str(exc)[:300]
        if delay_seconds:
            time.sleep(delay_seconds)
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    temporary.replace(output_path)
    result = {
        "schema_version": "st-timeline/v1",
        "status": "completed" if not failures else "partial",
        "built_at": datetime.now(timezone.utc).isoformat(),
        "symbols_queried": len(symbols),
        "st_periods": len(rows),
        "symbols_with_st": len({row["symbol"] for row in rows}),
        "failed_symbols": len(failures),
        "failures_sample": dict(list(failures.items())[:5]),
        "output": str(output_path),
    }
    (output_path.parent / (output_path.stem + ".report.json")).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def _frame(path: Path) -> pd.DataFrame:
    frame = pd.read_parquet(path)
    if not isinstance(frame.index, pd.DatetimeIndex):
        if "date" in frame.columns:
            frame = frame.set_index(pd.to_datetime(frame.pop("date")))
        else:
            frame = frame.set_index(pd.to_datetime(frame.index))
    return frame.sort_index()


def audit_is_st(
    *,
    model_data: Path,
    datasets: tuple[str, ...],
    symbols: list[str],
    st_manifest: Path | None = None,
) -> dict[str, object]:
    """Compare cached ``is_st`` flags with the independent namechange timeline."""
    timeline = load_universe_memberships(st_manifest) if st_manifest else None
    per_dataset: dict[str, dict[str, object]] = {}
    mismatches = 0
    bars_checked = 0
    for dataset in datasets:
        source = LocalParquetMarketData(model_data, dataset)
        has_column = true_bars = total_bars = 0
        for symbol in symbols:
            path = source.dataset_dir / f"{symbol}.parquet"
            if not path.is_file():
                continue
            frame = _frame(path)
            total_bars += len(frame)
            if "is_st" in frame.columns:
                has_column += 1
                true_bars += int(frame["is_st"].fillna(False).astype(bool).sum())
            if timeline is not None and symbol in timeline:
                values = frame["is_st"].fillna(False).astype(bool) if "is_st" in frame.columns else pd.Series(False, index=frame.index)
                expected = pd.Series(
                    [active_on(timeline, symbol, item.date()) for item in frame.index],
                    index=frame.index,
                )
                mismatches += int((values != expected).sum())
                bars_checked += len(frame)
        per_dataset[dataset] = {"symbols_with_column": has_column, "is_st_true_bars": true_bars, "total_bars": total_bars}
    if timeline is not None:
        status = "validated" if mismatches == 0 and bars_checked > 0 else "mismatch"
    else:
        status = "unvalidated"
    return {
        "schema_version": "is-st-audit/v1",
        "status": status,
        "audited_at": pd.Timestamp.utcnow().isoformat(),
        "st_manifest": str(st_manifest) if st_manifest else None,
        "symbols_audited": len(symbols),
        "bars_checked_against_timeline": bars_checked,
        "mismatch_bars": mismatches,
        "mismatch_rate": round(mismatches / bars_checked, 6) if bars_checked else None,
        "per_dataset": per_dataset,
        "note": "timeline 来自 Tushare namechange（名称含 ST/*ST 的区间）；未提供 st-manifest 时只能报告缓存覆盖，不能视为已验证。",
    }
