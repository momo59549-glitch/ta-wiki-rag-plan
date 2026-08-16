"""Pure canonical-row validation and draft manifests for Gen3 Phase 1."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from math import isfinite
import re
from types import MappingProxyType
from typing import Iterable, Mapping

from .gen3_pit import PITRecord, make_pit_record
from .gen3_policy import DataClass
from .gen3_providers import CANONICAL_FIELDS, SourceFieldMapping

ROW_VALIDATION_VERSION = "gen3-row-validation-draft/v1"
MANIFEST_SCHEMA_VERSION = "gen3-source-manifest-draft/v1"
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_PIT_DOMAINS = frozenset({DataClass.FUNDAMENTALS, DataClass.ANNOUNCEMENTS, DataClass.NEWS})


def _json(value: object) -> object:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if type(value) is date:
        return value.isoformat()
    return value


def _hash(value: object) -> str:
    return "sha256:" + sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_json).encode()).hexdigest()


def _text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty trimmed string")
    return value


def _day(name: str, value: object) -> date:
    if type(value) is not date:
        raise ValueError(f"{name} must be a date (not datetime)")
    return value


def _number(name: str, value: object, minimum: float | None = None) -> float | int:
    if type(value) not in (int, float) or not isfinite(value):
        raise ValueError(f"{name} must be a finite non-boolean number")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _boolean(name: str, value: object) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{name} must be bool")
    return value


@dataclass(frozen=True)
class CanonicalRow:
    source_id: str
    domain: DataClass
    values: tuple[tuple[str, object], ...]
    row_hash: str
    mapping_hash: str
    validation_version: str = ROW_VALIDATION_VERSION
    pit_record_hash: str | None = None

    @property
    def mapping(self) -> Mapping[str, object]:
        return MappingProxyType(dict(self.values))

    def verify(self) -> None:
        if self.validation_version != ROW_VALIDATION_VERSION:
            raise ValueError("unexpected row validation_version")
        _text("source_id", self.source_id)
        if not isinstance(self.domain, DataClass) or not self.values or self.values != tuple(sorted(self.values)) or len({key for key, _ in self.values}) != len(self.values):
            raise ValueError("canonical row domain or sorted unique values is invalid")
        if {key for key, _ in self.values} != CANONICAL_FIELDS[self.domain]:
            raise ValueError("canonical row values must exactly match domain schema")
        if not isinstance(self.mapping_hash, str) or not _HASH_RE.fullmatch(self.mapping_hash):
            raise ValueError("mapping_hash must match sha256:<64 lowercase hex>")
        if not isinstance(self.row_hash, str) or not _HASH_RE.fullmatch(self.row_hash):
            raise ValueError("row_hash must match sha256:<64 lowercase hex>")
        pit = _validate_domain(self.domain, self.mapping, self.source_id)
        if self.domain in _PIT_DOMAINS:
            if not isinstance(self.pit_record_hash, str) or not _HASH_RE.fullmatch(self.pit_record_hash):
                raise ValueError("PIT row must have a valid pit_record_hash")
            if pit is None or pit.record_hash != self.pit_record_hash:
                raise ValueError("pit_record_hash does not match canonical PIT record")
        elif self.pit_record_hash is not None:
            raise ValueError("non-PIT row cannot have pit_record_hash")
        expected = _hash({"source_id": self.source_id, "domain": self.domain.value, "mapping_hash": self.mapping_hash, "validation_version": self.validation_version, "values": {key: _json(value) for key, value in self.values}, "pit_record_hash": self.pit_record_hash})
        if self.row_hash != expected:
            raise ValueError("row_hash does not match canonical row payload")


def _validate_domain(domain: DataClass, values: Mapping[str, object], source_id: str) -> PITRecord | None:
    if domain == DataClass.MARKET:
        _text("symbol", values["symbol"]); _day("session", values["session"])
        open_, high, low, close = (_number(name, values[name], 0.0000001) for name in ("open", "high", "low", "close"))
        _number("volume", values["volume"], 0)
        if high < max(open_, low, close) or low > min(open_, high, close):
            raise ValueError("OHLC high/low bounds are invalid")
        return None
    if domain == DataClass.TRADABILITY:
        _text("symbol", values["symbol"]); _day("session", values["session"])
        flags = {name: _boolean(name, values[name]) for name in ("is_st", "is_suspended", "is_limit_up", "is_limit_down", "can_buy", "can_sell")}
        if flags["is_suspended"] and (flags["can_buy"] or flags["can_sell"]):
            raise ValueError("suspended rows cannot be buyable or sellable")
        if flags["is_limit_up"] and flags["can_buy"]:
            raise ValueError("limit-up rows cannot be buyable")
        if flags["is_limit_down"] and flags["can_sell"]:
            raise ValueError("limit-down rows cannot be sellable")
        if flags["is_limit_up"] and flags["is_limit_down"]:
            raise ValueError("a row cannot be both limit-up and limit-down")
        if flags["is_suspended"] and (flags["is_limit_up"] or flags["is_limit_down"]):
            raise ValueError("suspended rows cannot carry limit flags")
        return None
    if domain == DataClass.INDEX_CONSTITUENTS:
        _text("index_symbol", values["index_symbol"]); _text("constituent_symbol", values["constituent_symbol"])
        start = _day("effective_from", values["effective_from"])
        end = values["effective_to"]
        if end is not None and (_day("effective_to", end) < start):
            raise ValueError("effective_to cannot precede effective_from")
        return None
    pit = make_pit_record(source=source_id, source_record_id=_text("source_record_id", values["source_record_id"]), published_at=values["published_at"], available_at=values["available_at"], effective_session=_day("effective_session", values["effective_session"]), revision_id=_text("revision_id", values["revision_id"]), content_hash=values["content_hash"], ingested_at=values["ingested_at"], symbol_mapping_version=_text("symbol_mapping_version", values["symbol_mapping_version"]))
    _text("symbol", values["symbol"])
    if domain == DataClass.FUNDAMENTALS:
        _text("value_name", values["value_name"]); _number("value", values["value"])
    elif domain == DataClass.ANNOUNCEMENTS:
        _text("event_type", values["event_type"])
    elif domain == DataClass.NEWS:
        _text("title", values["title"]); _text("content", values["content"])
    return pit


def canonicalize_and_validate_row(mapping: SourceFieldMapping, raw_row: Mapping[str, object]) -> CanonicalRow:
    """Use only explicit mapping columns; extra source fields never enter hashes."""
    mapping.validate()
    if not isinstance(raw_row, Mapping):
        raise ValueError("raw_row must be a Mapping")
    missing = [column for column in mapping.mapping.values() if column not in raw_row]
    if missing:
        raise ValueError(f"raw_row is missing mapped columns: {sorted(missing)}")
    values = {canonical: raw_row[source] for canonical, source in mapping.mapping.items()}
    pit = _validate_domain(mapping.domain, values, mapping.source_id)
    ordered = tuple(sorted(values.items()))
    pit_hash = pit.record_hash if pit else None
    row = CanonicalRow(mapping.source_id, mapping.domain, ordered, "sha256:" + "0" * 64, mapping.mapping_hash, pit_record_hash=pit_hash)
    result = CanonicalRow(**{**row.__dict__, "row_hash": _hash({"source_id": row.source_id, "domain": row.domain.value, "mapping_hash": row.mapping_hash, "validation_version": row.validation_version, "values": {key: _json(value) for key, value in row.values}, "pit_record_hash": row.pit_record_hash})})
    result.verify()
    return result


@dataclass(frozen=True)
class DraftSourceManifest:
    source_id: str
    domain: DataClass
    mapping_hash: str
    row_validation_version: str
    row_hashes: tuple[str, ...]
    record_count: int
    min_session: date
    max_session: date
    manifest_hash: str
    schema_version: str = MANIFEST_SCHEMA_VERSION

    def verify(self) -> None:
        if self.schema_version != MANIFEST_SCHEMA_VERSION or self.row_validation_version != ROW_VALIDATION_VERSION:
            raise ValueError("unexpected manifest schema or row validation version")
        _text("source_id", self.source_id)
        if not isinstance(self.domain, DataClass):
            raise ValueError("manifest domain must be a DataClass")
        if not isinstance(self.mapping_hash, str) or not _HASH_RE.fullmatch(self.mapping_hash):
            raise ValueError("manifest mapping_hash must match sha256:<64 lowercase hex>")
        if not isinstance(self.manifest_hash, str) or not _HASH_RE.fullmatch(self.manifest_hash):
            raise ValueError("manifest_hash must match sha256:<64 lowercase hex>")
        if not self.row_hashes or self.row_hashes != tuple(sorted(self.row_hashes)) or len(set(self.row_hashes)) != len(self.row_hashes):
            raise ValueError("manifest row_hashes must be non-empty sorted and unique")
        if any(not isinstance(item, str) or not _HASH_RE.fullmatch(item) for item in self.row_hashes):
            raise ValueError("manifest row_hashes must be sha256 hashes")
        if type(self.record_count) is not int or self.record_count != len(self.row_hashes) or type(self.min_session) is not date or type(self.max_session) is not date or self.min_session > self.max_session:
            raise ValueError("manifest count or session range is invalid")
        expected = _hash({"schema_version": self.schema_version, "source_id": self.source_id, "domain": self.domain.value, "mapping_hash": self.mapping_hash, "row_validation_version": self.row_validation_version, "row_hashes": list(self.row_hashes), "record_count": self.record_count, "min_session": self.min_session.isoformat(), "max_session": self.max_session.isoformat()})
        if self.manifest_hash != expected:
            raise ValueError("manifest_hash does not match canonical manifest payload")


def build_draft_source_manifest(mapping: SourceFieldMapping, rows: Iterable[CanonicalRow]) -> DraftSourceManifest:
    mapping.validate()
    values = tuple(rows)
    if not values:
        raise ValueError("manifest cannot be empty")
    dates: list[date] = []
    hashes: list[str] = []
    session_field = "effective_session" if mapping.domain in {DataClass.FUNDAMENTALS, DataClass.ANNOUNCEMENTS, DataClass.NEWS} else "effective_from" if mapping.domain == DataClass.INDEX_CONSTITUENTS else "session"
    for row in values:
        row.verify()
        if row.source_id != mapping.source_id or row.domain != mapping.domain or row.mapping_hash != mapping.mapping_hash:
            raise ValueError("manifest rows mix source, domain or mapping")
        hashes.append(row.row_hash); dates.append(_day(session_field, row.mapping[session_field]))
    if len(hashes) != len(set(hashes)):
        raise ValueError("manifest rows contain duplicate row_hash")
    ordered = tuple(sorted(hashes))
    payload = {"schema_version": MANIFEST_SCHEMA_VERSION, "source_id": mapping.source_id, "domain": mapping.domain.value, "mapping_hash": mapping.mapping_hash, "row_validation_version": ROW_VALIDATION_VERSION, "row_hashes": list(ordered), "record_count": len(ordered), "min_session": min(dates).isoformat(), "max_session": max(dates).isoformat()}
    result = DraftSourceManifest(mapping.source_id, mapping.domain, mapping.mapping_hash, ROW_VALIDATION_VERSION, ordered, len(ordered), min(dates), max(dates), _hash(payload))
    result.verify()
    return result
