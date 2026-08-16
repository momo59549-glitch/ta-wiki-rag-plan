"""Candidate-portfolio verification through vectorbt.

The event-study pipeline remains the source of Observations and Outcomes.
This adapter is intentionally used only after a candidate survives evidence
gates, to verify portfolio accounting with a mature open-source engine.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math
import pandas as pd


def verify_fixed_horizon_portfolio(
    *,
    opens: pd.Series,
    closes: pd.Series,
    signal_at_close: pd.Series,
    horizon_bars: int,
    fees: float = 0.0003,
    slippage: float = 0.0005,
):
    """Build a long-only candidate portfolio without same-bar execution.

    A signal observed at T close becomes an entry at T+1 open.  The exit is
    held for ``horizon_bars`` trading bars and uses that bar's close.  The
    returned vectorbt Portfolio exposes orders, trades and standard metrics.
    """
    if horizon_bars < 2:
        raise ValueError("vectorbt 候选复核目前要求 horizon_bars>=2；同日开盘到收盘需单独使用日内订单模型")
    if not opens.index.equals(closes.index) or not opens.index.equals(signal_at_close.index):
        raise ValueError("opens、closes、signal_at_close 必须使用同一时间索引")
    try:
        import vectorbt as vbt
    except ImportError as exc:  # pragma: no cover - exercised in deployment
        raise RuntimeError("缺少研究依赖 vectorbt；请安装项目的 research extra") from exc

    entries = signal_at_close.fillna(False).astype(bool).shift(1, fill_value=False)
    exits = entries.shift(horizon_bars - 1, fill_value=False)
    # Vectorbt accepts a per-bar fill price.  Entry bars receive the opening
    # price; non-entry/exit marks use close.  There is no T-close/T-close fill.
    prices = closes.astype(float).copy()
    prices.loc[entries] = opens.loc[entries].astype(float)
    return vbt.Portfolio.from_signals(
        close=closes.astype(float), entries=entries, exits=exits,
        price=prices, fees=fees, slippage=slippage, direction="longonly",
    )


SPIKE_START = date(2019, 1, 1)
SPIKE_END = date(2021, 12, 31)
SPIKE_SYMBOL_COUNT = 20
SPIKE_INITIAL_CASH = 100_000.0
SPIKE_ORDER_VALUE = 5_000.0


@dataclass(frozen=True)
class VectorbtSpikeMetric:
    """One fixed demonstration result, not a candidate score."""
    strategy: str
    orders: int
    trades: int
    total_return: float
    max_drawdown: float
    engine: str
    cash_sharing: bool = True
    nonadjudicable: bool = True


def fixed_close_signals(close: pd.DataFrame) -> dict[str, tuple[pd.DataFrame, pd.DataFrame]]:
    """Return three pre-committed close-time signal families.

    These are deliberately few and parameter-fixed for an engine smoke test;
    callers must not rank or select them from their outputs.
    """
    if not isinstance(close, pd.DataFrame) or close.empty or not isinstance(close.index, pd.DatetimeIndex) or not close.index.is_unique or not close.index.is_monotonic_increasing:
        raise ValueError("close must be a non-empty datetime-indexed wide frame")
    if close.columns.duplicated().any() or close.isna().all().any():
        raise ValueError("close columns must be unique and non-empty")
    if not close.apply(lambda x: pd.api.types.is_numeric_dtype(x)).all() or not close.apply(lambda x: x.gt(0).all()).all() or not close.apply(lambda x: x.map(lambda v: math.isfinite(float(v))).all()).all():
        raise ValueError("close must be finite positive numeric data")
    fast, slow = close.rolling(10, min_periods=10).mean(), close.rolling(30, min_periods=30).mean()
    ma_entries = (fast > slow) & (fast.shift(1) <= slow.shift(1))
    ma_exits = (fast < slow) & (fast.shift(1) >= slow.shift(1))
    delta = close.diff(); gain = delta.clip(lower=0).rolling(14, min_periods=14).mean(); loss = (-delta.clip(upper=0)).rolling(14, min_periods=14).mean()
    rsi = 100 - 100 / (1 + gain / loss.replace(0, float("nan")))
    rsi_entries, rsi_exits = rsi < 30, rsi > 55
    momentum = close.pct_change(20)
    mom_entries, mom_exits = momentum > 0, momentum < 0
    output = {
        "sma_10_30_cross": (ma_entries.fillna(False), ma_exits.fillna(False)),
        "rsi_14_30_55": (rsi_entries.fillna(False), rsi_exits.fillna(False)),
        "momentum_20_sign": (mom_entries.fillna(False), mom_exits.fillna(False)),
    }
    if any((entries & exits).any().any() for entries, exits in output.values()):
        raise ValueError("fixed close signals may not enter and exit together")
    return output


def _fixed_shared_portfolio(*, vbt, opens: pd.DataFrame, closes: pd.DataFrame, entries: pd.DataFrame, exits: pd.DataFrame, fees: float, slippage: float):
    """Delegate execution/accounting to VectorBT with frozen spike sizing."""
    next_open_entries, next_open_exits = entries.shift(1, fill_value=False), exits.shift(1, fill_value=False)
    if (next_open_entries & next_open_exits).any().any():
        raise ValueError("shifted signals may not enter and exit together")
    price=closes.astype(float).copy(); execute_at_open=next_open_entries | next_open_exits
    price[execute_at_open]=opens.astype(float)[execute_at_open]
    return vbt.Portfolio.from_signals(
        close=closes.astype(float), entries=next_open_entries, exits=next_open_exits, price=price,
        fees=float(fees), slippage=float(slippage), direction="longonly", init_cash=SPIKE_INITIAL_CASH,
        size=SPIKE_ORDER_VALUE, size_type="value", cash_sharing=True, group_by=True, call_seq="auto", freq="1D",
    )


def run_fixed_wide_spike(*, opens: pd.DataFrame, closes: pd.DataFrame, fees: float = 0.0003, slippage: float = 0.0005) -> tuple[VectorbtSpikeMetric, ...]:
    """Actually exercise vectorbt on fixed close signals with T+1-open fills.

    Shared cash is explicitly enabled.  This engine cannot adjudicate A-share
    halts or price limits; callers must pre-filter those separately.
    """
    if not opens.index.equals(closes.index) or not opens.columns.equals(closes.columns) or not opens.index.is_unique or not opens.index.is_monotonic_increasing:
        raise ValueError("open/close wide frames must use the same axes")
    for frame,name in ((opens,"opens"),(closes,"closes")):
        if frame.columns.duplicated().any() or not frame.apply(lambda x: pd.api.types.is_numeric_dtype(x)).all() or not frame.apply(lambda x: x.map(lambda v: math.isfinite(float(v)) and float(v)>0).all()).all():
            raise ValueError(name+" must be finite positive numeric data")
    if type(fees) not in (int, float) or type(slippage) not in (int, float) or not all(math.isfinite(float(x)) and x >= 0 for x in (fees, slippage)):
        raise ValueError("fees/slippage must be finite non-negative values")
    signals = fixed_close_signals(closes)
    try:
        import vectorbt as vbt
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("vectorbt is required for the spike") from exc
    metrics=[]
    for name, (at_close_entries, at_close_exits) in signals.items():
        portfolio=_fixed_shared_portfolio(vbt=vbt,opens=opens,closes=closes,entries=at_close_entries,exits=at_close_exits,fees=float(fees),slippage=float(slippage))
        result=VectorbtSpikeMetric(
            name, int(len(portfolio.orders.records)), int(len(portfolio.trades.records)),
            float(portfolio.total_return(group_by=True)), float(portfolio.max_drawdown(group_by=True)),
            "vectorbt." + str(getattr(vbt, "__version__", "unknown")),
        )
        if not all(math.isfinite(x) for x in (result.total_return, result.max_drawdown)):
            raise ValueError("vectorbt returned non-finite spike metric")
        metrics.append(result)
    return tuple(metrics)
