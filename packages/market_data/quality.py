"""Preflight quality profile for a frozen strategy-test data source."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Iterable


def audit_market_data_quality(
    source: Any,
    symbols: Iterable[str],
    output: Path,
    *,
    as_of: date,
    active_at_end: Iterable[str],
    minimum_bars: int = 60,
    freshness_calendar_days: int = 14,
) -> dict:
    active = set(active_at_end)
    failures = []
    stale_active = []
    short_histories = []
    loaded = 0
    rows = 0
    earliest = latest = None
    freshness_cutoff = as_of - timedelta(days=freshness_calendar_days)
    requested = sorted(set(symbols))
    for symbol in requested:
        try:
            candles = source.load(symbol, end=as_of)
            if not candles:
                raise ValueError("no_valid_candles")
        except (FileNotFoundError, ValueError, OSError) as exc:
            failures.append({"symbol": symbol, "reason": type(exc).__name__})
            continue
        loaded += 1
        rows += len(candles)
        first_day = candles[0].timestamp.date()
        last_day = candles[-1].timestamp.date()
        earliest = first_day if earliest is None or first_day < earliest else earliest
        latest = last_day if latest is None or last_day > latest else latest
        if len(candles) < minimum_bars:
            short_histories.append(symbol)
        if symbol in active and last_day < freshness_cutoff:
            stale_active.append({"symbol": symbol, "last_date": last_day.isoformat()})
    active_with_data = len(active) - sum(item["symbol"] in active for item in failures)
    fresh_active = active_with_data - len(stale_active)
    freshness_ratio = fresh_active / len(active) if active else 0.0
    status = "passed" if not failures and freshness_ratio >= 0.99 else "failed"
    payload = {
        "schema_version": "market-data-quality/v1",
        "status": status,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of.isoformat(),
        "symbols_requested": len(requested),
        "symbols_loaded": loaded,
        "rows_loaded": rows,
        "earliest_date": earliest.isoformat() if earliest else None,
        "latest_date": latest.isoformat() if latest else None,
        "active_symbols": len(active),
        "fresh_active_symbols": fresh_active,
        "active_freshness_ratio": freshness_ratio,
        "minimum_bars": minimum_bars,
        "short_history_count": len(short_histories),
        "short_history_symbols": short_histories[:100],
        "load_failure_count": len(failures),
        "load_failures": failures[:100],
        "stale_active_count": len(stale_active),
        "stale_active_symbols": stale_active[:100],
        "note": "短历史用于资格过滤但不视为数据损坏；加载失败或当前有效股票陈旧会阻断策略测试。",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
