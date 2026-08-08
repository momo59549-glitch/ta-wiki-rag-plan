"""Independent fixed-horizon candidate verification through Backtrader."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True, slots=True)
class BacktraderVerification:
    start_value: float
    end_value: float
    total_return: float
    closed_trades: int
    engine: str = "backtrader.Cerebro"


def verify_fixed_horizon_candidate(
    frame: pd.DataFrame, signal_at_close: pd.Series, *, horizon_bars: int,
    initial_cash: float = 100_000.0, commission: float = 0.0003,
) -> BacktraderVerification:
    """Verify a long-only candidate using Backtrader's event loop.

    ``next_open`` sees only the preceding completed bar's signal and submits
    the entry at the current open.  It intentionally validates only
    ``horizon_bars >= 2``; same-day open-to-close exits need intraday data.
    """
    if horizon_bars < 2:
        raise ValueError("Backtrader 复核要求 horizon_bars>=2")
    required = {"open", "high", "low", "close", "volume"}
    if missing := required - set(frame.columns):
        raise ValueError(f"缺少 Backtrader 行情列: {sorted(missing)}")
    if not frame.index.equals(signal_at_close.index):
        raise ValueError("frame 与 signal_at_close 必须使用同一时间索引")
    try:
        import backtrader as bt
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("缺少研究依赖 backtrader；请安装项目的 research extra") from exc

    signal_by_date = {timestamp.date(): bool(value) for timestamp, value in signal_at_close.fillna(False).items()}
    class FixedHorizonStrategy(bt.Strategy):
        params = (("horizon_bars", horizon_bars),)
        def __init__(self):
            self.entry_bar: int | None = None
            self.closed_trades = 0
        def next_open(self):
            previous_date = self.data.datetime.date(-1)
            if not self.position and signal_by_date.get(previous_date, False):
                self.buy()
        def notify_order(self, order):
            if order.status == order.Completed and order.isbuy():
                self.entry_bar = len(self)
        def next(self):
            if self.position and self.entry_bar is not None and len(self) - self.entry_bar >= self.p.horizon_bars - 1:
                self.close()
        def notify_trade(self, trade):
            if trade.isclosed:
                self.closed_trades += 1

    data = bt.feeds.PandasData(dataname=frame.loc[:, ["open", "high", "low", "close", "volume"]].copy())
    cerebro = bt.Cerebro(cheat_on_open=True, stdstats=False)
    cerebro.adddata(data)
    cerebro.addstrategy(FixedHorizonStrategy)
    cerebro.broker.setcash(initial_cash)
    cerebro.broker.setcommission(commission=commission)
    cerebro.broker.set_coo(True)
    cerebro.broker.set_coc(True)
    result = cerebro.run()[0]
    end_value = float(cerebro.broker.getvalue())
    return BacktraderVerification(initial_cash, end_value, end_value / initial_cash - 1, result.closed_trades)
