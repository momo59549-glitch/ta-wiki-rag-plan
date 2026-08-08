"""Resumable Tushare daily + adjustment-factor downloader.

The downloader writes an independent cache.  It never overwrites
``trend_cache``; a later reviewed merge step is required.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import time
from typing import Any

import pandas as pd
import requests

from packages.market_data.universe import active_on, load_universe_memberships


def sync_missing_tushare_daily(
    *, manifest_path: Path, existing_dataset_dir: Path, output_dataset_dir: Path,
    checkpoint_path: Path, progress_path: Path, start: date, end: date,
    token: str | None = None, limit: int | None = None, timeout_seconds: float = 30,
    max_retries: int = 3, delay_seconds: float = 0.2, st_manifest: Path | None = None,
) -> dict[str, Any]:
    """Fetch missing L/D/P symbols and write compatible Parquet files.

    The checkpoint contains only symbol statuses and non-sensitive errors.
    Each successfully written file is atomic, allowing an interrupted run to
    resume without rereading or replacing completed symbols.
    """
    token = token or os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("未设置 TUSHARE_TOKEN；请通过环境变量提供")
    st_timeline = load_universe_memberships(st_manifest) if st_manifest else None
    if not manifest_path.is_file() or not existing_dataset_dir.is_dir():
        raise FileNotFoundError("股票池清单或现有行情目录不存在")
    output_dataset_dir.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    existing = {path.stem for path in existing_dataset_dir.glob("*.parquet")}
    targets = [row for row in rows if row.get("symbol") not in existing and row.get("list_status") in {"L", "D", "P"} and row.get("active_from", "9999-12-31") <= end.isoformat()]
    targets.sort(key=lambda row: row["symbol"])
    if limit is not None:
        targets = targets[:limit]
    checkpoint = _read_json(checkpoint_path, {"completed": [], "failed": {}})
    completed = set(checkpoint.get("completed") or [])
    failed: dict[str, str] = dict(checkpoint.get("failed") or {})
    pending = [row for row in targets if row["symbol"] not in completed]
    _write_json(progress_path, {
        "status": "running", "total": len(targets), "completed": len(completed & {row["symbol"] for row in targets}),
        "failed": len(failed), "current_symbol": None, "output_dataset": str(output_dataset_dir),
        "updated_at": _now(),
    })
    for offset, row in enumerate(pending, start=1):
        symbol = row["symbol"]
        _write_json(progress_path, {
            "status": "running", "total": len(targets), "completed": len(completed & {item["symbol"] for item in targets}),
            "failed": len(failed), "current_symbol": symbol, "queue_position": offset, "output_dataset": str(output_dataset_dir), "updated_at": _now(),
        })
        try:
            frame = _fetch_symbol_frame(token, row, max(start, date.fromisoformat(row["active_from"])), end, timeout_seconds, max_retries, st_timeline=st_timeline)
            if frame.empty:
                raise RuntimeError("无日线数据")
            temporary = output_dataset_dir / f"{symbol}.parquet.tmp"
            target = output_dataset_dir / f"{symbol}.parquet"
            frame.to_parquet(temporary)
            temporary.replace(target)
            completed.add(symbol)
            failed.pop(symbol, None)
        except Exception as exc:  # record and continue; resume can retry failures
            failed[symbol] = str(exc)[:500]
        _write_json(checkpoint_path, {"completed": sorted(completed), "failed": failed, "updated_at": _now()})
        if delay_seconds:
            time.sleep(delay_seconds)
    result = {
        "status": "completed", "total": len(targets),
        "completed": len(completed & {row["symbol"] for row in targets}), "failed": len(failed),
        "output_dataset": str(output_dataset_dir), "checkpoint": str(checkpoint_path),
        "is_st_source": "namechange_timeline" if st_timeline else "unvalidated_false",
    }
    _write_json(progress_path, {**result, "current_symbol": None, "updated_at": _now()})
    return result


def sync_tushare_incremental(
    *, manifest_path: Path, output_dataset_dir: Path, checkpoint_path: Path,
    progress_path: Path, start: date, end: date, token: str | None = None,
    timeout_seconds: float = 30, max_retries: int = 3, delay_seconds: float = 0.2,
    st_manifest: Path | None = None,
) -> dict[str, Any]:
    """Fetch open trade dates into a raw-price overlay without touching base data."""
    token = token or os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("未设置 TUSHARE_TOKEN；请通过环境变量提供")
    st_timeline = load_universe_memberships(st_manifest) if st_manifest else None
    if start > end:
        raise ValueError("start 不能晚于 end")
    rows = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_code = {row["ts_code"]: row for row in rows if row.get("ts_code") and row.get("symbol")}
    calendar = _request(token, "trade_cal", {"exchange": "SSE", "start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d"), "is_open": "1"}, timeout_seconds, max_retries)
    trade_dates = sorted({str(item["cal_date"]) for item in calendar if str(item.get("is_open", "1")) == "1"})
    checkpoint = _read_json(checkpoint_path, {"completed_trade_dates": [], "failed_trade_dates": {}})
    completed = set(checkpoint.get("completed_trade_dates") or [])
    failed = dict(checkpoint.get("failed_trade_dates") or {})
    pending = [value for value in trade_dates if value not in completed]
    output_dataset_dir.mkdir(parents=True, exist_ok=True)
    for position, trade_date in enumerate(pending, start=1):
        _write_json(progress_path, {"status": "running", "total": len(trade_dates), "completed": len(completed), "current_trade_date": trade_date, "queue_position": position, "updated_at": _now()})
        try:
            daily = _request(token, "daily", {"trade_date": trade_date}, timeout_seconds, max_retries)
            factors = _request(token, "adj_factor", {"trade_date": trade_date}, timeout_seconds, max_retries)
            factor_by_code = {item["ts_code"]: item.get("adj_factor") for item in factors}
            for item in daily:
                record = by_code.get(item.get("ts_code"))
                adj_factor = factor_by_code.get(item.get("ts_code"))
                if not record or adj_factor is None:
                    continue
                _merge_incremental_row(output_dataset_dir / f"{record['symbol']}.parquet", record, item, adj_factor, st_timeline=st_timeline)
            completed.add(trade_date)
            failed.pop(trade_date, None)
        except Exception as exc:
            failed[trade_date] = str(exc)[:500]
        _write_json(checkpoint_path, {"completed_trade_dates": sorted(completed), "failed_trade_dates": failed, "updated_at": _now()})
        if delay_seconds:
            time.sleep(delay_seconds)
    result = {
        "status": "completed", "total": len(trade_dates), "completed": len(completed & set(trade_dates)),
        "failed": len({key: value for key, value in failed.items() if key in trade_dates}),
        "output_dataset": str(output_dataset_dir),
        "is_st_source": "namechange_timeline" if st_timeline else "unvalidated_false",
    }
    _write_json(progress_path, {**result, "current_trade_date": None, "updated_at": _now()})
    return result


def _merge_incremental_row(path: Path, record: dict[str, Any], item: dict[str, Any], adj_factor: Any, *, st_timeline: dict[str, Any] | None = None) -> None:
    index = pd.to_datetime([str(item["trade_date"])])
    factor = float(adj_factor)
    is_st = bool(active_on(st_timeline, record["symbol"], date.fromisoformat(str(item["trade_date"])))) if st_timeline else False
    row = pd.DataFrame({
        "name": [record.get("name", "")], "raw_open": [item.get("open")], "raw_high": [item.get("high")],
        "raw_low": [item.get("low")], "raw_close": [item.get("close")], "raw_prev_close": [item.get("pre_close")],
        "volume": [item.get("vol")], "amount_thousand": [item.get("amount")], "adj_factor": [factor],
        "open": [float(item["open"])], "high": [float(item["high"])], "low": [float(item["low"])], "close": [float(item["close"])],
        "amount": [float(item.get("amount") or 0) * 1000], "is_st": [is_st], "_trend_cache_version": ["tushare_incremental_v1"],
    }, index=index)
    row.index.name = "trade_date"
    if path.is_file():
        row = pd.concat([pd.read_parquet(path), row]).sort_index()
        row = row.loc[~row.index.duplicated(keep="last")]
    temporary = path.with_suffix(path.suffix + ".tmp")
    row.to_parquet(temporary)
    temporary.replace(path)


def _fetch_symbol_frame(
    token: str, record: dict[str, Any], start: date, end: date, timeout_seconds: float, max_retries: int,
    *, st_timeline: dict[str, Any] | None = None,
) -> pd.DataFrame:
    params = {"ts_code": record["ts_code"], "start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d")}
    daily = _request(token, "daily", params, timeout_seconds, max_retries)
    factors = _request(token, "adj_factor", params, timeout_seconds, max_retries)
    if not daily:
        return pd.DataFrame()
    prices = pd.DataFrame(daily).set_index("trade_date")
    adjustments = pd.DataFrame(factors).set_index("trade_date") if factors else pd.DataFrame(index=prices.index)
    frame = prices.join(adjustments[["adj_factor"]], how="left").sort_index()
    frame.index = pd.to_datetime(frame.index)
    frame = frame.dropna(subset=["open", "high", "low", "close", "pre_close", "adj_factor"])
    if frame.empty:
        return frame
    scale = float(frame["adj_factor"].iloc[-1])
    result = pd.DataFrame(index=frame.index)
    result["name"] = record.get("name", "")
    for column in ("open", "high", "low", "close"):
        result[f"raw_{column}"] = pd.to_numeric(frame[column], errors="coerce")
    result["raw_prev_close"] = pd.to_numeric(frame["pre_close"], errors="coerce")
    result["volume"] = pd.to_numeric(frame["vol"], errors="coerce")
    result["amount_thousand"] = pd.to_numeric(frame["amount"], errors="coerce")
    result["adj_factor"] = pd.to_numeric(frame["adj_factor"], errors="coerce")
    for column in ("open", "high", "low", "close"):
        result[column] = result[f"raw_{column}"] * result["adj_factor"] / scale
    result["amount"] = result["amount_thousand"] * 1000
    result["is_st"] = [
        bool(active_on(st_timeline, record["symbol"], item.date())) if st_timeline else False
        for item in frame.index
    ]
    result["_trend_cache_version"] = "tushare_daily_v1"
    return result.dropna(subset=["open", "high", "low", "close"])


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


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
