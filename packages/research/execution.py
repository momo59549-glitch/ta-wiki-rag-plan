"""Conservative, file-only A-share execution eligibility checks.

The research pipeline should call :func:`assess_execution` before treating a
forward return as tradeable.  This module deliberately *marks* an outcome; the
caller decides whether to skip it.  It never changes the source price files.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from math import isfinite
from typing import Any, Literal, Mapping, Optional


def _number(value: Any) -> Optional[float]:
    """Return a finite positive price-like value, or ``None``."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if isfinite(value) and value > 0 else None


def _day(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


@dataclass(frozen=True)
class ExecutionConfig:
    """Rules used for a conservative A-share approximation.

    ``limit_tolerance`` makes comparison robust to vendor rounding.  The
    default does not try to infer intraday queue position: a close at/near the
    daily limit is considered non-executable for that direction.
    """

    skip_untradeable: bool = True
    limit_tolerance: float = 0.001
    main_board_limit: float = 0.10
    st_limit: float = 0.05
    chinext_limit: float = 0.20
    star_limit: float = 0.20
    chinext_20pct_from: date = date(2020, 8, 24)


@dataclass(frozen=True)
class ExecutionAssessment:
    executable: bool
    reason_codes: tuple[str, ...] = ()
    limit_pct: Optional[float] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def should_skip(self) -> bool:
        return not self.executable


def limit_pct_for(symbol: str, trade_date: Any, *, is_st: bool = False,
                  config: ExecutionConfig = ExecutionConfig()) -> float:
    """Approximate the normal daily price limit for common A-share symbols."""
    if is_st:
        return config.st_limit
    code = str(symbol).split(".")[0].zfill(6)
    day = _day(trade_date)
    if code.startswith("688"):
        return config.star_limit
    if code.startswith(("300", "301")) and (
        day is None or day >= config.chinext_20pct_from
    ):
        return config.chinext_limit
    return config.main_board_limit


def assess_execution(
    bar: Mapping[str, Any],
    *,
    symbol: str,
    side: str = "buy",
    price_at: Literal["open", "close"] = "close",
    require_session_liquidity: bool = True,
    config: ExecutionConfig = ExecutionConfig(),
) -> ExecutionAssessment:
    """Assess whether a daily bar is usable as an execution price.

    ``price_at`` is an information boundary, not merely a pricing choice.
    For an order filled at the next open, use ``price_at="open"`` and
    ``require_session_liquidity=False``: that check must not inspect that
    day's close, volume, or amount.  For an end-of-day close fill, the full
    bar can be used.  This distinction prevents outcome filtering with data
    that was unavailable when an opening order was placed.
    """
    side = side.lower()
    if side not in {"buy", "sell"}:
        raise ValueError("side must be 'buy' or 'sell'")
    if price_at not in {"open", "close"}:
        raise ValueError("price_at 必须为 open 或 close")

    reasons: list[str] = []
    open_price = _number(bar.get("open"))
    close_price = _number(bar.get("close")) if price_at == "close" else None
    volume = _number(bar.get("volume", bar.get("vol")))
    amount = _number(bar.get("amount", bar.get("turnover")))
    if bool(bar.get("suspended", False)):
        reasons.append("suspended")
    execution_price = open_price if price_at == "open" else close_price
    if execution_price is None:
        reasons.append("missing_or_invalid_price")
    # Vendors may omit volume/amount.  Only an explicit zero is a suspension
    # signal; a missing field is handled by the price checks above.
    if require_session_liquidity and ("volume" in bar or "vol" in bar):
        if volume is None:
            reasons.append("zero_or_invalid_volume")
    if require_session_liquidity and ("amount" in bar or "turnover" in bar):
        if amount is None:
            reasons.append("zero_or_invalid_amount")

    prior = _number(bar.get("pre_close", bar.get("prev_close")))
    limit = limit_pct_for(symbol, bar.get("date"), is_st=bool(bar.get("is_st", False)), config=config)
    if prior is not None and execution_price is not None:
        change = execution_price / prior - 1
        at_up = change >= limit - config.limit_tolerance
        at_down = change <= -limit + config.limit_tolerance
        if side == "buy" and at_up:
            reasons.append("limit_up_buy_unavailable")
        if side == "sell" and at_down:
            reasons.append("limit_down_sell_unavailable")

    return ExecutionAssessment(
        executable=not reasons,
        reason_codes=tuple(reasons),
        limit_pct=limit,
        metadata={"side": side, "price_at": price_at, "execution_price": execution_price, "prior_close": prior},
    )
