"""Dry-run-only source acquisition contracts; no network or filesystem writes."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Iterable, Mapping
from urllib.parse import urlparse

from .gen3_policy import DataClass

ACQUISITION_SCHEMA_VERSION = "gen3-acquisition-draft/v1"
_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_ENV = re.compile(r"^[A-Z][A-Z0-9_]*$")
_DATASET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
_PROVIDERS = frozenset({"local_parquet", "tushare", "cninfo", "sse", "szse"})


def _hash(payload: object) -> str:
    return "sha256:" + sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _text(name: str, value: object) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty trimmed string")


def _positive(name: str, value: object) -> None:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive non-boolean integer")


@dataclass(frozen=True)
class SourceAcquisitionSpec:
    domain: DataClass
    provider: str
    endpoint_or_dataset_id: str
    credential_env_name: str | None
    start: date
    end: date
    expected_mapping_hash: str
    supports_historical_revisions: bool
    supports_published_at: bool
    supports_available_at: bool
    supports_effective_session: bool
    supports_content_hash: bool
    write_mode: str
    max_records: int
    max_bytes: int
    license_note: str
    terms_url: str
    spec_hash: str
    schema_version: str = ACQUISITION_SCHEMA_VERSION

    def payload(self) -> dict[str, object]:
        return {"schema_version": self.schema_version, "domain": self.domain.value, "provider": self.provider, "endpoint_or_dataset_id": self.endpoint_or_dataset_id, "credential_env_name": self.credential_env_name, "start": self.start.isoformat(), "end": self.end.isoformat(), "expected_mapping_hash": self.expected_mapping_hash, "supports_historical_revisions": self.supports_historical_revisions, "supports_published_at": self.supports_published_at, "supports_available_at": self.supports_available_at, "supports_effective_session": self.supports_effective_session, "supports_content_hash": self.supports_content_hash, "write_mode": self.write_mode, "max_records": self.max_records, "max_bytes": self.max_bytes, "license_note": self.license_note, "terms_url": self.terms_url}

    def verify(self) -> None:
        if self.schema_version != ACQUISITION_SCHEMA_VERSION or not isinstance(self.domain, DataClass):
            raise ValueError("invalid acquisition schema_version or domain")
        if self.provider not in _PROVIDERS: raise ValueError("provider is not approved")
        for name in ("endpoint_or_dataset_id", "license_note", "terms_url"): _text(name, getattr(self, name))
        if not _DATASET.fullmatch(self.endpoint_or_dataset_id) or "://" in self.endpoint_or_dataset_id or any(mark in self.endpoint_or_dataset_id for mark in ("@", "=", " ")) or re.search(r"(?i)(token|secret|key|password)=", self.endpoint_or_dataset_id): raise ValueError("endpoint_or_dataset_id must be a provider dataset identifier, not a URL or secret")
        terms = urlparse(self.terms_url)
        if terms.scheme != "https" or not terms.netloc or terms.username or terms.password or terms.query or terms.fragment: raise ValueError("terms_url must be https with netloc and without userinfo/query/fragment")
        if self.credential_env_name is not None and (not isinstance(self.credential_env_name, str) or len(self.credential_env_name) > 64 or not _ENV.fullmatch(self.credential_env_name)):
            raise ValueError("credential_env_name must be an environment variable name, never a token")
        if type(self.start) is not date or type(self.end) is not date or self.start > self.end: raise ValueError("invalid acquisition date range")
        if not _HASH.fullmatch(self.expected_mapping_hash) or not _HASH.fullmatch(self.spec_hash): raise ValueError("mapping/spec hash must be sha256")
        if any(type(getattr(self, name)) is not bool for name in ("supports_historical_revisions", "supports_published_at", "supports_available_at", "supports_effective_session", "supports_content_hash")): raise ValueError("capability flags must be bool")
        if self.write_mode != "write_once_raw_snapshot": raise ValueError("write_mode must be write_once_raw_snapshot")
        _positive("max_records", self.max_records); _positive("max_bytes", self.max_bytes)
        if self.spec_hash != _hash(self.payload()): raise ValueError("spec_hash does not match canonical acquisition spec")

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> "SourceAcquisitionSpec":
        required = set(cls.__dataclass_fields__)
        if set(values) != required: raise ValueError("SourceAcquisitionSpec fields must match whitelist")
        result = cls(**values)  # type: ignore[arg-type]
        result.verify(); return result


def make_spec(**values: object) -> SourceAcquisitionSpec:
    if "spec_hash" in values:
        raise ValueError("make_spec computes spec_hash; callers must not supply it")
    provisional = SourceAcquisitionSpec(**{**values, "spec_hash": "sha256:" + "0" * 64})  # type: ignore[arg-type]
    result = SourceAcquisitionSpec(**{**provisional.__dict__, "spec_hash": _hash(provisional.payload())})
    result.verify(); return result


def readiness(spec: SourceAcquisitionSpec) -> tuple[bool, str | None]:
    spec.verify()
    if spec.domain in {DataClass.FUNDAMENTALS, DataClass.ANNOUNCEMENTS, DataClass.NEWS}:
        needed = (spec.supports_historical_revisions, spec.supports_published_at, spec.supports_available_at, spec.supports_effective_session, spec.supports_content_hash)
        if not all(needed): return False, "PIT domain lacks declared historical revision/provenance capabilities"
    if spec.domain == DataClass.INDEX_CONSTITUENTS and not (spec.supports_historical_revisions and spec.supports_effective_session): return False, "index source lacks declared historical membership/revision capability"
    if spec.domain == DataClass.TRADABILITY and not spec.supports_effective_session: return False, "tradability source lacks declared session semantics"
    return True, None


@dataclass(frozen=True)
class AcquisitionPlan:
    specs: tuple[SourceAcquisitionSpec, ...]
    target_root: Path
    allowed_root: Path
    total_max_records: int
    total_max_bytes: int
    caller_max_records: int
    caller_max_bytes: int
    status: str
    plan_hash: str

    def verify(self) -> None:
        if self.status != "dry_run_only" or not self.specs: raise ValueError("plan must be non-empty dry_run_only")
        if self.specs != tuple(sorted(self.specs, key=lambda item: item.spec_hash)): raise ValueError("plan specs must be sorted")
        identities: set[tuple[DataClass, str, str]] = set(); hashes: set[str] = set()
        for spec in self.specs:
            spec.verify()
            if not readiness(spec)[0]: raise ValueError("plan contains blocked acquisition spec")
            identity = (spec.domain, spec.provider, spec.endpoint_or_dataset_id)
            if identity in identities or spec.spec_hash in hashes: raise ValueError("plan contains duplicate spec identity or hash")
            identities.add(identity); hashes.add(spec.spec_hash)
        _positive("caller_max_records", self.caller_max_records); _positive("caller_max_bytes", self.caller_max_bytes)
        if self.total_max_records != sum(item.max_records for item in self.specs) or self.total_max_bytes != sum(item.max_bytes for item in self.specs): raise ValueError("plan budgets do not match specs")
        if self.total_max_records > self.caller_max_records or self.total_max_bytes > self.caller_max_bytes: raise ValueError("plan exceeds caller budget")
        target, allowed = self.target_root.resolve(), self.allowed_root.resolve()
        try: target.relative_to(allowed)
        except ValueError as exc: raise ValueError("target_root escapes allowed_root") from exc
        if target == allowed: raise ValueError("target_root cannot overwrite an allowed/data root")
        if self.plan_hash != _hash({"spec_hashes": [item.spec_hash for item in self.specs], "target_root": str(target), "allowed_root": str(allowed), "max_records": self.total_max_records, "max_bytes": self.total_max_bytes, "caller_max_records": self.caller_max_records, "caller_max_bytes": self.caller_max_bytes, "status": self.status}): raise ValueError("plan_hash does not match plan")


def build_dry_run_plan(specs: Iterable[SourceAcquisitionSpec], *, target_root: str | Path, allowed_root: str | Path, max_records: int, max_bytes: int) -> AcquisitionPlan:
    root, allowed = Path(target_root).resolve(), Path(allowed_root).resolve()
    try: root.relative_to(allowed)
    except ValueError as exc: raise ValueError("target_root escapes allowed_root") from exc
    if root == allowed: raise ValueError("target_root cannot overwrite an allowed/data root")
    values = tuple(sorted(tuple(specs), key=lambda item: item.spec_hash))
    if not values: raise ValueError("plan cannot be empty")
    identities: set[tuple[DataClass, str, str]] = set()
    for spec in values:
        spec.verify()
        if not readiness(spec)[0]: raise ValueError(f"blocked acquisition spec: {readiness(spec)[1]}")
        identity = (spec.domain, spec.provider, spec.endpoint_or_dataset_id)
        if identity in identities: raise ValueError("duplicate domain/provider/dataset identity")
        identities.add(identity)
    _positive("max_records", max_records); _positive("max_bytes", max_bytes)
    records, bytes_ = sum(item.max_records for item in values), sum(item.max_bytes for item in values)
    if records > max_records or bytes_ > max_bytes: raise ValueError("plan exceeds caller budget")
    payload = {"spec_hashes": [item.spec_hash for item in values], "target_root": str(root), "allowed_root": str(allowed), "max_records": records, "max_bytes": bytes_, "caller_max_records": max_records, "caller_max_bytes": max_bytes, "status": "dry_run_only"}
    plan = AcquisitionPlan(values, root, allowed, records, bytes_, max_records, max_bytes, "dry_run_only", _hash(payload)); plan.verify(); return plan
