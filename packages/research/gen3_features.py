"""Pure feature identity and dependency contracts for Gen3.

No transforms, data access, candidate creation, ledger access, or file I/O is
implemented here.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Iterable, Mapping

from .gen3_policy import DataClass
from .gen3_providers import CANONICAL_FIELDS

FEATURE_SCHEMA_VERSION = "gen3-feature-draft/v1"
_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
_FAMILIES = frozenset({"technical", "single_factor", "announcement_event", "news", "control"})
_DTYPES = frozenset({"boolean", "float", "category"})
_NULLS = frozenset({"reject", "drop", "false"})
_AVAILABILITY = {
    DataClass.MARKET: "session", DataClass.TRADABILITY: "session",
    DataClass.FUNDAMENTALS: "effective_session", DataClass.ANNOUNCEMENTS: "effective_session",
    DataClass.NEWS: "effective_session", DataClass.INDEX_CONSTITUENTS: "effective_from",
}
_TECHNICAL = frozenset({DataClass.MARKET, DataClass.TRADABILITY, DataClass.INDEX_CONSTITUENTS})
_PIT_DOMAINS = frozenset({DataClass.FUNDAMENTALS, DataClass.ANNOUNCEMENTS, DataClass.NEWS})
_PIT_PROVENANCE = frozenset({"source_record_id", "effective_session", "content_hash", "revision_id"})


def _hash(payload: object) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{sha256(data).hexdigest()}"


def _identifier(name: str, value: object) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase identifier")


def _integer(name: str, value: object, minimum: int) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be a non-boolean integer >= {minimum}")


@dataclass(frozen=True)
class FeatureDependency:
    domain: DataClass
    mapping_hash: str
    required_fields: tuple[str, ...]
    availability_field: str
    lag_sessions: int = 0

    def validate(self) -> None:
        if not isinstance(self.domain, DataClass):
            raise ValueError("dependency domain must be a DataClass")
        if not isinstance(self.mapping_hash, str) or not _HASH.fullmatch(self.mapping_hash):
            raise ValueError("mapping_hash must match sha256:<64 lowercase hex>")
        if not isinstance(self.required_fields, tuple) or not self.required_fields:
            raise ValueError("required_fields must be a non-empty tuple")
        if self.required_fields != tuple(sorted(self.required_fields)) or len(set(self.required_fields)) != len(self.required_fields):
            raise ValueError("required_fields must be unique and sorted")
        if any(field not in CANONICAL_FIELDS[self.domain] for field in self.required_fields):
            raise ValueError("required_fields contains a field outside the domain canonical schema")
        if self.availability_field != _AVAILABILITY[self.domain]:
            raise ValueError("availability_field does not match the domain rule")
        if self.availability_field not in self.required_fields:
            raise ValueError("required_fields must include availability_field")
        if self.domain in _PIT_DOMAINS and not _PIT_PROVENANCE <= set(self.required_fields):
            raise ValueError("PIT dependencies must include provenance fields")
        _integer("lag_sessions", self.lag_sessions, 0)

    def canonical(self) -> dict[str, object]:
        return {"domain": self.domain.value, "mapping_hash": self.mapping_hash, "required_fields": list(self.required_fields), "availability_field": self.availability_field, "lag_sessions": self.lag_sessions}


@dataclass(frozen=True)
class FeatureSpec:
    feature_id: str
    version: str
    family: str
    dependencies: tuple[FeatureDependency, ...]
    lookback_sessions: int
    min_observations: int
    output_dtype: str
    null_policy: str
    transform_id: str
    transform_version: str
    feature_hash: str
    schema_version: str = FEATURE_SCHEMA_VERSION

    @property
    def identity(self) -> tuple[str, str]:
        return (self.feature_id, self.version)

    def canonical_payload(self) -> dict[str, object]:
        return {"schema_version": self.schema_version, "feature_id": self.feature_id, "version": self.version, "family": self.family, "dependencies": [dependency.canonical() for dependency in sorted(self.dependencies, key=lambda item: (item.domain.value, item.mapping_hash))], "lookback_sessions": self.lookback_sessions, "min_observations": self.min_observations, "output_dtype": self.output_dtype, "null_policy": self.null_policy, "evaluation_point": "signal_session_close", "transform_id": self.transform_id, "transform_version": self.transform_version}

    def computed_feature_hash(self) -> str:
        return _hash(self.canonical_payload())

    def validate(self) -> None:
        if self.schema_version != FEATURE_SCHEMA_VERSION:
            raise ValueError("schema_version must equal FEATURE_SCHEMA_VERSION")
        _identifier("feature_id", self.feature_id)
        _identifier("version", self.version)
        _identifier("transform_id", self.transform_id)
        _identifier("transform_version", self.transform_version)
        if self.family not in _FAMILIES:
            raise ValueError("family is not approved")
        if not isinstance(self.dependencies, tuple) or not self.dependencies:
            raise ValueError("dependencies must be a non-empty tuple")
        pairs: set[tuple[DataClass, str]] = set()
        for dependency in self.dependencies:
            if not isinstance(dependency, FeatureDependency):
                raise ValueError("dependencies must contain FeatureDependency values")
            dependency.validate()
            pair = (dependency.domain, dependency.mapping_hash)
            if pair in pairs:
                raise ValueError("domain and mapping_hash dependency combination must be unique")
            pairs.add(pair)
        _integer("lookback_sessions", self.lookback_sessions, 1)
        _integer("min_observations", self.min_observations, 1)
        if self.min_observations > self.lookback_sessions:
            raise ValueError("min_observations cannot exceed lookback_sessions")
        if self.output_dtype not in _DTYPES or self.null_policy not in _NULLS:
            raise ValueError("output_dtype or null_policy is not approved")
        domains = {dependency.domain for dependency in self.dependencies}
        if self.family in {"technical", "control"} and (not domains <= _TECHNICAL or not domains & {DataClass.MARKET, DataClass.TRADABILITY}):
            raise ValueError(f"{self.family} features require market or tradability and can only depend on market/tradability/index")
        if self.family == "single_factor" and DataClass.FUNDAMENTALS not in domains:
            raise ValueError("single_factor features must depend on fundamentals")
        if self.family == "single_factor" and not domains <= (_TECHNICAL | {DataClass.FUNDAMENTALS}):
            raise ValueError("single_factor features contain a forbidden domain")
        if self.family == "announcement_event" and (DataClass.ANNOUNCEMENTS not in domains or not domains <= (_TECHNICAL | {DataClass.ANNOUNCEMENTS})):
            raise ValueError("announcement_event features must contain announcements and no forbidden domain")
        if self.family == "news" and (DataClass.NEWS not in domains or not domains <= (_TECHNICAL | {DataClass.NEWS})):
            raise ValueError("news features must contain news and no forbidden domain")
        if not isinstance(self.feature_hash, str) or not _HASH.fullmatch(self.feature_hash):
            raise ValueError("feature_hash must match sha256:<64 lowercase hex>")

    def verify(self) -> None:
        self.validate()
        if self.feature_hash != self.computed_feature_hash():
            raise ValueError("feature_hash does not match canonical feature payload")

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> "FeatureSpec":
        required = {"schema_version", "feature_id", "version", "family", "dependencies", "lookback_sessions", "min_observations", "output_dtype", "null_policy", "transform_id", "transform_version", "feature_hash"}
        if set(values) != required:
            raise ValueError("FeatureSpec fields must match whitelist")
        result = cls(**values)  # type: ignore[arg-type]
        result.verify()
        return result


def make_feature_spec(*, feature_id: str, version: str, family: str, dependencies: tuple[FeatureDependency, ...], lookback_sessions: int, min_observations: int, output_dtype: str, null_policy: str, transform_id: str, transform_version: str, schema_version: str = FEATURE_SCHEMA_VERSION) -> FeatureSpec:
    provisional = FeatureSpec(feature_id, version, family, dependencies, lookback_sessions, min_observations, output_dtype, null_policy, transform_id, transform_version, "sha256:" + "0" * 64, schema_version)
    provisional.validate()
    result = FeatureSpec(**{**provisional.__dict__, "feature_hash": provisional.computed_feature_hash()})
    result.verify()
    return result


def assert_no_future_dependencies(spec: FeatureSpec) -> None:
    """Explicit safety hook; validation rejects every negative/future lag."""
    spec.verify()
    for dependency in spec.dependencies:
        if dependency.lag_sessions < 0 or dependency.availability_field not in dependency.required_fields:
            raise ValueError("feature contains a future or unavailable dependency")


@dataclass(frozen=True)
class FeatureRegistry:
    specs: tuple[FeatureSpec, ...]
    registry_hash: str

    def verify(self) -> None:
        if not self.specs:
            raise ValueError("registry cannot be empty")
        ordered = tuple(sorted(self.specs, key=lambda spec: (spec.feature_id, spec.version, spec.feature_hash)))
        if self.specs != ordered:
            raise ValueError("registry specs must be canonically sorted")
        identities: set[tuple[str, str]] = set()
        hashes: set[str] = set()
        for spec in self.specs:
            spec.verify()
            if spec.identity in identities:
                raise ValueError("registry contains duplicate feature identity")
            if spec.feature_hash in hashes:
                raise ValueError("registry contains duplicate feature_hash")
            identities.add(spec.identity)
            hashes.add(spec.feature_hash)
        expected = _hash({"schema_version": FEATURE_SCHEMA_VERSION, "feature_hashes": [spec.feature_hash for spec in self.specs]})
        if self.registry_hash != expected:
            raise ValueError("registry_hash does not match canonical registry payload")


def build_registry(specs: Iterable[FeatureSpec]) -> FeatureRegistry:
    seen_identities: dict[tuple[str, str], str] = {}
    hashes: set[str] = set()
    values: list[FeatureSpec] = []
    for spec in specs:
        spec.verify()
        prior = seen_identities.get(spec.identity)
        if prior is not None:
            if prior != spec.feature_hash:
                raise ValueError("conflicting feature identity")
            raise ValueError("duplicate feature identity")
        if spec.feature_hash in hashes:
            raise ValueError("duplicate feature_hash")
        seen_identities[spec.identity] = spec.feature_hash
        hashes.add(spec.feature_hash)
        values.append(spec)
    if not values:
        raise ValueError("registry cannot be empty")
    ordered = tuple(sorted(values, key=lambda spec: (spec.feature_id, spec.version, spec.feature_hash)))
    registry = FeatureRegistry(ordered, _hash({"schema_version": FEATURE_SCHEMA_VERSION, "feature_hashes": [spec.feature_hash for spec in ordered]}))
    registry.verify()
    return registry
