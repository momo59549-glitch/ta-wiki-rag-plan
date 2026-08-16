"""Fail-closed, local schema admission for Gen3 Phase 1.

This is deliberately limited to explicit source-to-canonical column mappings
and optional Parquet *schema* metadata. It never reads rows, creates PIT
records, recurses directories, writes files, or declares a source AVAILABLE.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .gen3_policy import DataClass


PROVIDER_SCHEMA_VERSION = "gen3-provider-draft/v1"
SCHEMA_COMPATIBLE_NOT_PIT_VERIFIED = "schema_compatible_not_pit_verified"

CANONICAL_FIELDS: Mapping[DataClass, frozenset[str]] = MappingProxyType({
    DataClass.MARKET: frozenset({"symbol", "session", "open", "high", "low", "close", "volume"}),
    DataClass.FUNDAMENTALS: frozenset({"source_record_id", "symbol", "value_name", "value", "published_at", "available_at", "effective_session", "revision_id", "content_hash", "ingested_at", "symbol_mapping_version"}),
    DataClass.ANNOUNCEMENTS: frozenset({"source_record_id", "symbol", "published_at", "available_at", "effective_session", "revision_id", "content_hash", "ingested_at", "symbol_mapping_version", "event_type"}),
    DataClass.NEWS: frozenset({"source_record_id", "symbol", "published_at", "available_at", "effective_session", "revision_id", "content_hash", "ingested_at", "symbol_mapping_version", "title", "content"}),
    DataClass.INDEX_CONSTITUENTS: frozenset({"index_symbol", "constituent_symbol", "effective_from", "effective_to"}),
    DataClass.TRADABILITY: frozenset({"symbol", "session", "is_st", "is_suspended", "is_limit_up", "is_limit_down", "can_buy", "can_sell"}),
})
_PIT_DOMAINS = frozenset({DataClass.FUNDAMENTALS, DataClass.ANNOUNCEMENTS, DataClass.NEWS})


def _text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty trimmed string")
    return value


@dataclass(frozen=True)
class SourceFieldMapping:
    """Immutable explicit mapping; all canonical fields must be accounted for."""
    source_id: str
    domain: DataClass
    schema_version: str
    root: str
    file_format: str
    fields: tuple[tuple[str, str], ...]

    @classmethod
    def from_mapping(
        cls,
        *,
        source_id: str,
        domain: DataClass,
        schema_version: str,
        root: str,
        file_format: str,
        mapping: Mapping[str, str],
    ) -> "SourceFieldMapping":
        if not isinstance(domain, DataClass):
            raise ValueError("domain must be a DataClass")
        required = CANONICAL_FIELDS[domain]
        keys = set(mapping)
        if keys != required:
            raise ValueError(f"mapping keys must exactly match canonical schema; missing={sorted(required - keys)}, extra={sorted(keys - required)}")
        frozen = tuple(sorted(mapping.items()))
        instance = cls(source_id, domain, schema_version, root, file_format, frozen)
        instance.validate()
        return instance

    @property
    def mapping(self) -> Mapping[str, str]:
        return MappingProxyType(dict(self.fields))

    @property
    def mapping_hash(self) -> str:
        payload = {
            "source_id": self.source_id,
            "domain": self.domain.value,
            "schema_version": self.schema_version,
            "root": self.root,
            "file_format": self.file_format,
            "fields": list(self.fields),
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return f"sha256:{sha256(encoded).hexdigest()}"

    def validate(self) -> None:
        for name in ("source_id", "schema_version", "root", "file_format"):
            _text(name, getattr(self, name))
        if self.schema_version != PROVIDER_SCHEMA_VERSION:
            raise ValueError("schema_version must equal PROVIDER_SCHEMA_VERSION")
        if self.file_format != "parquet":
            raise ValueError("file_format must exactly equal 'parquet'")
        if not isinstance(self.domain, DataClass):
            raise ValueError("domain must be a DataClass")
        expected = CANONICAL_FIELDS[self.domain]
        items = tuple(self.fields)
        if len(items) != len(expected) or {key for key, _ in items} != expected:
            raise ValueError("mapping keys must exactly match canonical schema")
        source_columns: list[str] = []
        for key, value in items:
            _text("canonical field", key)
            source_columns.append(_text("source column", value))
        if len(source_columns) != len(set(source_columns)):
            raise ValueError("multiple canonical fields cannot map to one source column")


@dataclass(frozen=True)
class SchemaAdmission:
    """Schema-only outcome; never evidence that PIT rows are valid."""
    source_id: str
    domain: DataClass
    schema_version: str
    status: str
    observed_columns: tuple[str, ...]
    mapping_hash: str
    row_validation_required: bool
    pit_row_validation_required: bool


@dataclass(frozen=True)
class ParquetSchemaObservation:
    path: Path
    size_bytes: int
    observed_columns: tuple[str, ...]


def validate_observed_schema(mapping: SourceFieldMapping, observed_columns: object) -> SchemaAdmission:
    """Validate names only and fail closed on any absent explicitly mapped column."""
    mapping.validate()
    if not isinstance(observed_columns, (tuple, list, set, frozenset)):
        raise ValueError("observed_columns must be a concrete collection of column names")
    columns = tuple(observed_columns)
    if any(not isinstance(column, str) or not column or column != column.strip() for column in columns):
        raise ValueError("observed_columns must contain non-empty trimmed strings")
    if len(columns) != len(set(columns)):
        raise ValueError("observed_columns cannot contain duplicates")
    missing = set(mapping.mapping.values()) - set(columns)
    if missing:
        raise ValueError(f"source schema is missing mapped columns: {sorted(missing)}")
    return SchemaAdmission(
        source_id=mapping.source_id,
        domain=mapping.domain,
        schema_version=mapping.schema_version,
        status=SCHEMA_COMPATIBLE_NOT_PIT_VERIFIED,
        observed_columns=tuple(sorted(columns)),
        mapping_hash=mapping.mapping_hash,
        row_validation_required=True,
        pit_row_validation_required=mapping.domain in _PIT_DOMAINS,
    )


def inspect_parquet_schema(explicit_file: str | Path) -> ParquetSchemaObservation:
    """Read only schema metadata from one explicit existing ``.parquet`` file."""
    path = Path(explicit_file)
    if path.is_dir():
        raise ValueError("explicit_file must be an existing file, not a directory")
    if path.suffix.lower() != ".parquet":
        raise ValueError("explicit_file must have a .parquet suffix")
    if not path.is_file():
        raise ValueError("explicit_file must be an existing file, not a directory")
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - dependency is environment-specific
        raise RuntimeError("pyarrow is required for Parquet schema inspection") from exc
    return ParquetSchemaObservation(path, path.stat().st_size, tuple(pq.ParquetFile(path).schema.names))


def legacy_fin_cache_mapping(*, root: str = "<explicit-root-required>") -> SourceFieldMapping:
    """Fail closed: NOTICE_DATE/ROEJQ/symbol cannot satisfy the PIT contract."""
    return SourceFieldMapping.from_mapping(
        source_id="legacy_fin_cache",
        domain=DataClass.FUNDAMENTALS,
        schema_version=PROVIDER_SCHEMA_VERSION,
        root=root,
        file_format="parquet",
        mapping={"source_record_id": "symbol", "symbol": "symbol", "value_name": "ROEJQ", "value": "ROEJQ", "published_at": "NOTICE_DATE"},
    )
