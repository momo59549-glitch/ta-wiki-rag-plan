from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Observation:
    id: str
    symbol: str
    observed_at: datetime
    executable_at: datetime
    rule_id: str
    rule_version: str
    rule_semantic_hash: str
    dataset_snapshot_id: str
    conditions: tuple[dict, ...]


@dataclass(frozen=True, slots=True)
class Outcome:
    observation_id: str
    horizon_bars: int
    entry_at: datetime
    exit_at: datetime
    entry_price: float
    exit_price: float
    raw_return: float
    mfe: float
    mae: float
    benchmark_return: float | None = None
    excess_return: float | None = None
    net_return: float | None = None
    net_excess_return: float | None = None
    sample_split: str = "in_sample"
    market_regime: str = "unknown"
    signal_amount: float | None = None
    entry_executable: bool = True
    exit_executable: bool = True
    execution_reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResearchRun:
    run_id: str
    created_at: datetime
    dataset_snapshot_id: str
    rule_semantic_hash: str
    symbols_requested: int
    symbols_loaded: int
    observations: int
    outcomes: int
    skipped_symbols: tuple[str, ...]
