"""文件型分批运行与断点续跑；不保存行情，只保存批次状态。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Callable


@dataclass(frozen=True, slots=True)
class BatchResult:
    batch_id: int
    symbols: tuple[str, ...]
    status: str
    result: dict | None = None
    error: str | None = None


def run_in_batches(symbols: list[str], batch_size: int, checkpoint_path: Path, runner: Callable[[list[str]], dict]) -> list[BatchResult]:
    if batch_size < 1:
        raise ValueError("batch_size 必须为正整数")
    ordered = sorted(set(symbols))
    checkpoint = _load(checkpoint_path)
    completed = {int(item["batch_id"]): item for item in checkpoint.get("batches", []) if item.get("status") == "completed"}
    results: list[BatchResult] = []
    for batch_id, start in enumerate(range(0, len(ordered), batch_size)):
        group = tuple(ordered[start:start + batch_size])
        if batch_id in completed:
            results.append(BatchResult(batch_id, group, "completed", completed[batch_id].get("result")))
            continue
        try:
            result = runner(list(group))
            item = BatchResult(batch_id, group, "completed", result)
        except Exception as exc:  # checkpoint the error; a later run can retry it.
            item = BatchResult(batch_id, group, "failed", error=f"{type(exc).__name__}: {exc}")
        results.append(item)
        _write(checkpoint_path, ordered, batch_size, results)
    return results


def _load(path: Path) -> dict:
    if not path.is_file():
        return {"batches": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, symbols: list[str], batch_size: int, results: list[BatchResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": "batch-checkpoint/v1", "updated_at": datetime.now(timezone.utc).isoformat(), "symbols": symbols, "batch_size": batch_size, "batches": [{"batch_id": item.batch_id, "symbols": list(item.symbols), "status": item.status, "result": item.result, "error": item.error} for item in results]}
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)
