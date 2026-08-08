"""Vectorized indicator columns shared by the audited pipeline and search screen.

Indicator values are computed with pandas so the engine's precomputed path and
the vectorized search evaluator read exactly the same numbers.  The engine also
contains a pure-Python fallback for direct calls without a column cache.
"""
from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd

from packages.contracts import Candle
from packages.rule_dsl.compiler import MACD_FAST, MACD_SIGNAL, MACD_SLOW, indicator_key


def candles_to_frame(candles: list[Candle]) -> pd.DataFrame:
    """Convert domain candles into a positional DataFrame of raw columns."""
    return pd.DataFrame(
        [
            {
                "open": item.open,
                "high": item.high,
                "low": item.low,
                "close": item.close,
                "volume": item.volume,
                "amount": item.amount,
                "prev_close": item.prev_close,
                "is_st": bool(item.is_st),
            }
            for item in candles
        ]
    )


def _rsi(close: pd.Series, window: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    average_gain = gain.ewm(alpha=1.0 / window, adjust=False).mean()
    average_loss = loss.ewm(alpha=1.0 / window, adjust=False).mean()
    relative_strength = average_gain / average_loss.replace(0.0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + relative_strength)
    return rsi.where(average_loss > 0.0, np.where(average_gain > 0.0, 100.0, 50.0))


def compute_indicators(frame: pd.DataFrame, needs: Iterable[str]) -> dict[str, pd.Series]:
    """Compute only the requested canonical indicator columns."""
    close = frame["close"].astype(float)
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    volume = frame["volume"].astype(float) if "volume" in frame.columns else None
    columns: dict[str, pd.Series] = {}
    down = (close < close.shift(1)).astype(float)
    up = (close > close.shift(1)).astype(float)
    dif = ema = None
    for key in sorted(set(needs)):
        parts = key.split(":")
        name = parts[0]
        if name in {"lower_close_count", "higher_close_count"}:
            window = int(parts[1])
            source = down if name == "lower_close_count" else up
            columns[key] = source.rolling(window - 1).sum().shift(1)
        elif name in {"sma", "ema", "rsi", "roc", "max_high", "min_low"}:
            window = int(parts[1])
            if name == "sma":
                columns[key] = close.rolling(window).mean()
            elif name == "ema":
                columns[key] = close.ewm(span=window, adjust=False).mean()
            elif name == "rsi":
                columns[key] = _rsi(close, window)
            elif name == "roc":
                columns[key] = close / close.shift(window) - 1.0
            elif name == "max_high":
                columns[key] = high.rolling(window).max()
            else:
                columns[key] = low.rolling(window).min()
        elif name in {"boll_upper", "boll_lower"}:
            window = int(parts[1])
            average = close.rolling(window).mean()
            stddev = close.rolling(window).std(ddof=0)
            columns[key] = average + 2.0 * stddev if name == "boll_upper" else average - 2.0 * stddev
        elif name in {"macd_dif", "macd_dea", "macd_hist"}:
            if dif is None:
                ema_fast = close.ewm(span=MACD_FAST, adjust=False).mean()
                ema_slow = close.ewm(span=MACD_SLOW, adjust=False).mean()
                dif = ema_fast - ema_slow
                ema = dif.ewm(span=MACD_SIGNAL, adjust=False).mean()
            if name == "macd_dif":
                columns[key] = dif
            elif name == "macd_dea":
                columns[key] = ema
            else:
                columns[key] = dif - ema
        elif name == "volume_ratio" and volume is not None:
            window = int(parts[1])
            columns[key] = volume / volume.rolling(window).mean()
        else:
            raise ValueError(f"不支持的指标列: {key}")
    return columns


def required_indicator_keys(names: Iterable[tuple[str, int | None]]) -> set[str]:
    """Expand (name, window) pairs to canonical keys."""
    return {indicator_key(name, window) for name, window in names}
