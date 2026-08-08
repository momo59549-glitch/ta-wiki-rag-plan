"""Point-in-time universe manifest for survivorship-aware research."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
import re


_SYMBOL = re.compile(r"^[0-9]{6}$")


@dataclass(frozen=True, slots=True)
class UniverseMembership:
    symbol: str
    active_from: date
    active_to: date | None
    source: str

    def active_on(self, as_of: date) -> bool:
        return self.active_from <= as_of and (self.active_to is None or as_of <= self.active_to)


def load_point_in_time_universe(manifest_path: Path, as_of: date) -> tuple[list[str], dict]:
    """Load JSONL memberships without falling back to today's file list.

    Each JSONL line needs ``symbol``, ``active_from``, optional ``active_to``
    and ``source``.  A missing manifest is intentionally an error: silently
    scanning current parquet files is survivorship-unsafe.
    """
    if not manifest_path.is_file():
        raise FileNotFoundError(f"缺少点时股票池清单: {manifest_path}")
    active: set[str] = set()
    records = 0
    for line_number, line in enumerate(manifest_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            symbol = str(row["symbol"]).zfill(6)
            membership = UniverseMembership(
                symbol=symbol,
                active_from=date.fromisoformat(row["active_from"]),
                active_to=date.fromisoformat(row["active_to"]) if row.get("active_to") else None,
                source=str(row["source"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"股票池清单第 {line_number} 行无效") from exc
        if not _SYMBOL.fullmatch(membership.symbol):
            raise ValueError(f"股票池清单第 {line_number} 行证券代码非法")
        records += 1
        if membership.active_on(as_of):
            active.add(membership.symbol)
    return sorted(active), {
        "status": "point_in_time",
        "manifest": str(manifest_path),
        "as_of": as_of.isoformat(),
        "records": records,
        "active_symbols": len(active),
    }


def load_universe_memberships(manifest_path: Path) -> dict[str, tuple[UniverseMembership, ...]]:
    """Load the complete historical membership timeline, keyed by symbol."""
    if not manifest_path.is_file():
        raise FileNotFoundError(f"缺少点时股票池清单: {manifest_path}")
    result: dict[str, list[UniverseMembership]] = {}
    for line_number, line in enumerate(manifest_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            symbol = str(row["symbol"]).zfill(6)
            membership = UniverseMembership(symbol, date.fromisoformat(row["active_from"]), date.fromisoformat(row["active_to"]) if row.get("active_to") else None, str(row["source"]))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"股票池清单第 {line_number} 行无效") from exc
        if not _SYMBOL.fullmatch(symbol):
            raise ValueError(f"股票池清单第 {line_number} 行证券代码非法")
        result.setdefault(symbol, []).append(membership)
    return {symbol: tuple(rows) for symbol, rows in result.items()}


def active_on(memberships: dict[str, tuple[UniverseMembership, ...]], symbol: str, as_of: date) -> bool:
    return any(item.active_on(as_of) for item in memberships.get(symbol, ()))
