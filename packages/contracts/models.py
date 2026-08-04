from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class Candle:
    """统一 CandleSeries 的最小输入；时间表示该 bar 收盘时刻。"""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    amount: float | None = None
    prev_close: float | None = None
    is_st: bool = False
    available_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("OHLC 不满足 low <= open/close <= high")
        if self.available_at and self.available_at < self.timestamp:
            raise ValueError("available_at 不能早于 bar 收盘时刻")


@dataclass(frozen=True, slots=True)
class RuleDefinition:
    id: str
    version: str
    name_zh: str
    expression: dict[str, Any]
    parameters: dict[str, float] = field(default_factory=dict)
    warmup_bars: int = 0
    observed_at: Literal["bar_close"] = "bar_close"
    executable_from: Literal["next_bar_open"] = "next_bar_open"


@dataclass(frozen=True, slots=True)
class ConditionResult:
    path: str
    operator: str
    actual: float | bool | None
    threshold: float | bool | None
    passed: bool | None
    label: str


@dataclass(frozen=True, slots=True)
class RuleEvaluation:
    matched: bool
    status: Literal["matched", "not_matched", "insufficient_data", "data_error"]
    observed_at: datetime | None
    executable_from: datetime | None
    conditions: tuple[ConditionResult, ...]
    semantic_hash: str
    warnings: tuple[str, ...] = ()
