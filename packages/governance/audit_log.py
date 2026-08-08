"""Append-only, hash-chained audit log for the file MVP."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4


class FileAuditLog:
    _lock = Lock()

    def __init__(self, path: Path):
        self.path = path

    def append(self, event_type: str, actor: str, role: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            previous_hash = self._last_hash()
            record = {
                "schema_version": "audit-event/v1", "audit_id": "audit_" + uuid4().hex,
                "event_type": event_type, "actor": actor, "role": role,
                "occurred_at": datetime.now(timezone.utc).isoformat(), "previous_hash": previous_hash,
                "payload": payload,
            }
            canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            record["record_hash"] = "sha256:" + sha256(canonical.encode()).hexdigest()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            return record

    def verify(self) -> dict[str, Any]:
        previous = None
        count = 0
        if not self.path.is_file():
            return {"valid": True, "records": 0, "error_at": None}
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            claimed = record.pop("record_hash", None)
            if record.get("previous_hash") != previous:
                return {"valid": False, "records": count, "error_at": line_number}
            canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            actual = "sha256:" + sha256(canonical.encode()).hexdigest()
            if claimed != actual:
                return {"valid": False, "records": count, "error_at": line_number}
            previous = claimed
            count += 1
        return {"valid": True, "records": count, "error_at": None}

    def _last_hash(self) -> str | None:
        if not self.path.is_file():
            return None
        lines = [line for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return json.loads(lines[-1])["record_hash"] if lines else None
