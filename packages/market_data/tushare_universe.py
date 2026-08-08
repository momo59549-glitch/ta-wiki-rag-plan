"""One-shot/periodic Tushare stock-basic importer for point-in-time universes."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from typing import Any

import requests


STATUSES = ("L", "D", "P", "G")


def build_tushare_universe_manifest(
    output_path: Path, *, token: str | None = None, progress_path: Path | None = None,
    timeout_seconds: float = 30.0, max_retries: int = 3,
) -> dict[str, Any]:
    """Fetch listed, delisted, suspended and untraded A-share basics.

    The token is accepted only in memory.  It is never written to the
    manifest, logs, configuration, or exception text.
    """
    token = token or os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("未设置 TUSHARE_TOKEN；请通过环境变量提供，不要写入文件")
    if timeout_seconds <= 0 or max_retries < 1:
        raise ValueError("timeout_seconds 必须为正，max_retries 至少为 1")
    fetched_at = datetime.now(timezone.utc).isoformat()
    records: dict[str, dict[str, Any]] = {}
    completed: list[str] = []
    _write_progress(progress_path, status="running", current_status=None, completed_statuses=completed, records=0, started_at=fetched_at)
    for status in STATUSES:
        _write_progress(progress_path, status="running", current_status=status, completed_statuses=completed, records=len(records), started_at=fetched_at)
        rows = _request_stock_basic(token, status, timeout_seconds, max_retries)
        for row in rows:
            symbol = str(row.get("symbol") or "").zfill(6)
            list_date = _date_string(row.get("list_date"))
            if len(symbol) != 6 or not list_date:
                continue
            existing = records.get(symbol)
            # A delisted record contains the most important terminal date.
            if existing and existing["list_status"] == "D":
                continue
            records[symbol] = {
                "symbol": symbol,
                "ts_code": str(row.get("ts_code") or ""),
                "name": str(row.get("name") or ""),
                "exchange": str(row.get("exchange") or ""),
                "market": str(row.get("market") or ""),
                "list_status": status,
                "active_from": list_date,
                "active_to": _date_string(row.get("delist_date")) if status == "D" else None,
                "source": "tushare.stock_basic",
                "fetched_at": fetched_at,
            }
        completed.append(status)
        _write_progress(progress_path, status="running", current_status=None, completed_statuses=completed, records=len(records), started_at=fetched_at)
    if not records:
        raise RuntimeError("Tushare 未返回股票基础信息；请检查 Token 权限或网络")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in sorted(records.values(), key=lambda item: item["symbol"]):
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(output_path)
    _write_progress(progress_path, status="completed", current_status=None, completed_statuses=completed, records=len(records), started_at=fetched_at)
    return {
        "status": "completed",
        "output": str(output_path),
        "records": len(records),
        "statuses_requested": list(STATUSES),
        "fetched_at": fetched_at,
        "token_persisted": False,
    }


def _request_stock_basic(token: str, list_status: str, timeout_seconds: float, max_retries: int) -> list[dict[str, Any]]:
    payload = {
        "api_name": "stock_basic",
        "token": token,
        "params": {"exchange": "", "list_status": list_status},
        "fields": "ts_code,symbol,name,exchange,market,list_status,list_date,delist_date",
    }
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post("https://api.tushare.pro", json=payload, timeout=timeout_seconds)
            response.raise_for_status()
            body = response.json()
            if body.get("code", 0) != 0:
                raise RuntimeError(f"Tushare stock_basic[{list_status}] 返回 code={body.get('code')}: {body.get('msg', 'unknown')}")
            data = body.get("data") or {}
            fields, items = data.get("fields") or [], data.get("items") or []
            return [dict(zip(fields, item)) for item in items]
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt < max_retries:
                time.sleep(attempt)
    raise RuntimeError(f"Tushare stock_basic[{list_status}] 请求失败，已重试 {max_retries} 次: {last_error}")


def _write_progress(path: Path | None, **payload: Any) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _date_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text if len(text) == 10 and text[4] == "-" and text[7] == "-" else None
