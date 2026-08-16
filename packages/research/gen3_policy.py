"""Non-binding schemas for the Gen3 multi-source discovery Phase 0.

This module deliberately contains no filesystem, market-data, trial-ledger, or
backtest I/O.  It describes a *draft* policy and the metadata an audit must
collect before a formal, immutable Gen3 contract can be proposed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from enum import StrEnum
from math import isfinite
from typing import Mapping


GEN3_POLICY_SCHEMA_VERSION = "gen3-policy-draft/v1"


class DataClass(StrEnum):
    """Required point-in-time data domains for Gen3 research."""

    MARKET = "market"
    FUNDAMENTALS = "fundamentals"
    ANNOUNCEMENTS = "announcements"
    NEWS = "news"
    INDEX_CONSTITUENTS = "index_constituents"
    TRADABILITY = "tradability"


class Availability(StrEnum):
    """Audit status, intentionally separate from a source's claimed coverage."""

    AVAILABLE = "available"
    PARTIAL = "partial"
    MISSING = "missing"
    UNVERIFIED = "unverified"


@dataclass(frozen=True)
class DataSourceAuditRecord:
    """Metadata-only evidence for one local source.

    ``observed_fields`` names fields actually observed in a small schema
    sample. It covers timestamps and provenance fields; no inference from a
    filename or report period is permitted.
    """

    source_id: str
    data_class: DataClass
    availability: Availability
    local_path: str | None = None
    file_format: str | None = None
    coverage_start: date | None = None
    coverage_end: date | None = None
    observed_fields: tuple[str, ...] = ()
    notes: str = ""

    def validate(self) -> None:
        if not self.source_id or self.source_id != self.source_id.strip():
            raise ValueError("source_id must be a non-empty trimmed string")
        if self.availability in (Availability.AVAILABLE, Availability.PARTIAL):
            if not self.local_path or not self.file_format:
                raise ValueError("available or partial sources need path and format")
            if self.coverage_start is None or self.coverage_end is None:
                raise ValueError("available or partial sources need coverage bounds")
            if self.coverage_end < self.coverage_start:
                raise ValueError("coverage_end must not precede coverage_start")

        observed = set(self.observed_fields)
        if self.data_class in (DataClass.ANNOUNCEMENTS, DataClass.NEWS, DataClass.FUNDAMENTALS):
            required = {"published_at", "available_at", "effective_session", "revision_id", "content_hash"}
            if self.availability == Availability.AVAILABLE and not required <= observed:
                raise ValueError(
                    f"{self.data_class} marked available lacks PIT fields: {required - observed}"
                )
        if self.data_class in (DataClass.MARKET, DataClass.TRADABILITY):
            if self.availability == Availability.AVAILABLE and "trading_day" not in observed:
                raise ValueError(f"{self.data_class} marked available needs trading_day")
        if self.data_class == DataClass.INDEX_CONSTITUENTS:
            if self.availability == Availability.AVAILABLE and "effective_session" not in observed:
                raise ValueError("index_constituents marked available need effective_session")


@dataclass(frozen=True)
class DataAvailabilityReport:
    """A metadata-only Phase 0 inventory; it cannot authorize a backtest."""

    records: tuple[DataSourceAuditRecord, ...]
    audit_scope: str = "metadata-only"

    def validate(self) -> None:
        if self.audit_scope != "metadata-only":
            raise ValueError("Phase 0 reports must remain metadata-only")
        ids = [record.source_id for record in self.records]
        if len(ids) != len(set(ids)):
            raise ValueError("source_id values must be unique")
        for record in self.records:
            record.validate()
        missing = set(DataClass) - {record.data_class for record in self.records}
        if missing:
            raise ValueError(f"Phase 0 report is missing required data domains: {sorted(missing)}")

    def by_class(self) -> Mapping[DataClass, tuple[DataSourceAuditRecord, ...]]:
        return {
            data_class: tuple(record for record in self.records if record.data_class == data_class)
            for data_class in DataClass
        }

    @classmethod
    def unverified_scaffold(cls) -> "DataAvailabilityReport":
        """Return all six domains without claiming that any data is available."""
        return cls(
            records=tuple(
                DataSourceAuditRecord(
                    source_id=f"unverified-{data_class.value}",
                    data_class=data_class,
                    availability=Availability.UNVERIFIED,
                )
                for data_class in DataClass
            )
        )


@dataclass(frozen=True)
class Gen3PolicyDraft:
    """Reviewable research policy, not a frozen contract or trial registration."""

    schema_version: str = GEN3_POLICY_SCHEMA_VERSION
    policy_id: str = "gen3_multi_source_discovery_draft"
    is_formal_contract: bool = False
    is_immutable: bool = False
    discovery_start: date = date(2015, 1, 5)
    discovery_end: date = date(2020, 12, 31)
    development_qualification_start: date = date(2021, 1, 1)
    development_qualification_end: date = date(2021, 12, 31)
    robustness_end: date = date(2026, 8, 31)
    forward_validation_start: date = date(2026, 9, 1)
    forward_validation_end: date = date(2027, 2, 28)
    lockbox_start: date = date(2027, 3, 1)
    minimum_lockbox_sessions: int = 60
    net_annual_return_minimum: float = 0.12
    annual_excess_return_minimum: float = 0.05
    stressed_cost_annual_return_minimum: float = 0.08
    maximum_drawdown_limit: float = 0.20
    calmar_minimum: float = 0.80
    profitable_fold_ratio_minimum: float = 0.70
    maximum_single_period_pnl_share: float = 0.50
    ledger_used_trials: int = 214
    ledger_remaining_trials: int = 42
    allocation: Mapping[str, int] = field(
        default_factory=lambda: {
            "technical": 8,
            "single_factor": 12,
            "announcement_event": 10,
            "news": 6,
            "reserve": 6,
        }
    )
    execution_contract_reference: str = (
        "Before formalization, copy the then-current approved execution terms verbatim; "
        "this draft does not redefine execution."
    )

    @property
    def candidate_trial_count(self) -> int:
        return sum(value for key, value in self.allocation.items() if key != "reserve")

    def validate(self) -> None:
        if self.is_formal_contract or self.is_immutable:
            raise ValueError("Gen3PolicyDraft must never represent a formal immutable contract")
        if self.schema_version != GEN3_POLICY_SCHEMA_VERSION:
            raise ValueError("unexpected Gen3 policy schema version")
        dates = (
            self.discovery_start,
            self.discovery_end,
            self.development_qualification_start,
            self.development_qualification_end,
            self.robustness_end,
            self.forward_validation_start,
            self.forward_validation_end,
            self.lockbox_start,
        )
        if any(later <= earlier for earlier, later in zip(dates, dates[1:])):
            raise ValueError("research, forward validation, and lockbox dates must be strictly ordered")
        if self.minimum_lockbox_sessions < 1:
            raise ValueError("minimum_lockbox_sessions must be positive")
        thresholds = (
            self.net_annual_return_minimum,
            self.annual_excess_return_minimum,
            self.stressed_cost_annual_return_minimum,
            self.maximum_drawdown_limit,
            self.calmar_minimum,
            self.profitable_fold_ratio_minimum,
            self.maximum_single_period_pnl_share,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value)
            for value in thresholds
        ):
            raise ValueError("thresholds must be finite non-boolean numbers")
        if any(value < 0 for value in thresholds):
            raise ValueError("thresholds must be non-negative")
        if self.profitable_fold_ratio_minimum > 1 or self.maximum_single_period_pnl_share > 1:
            raise ValueError("ratio thresholds must be at most one")
        counts = (self.ledger_used_trials, self.ledger_remaining_trials)
        if any(type(value) is not int or value < 0 for value in counts):
            raise ValueError("trial counts must be non-negative non-boolean integers")
        if counts != (214, 42):
            raise ValueError("this draft fixes the approved 214-used/42-remaining ledger state")
        if sum(counts) != 256:
            raise ValueError("trial budget must total 256")
        if set(self.allocation) != {
            "technical", "single_factor", "announcement_event", "news", "reserve"
        }:
            raise ValueError("allocation must have exactly the five approved buckets")
        if any(type(value) is not int or value < 0 for value in self.allocation.values()):
            raise ValueError("allocation values must be non-negative non-boolean integers")
        if sum(self.allocation.values()) != self.ledger_remaining_trials:
            raise ValueError("allocation must equal the verified remaining trial budget")
        if self.candidate_trial_count != 36:
            raise ValueError("candidate allocation must reserve exactly six trials")

    def as_dict(self) -> dict[str, object]:
        """Return review-friendly primitive values without writing or freezing anything."""

        value = asdict(self)
        for key, item in value.items():
            if isinstance(item, date):
                value[key] = item.isoformat()
        return value


def default_gen3_policy_draft() -> Gen3PolicyDraft:
    """Return the proposed policy only after checking its internal consistency."""

    draft = Gen3PolicyDraft()
    draft.validate()
    return draft
