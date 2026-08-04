"""面向 MVP 的追加式 JSONL 存储；领域层不依赖数据库。"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
import json
from pathlib import Path
from typing import Iterable


def _default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, tuple):
        return list(value)
    raise TypeError(type(value).__name__)


def write_jsonl(path: Path, values: Iterable[object]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    count = 0
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        for value in values:
            payload = asdict(value) if is_dataclass(value) else value
            handle.write(json.dumps(payload, ensure_ascii=False, default=_default, sort_keys=True) + "\n")
            count += 1
    temp.replace(path)
    return count


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(value) if is_dataclass(value) else value
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, default=_default, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(path)
