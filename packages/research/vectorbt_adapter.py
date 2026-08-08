"""Candidate-portfolio verification through vectorbt.

The event-study pipeline remains the source of Observations and Outcomes.
This adapter is intentionally used only after a candidate survives evidence
gates, to verify portfolio accounting with a mature open-source engine.
"""
from __future__ import annotations

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
