"""Draft, pure point-in-time contracts for Gen3 Phase 1.

This module has no provider, filesystem, network, candidate, ledger, or
backtest code. It only makes provenance and availability rules explicit.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from hashlib import sha256
import json
import re
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo

PIT_SCHEMA_VERSION = "gen3-pit-draft/v1"
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REQUIRED_FIELDS = frozenset({"schema_version", "source", "source_record_id", "published_at", "available_at", "effective_session", "revision_id", "content_hash", "ingested_at", "symbol_mapping_version", "record_hash"})


def _require_text(name: str, value: object) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty trimmed string")


def _require_aware(name: str, value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _require_hash(name: str, value: object) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{name} must match sha256:<64 lowercase hex>")


def _canonical_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return value.isoformat() if isinstance(value, date) else value


@dataclass(frozen=True)
class PITRecord:
    """Immutable, auditable provenance record; no payload rows are stored."""
    source: str
    source_record_id: str
    published_at: datetime
    available_at: datetime
    effective_session: date
    revision_id: str
    content_hash: str
    ingested_at: datetime
    symbol_mapping_version: str
    record_hash: str
    schema_version: str = PIT_SCHEMA_VERSION

    @property
    def identity(self) -> tuple[str, str, str, str]:
        return (self.source, self.source_record_id, self.revision_id, self.content_hash)

    def canonical_payload(self) -> dict[str, object]:
        return {"schema_version": self.schema_version, "source": self.source, "source_record_id": self.source_record_id, "published_at": _canonical_value(self.published_at), "available_at": _canonical_value(self.available_at), "effective_session": _canonical_value(self.effective_session), "revision_id": self.revision_id, "content_hash": self.content_hash, "ingested_at": _canonical_value(self.ingested_at), "symbol_mapping_version": self.symbol_mapping_version}

    def computed_record_hash(self) -> str:
        encoded = json.dumps(self.canonical_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return f"sha256:{sha256(encoded).hexdigest()}"

    def validate(self) -> None:
        if self.schema_version != PIT_SCHEMA_VERSION:
            raise ValueError("unexpected PIT schema_version")
        for name in ("source", "source_record_id", "revision_id", "symbol_mapping_version"):
            _require_text(name, getattr(self, name))
        for name in ("published_at", "available_at", "ingested_at"):
            _require_aware(name, getattr(self, name))
        if type(self.effective_session) is not date:
            raise ValueError("effective_session must be a date")
        if self.published_at > self.available_at or self.available_at > self.ingested_at:
            raise ValueError("timestamps must satisfy published_at <= available_at <= ingested_at")
        knowledge_date = max(self.published_at, self.available_at).astimezone(
            ZoneInfo("Asia/Shanghai")
        ).date()
        if self.effective_session <= knowledge_date:
            raise ValueError("effective_session must be strictly after the local knowledge date")
        _require_hash("content_hash", self.content_hash)
        _require_hash("record_hash", self.record_hash)

    def verify(self) -> None:
        self.validate()
        if self.record_hash != self.computed_record_hash():
            raise ValueError("record_hash does not match canonical PIT payload")

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> "PITRecord":
        if set(values) != _REQUIRED_FIELDS:
            missing, extra = sorted(_REQUIRED_FIELDS - set(values)), sorted(set(values) - _REQUIRED_FIELDS)
            raise ValueError(f"PIT record fields must match whitelist; missing={missing}, extra={extra}")
        record = cls(**values)  # type: ignore[arg-type]
        record.verify()
        return record


def make_pit_record(*, source: str, source_record_id: str, published_at: datetime, available_at: datetime, effective_session: date, revision_id: str, content_hash: str, ingested_at: datetime, symbol_mapping_version: str, schema_version: str = PIT_SCHEMA_VERSION) -> PITRecord:
    provisional = PITRecord(source, source_record_id, published_at, available_at, effective_session, revision_id, content_hash, ingested_at, symbol_mapping_version, "sha256:" + "0" * 64, schema_version)
    provisional.validate()
    record = PITRecord(**{**provisional.__dict__, "record_hash": provisional.computed_record_hash()})
    record.verify()
    return record


def assert_unique_identities(records: Iterable[PITRecord]) -> None:
    seen: set[tuple[str, str, str, str]] = set()
    revision_content: dict[tuple[str, str, str], str] = {}
    for record in records:
        record.verify()
        if record.identity in seen:
            raise ValueError(f"duplicate PIT identity: {record.identity!r}")
        revision_key = (record.source, record.source_record_id, record.revision_id)
        previous_content = revision_content.get(revision_key)
        if previous_content is not None and previous_content != record.content_hash:
            raise ValueError(f"conflicting content for revision: {revision_key!r}")
        seen.add(record.identity)
        revision_content[revision_key] = record.content_hash


def compute_effective_session(trading_sessions: Iterable[date], published_at: datetime, available_at: datetime, *, market_timezone: str = "Asia/Shanghai", cutoff: time = time(15, 0)) -> date:
    """Return next session after availability; the validated cutoff is inert in this draft."""
    if market_timezone != "Asia/Shanghai":
        raise ValueError("Phase 1 supports only market_timezone='Asia/Shanghai'")
    if type(cutoff) is not time or cutoff.tzinfo is not None:
        raise ValueError("cutoff must be a naive datetime.time")
    published, available = _require_aware("published_at", published_at), _require_aware("available_at", available_at)
    if published > available:
        raise ValueError("published_at must not be later than available_at")
    sessions = tuple(trading_sessions)
    if not sessions or any(type(item) is not date for item in sessions):
        raise ValueError("trading_sessions must be non-empty date values")
    if any(later <= earlier for earlier, later in zip(sessions, sessions[1:])):
        raise ValueError("trading_sessions must be strictly increasing and unique")
    local_date = max(published, available).astimezone(ZoneInfo(market_timezone)).date()
    for session in sessions:
        if session > local_date:
            return session
    raise ValueError("no future trading session is available")
