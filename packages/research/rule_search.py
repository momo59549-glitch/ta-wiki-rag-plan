"""Bounded, auditable automatic rule search for the frozen-campaign pipeline.

The search screen is deliberately cheap: it evaluates every preregistered
candidate once on a held-out validation window with the same costs, benchmark
and tradeability rules as the audited pipeline, applies FDR-BH across *all*
candidates, and writes a trial ledger.  It never consumes the final lockbox,
never publishes, and never re-tests a candidate after peeking at its result.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
from statistics import NormalDist
from typing import Any, Iterable

import numpy as np
import pandas as pd

from packages.contracts import RuleDefinition
from packages.market_data import LocalParquetMarketData, active_on, load_universe_memberships
from packages.research.indicators import candles_to_frame, compute_indicators
from packages.research.json_store import write_json
from packages.research.statistics import summarize_outcomes
from packages.rule_dsl import compile_rule, indicator_key
from packages.rule_dsl.compiler import WINDOWED_METRICS


BASE_METRICS = frozenset({"open", "high", "low", "close", "body", "range", "upper_shadow", "lower_shadow", "is_bullish", "is_bearish"})
NOT_INVESTMENT_ADVICE = "搜索筛选仅用于研究证据评估，不构成投资建议、交易信号或规则发布审批。"


@dataclass(frozen=True, slots=True)
class SearchConfig:
    horizons: tuple[int, ...] = (1, 3, 5, 10, 20)
    start: date | None = None
    end: date | None = None
    out_of_sample_start: date | None = None
    lockbox_start: date | None = None
    benchmark_symbol: str = "000001"
    benchmark_dataset: str = "etf_cache"
    commission_bps_per_side: float = 3.0
    slippage_bps_per_side: float = 5.0
    market_regime_window: int = 60
    min_signal_amount: float | None = None
    skip_untradeable: bool = True
    min_out_of_sample_observations: int = 300
    limit_tolerance: float = 0.001

    def __post_init__(self) -> None:
        if not self.horizons or any(item < 1 for item in self.horizons):
            raise ValueError("horizons 必须是正整数")
        if self.end and self.lockbox_start and self.end >= self.lockbox_start:
            raise ValueError("end 必须早于 lockbox_start，禁止读取最终锁箱")
        if self.start and self.out_of_sample_start and self.start >= self.out_of_sample_start:
            raise ValueError("验证集必须晚于研究起始日期")
        if self.out_of_sample_start and self.end and self.out_of_sample_start > self.end:
            raise ValueError("验证集起始日期不能晚于研究结束日期")
        if self.commission_bps_per_side < 0 or self.slippage_bps_per_side < 0:
            raise ValueError("交易成本不能为负")
        if self.market_regime_window < 2:
            raise ValueError("市场状态窗口至少为 2")


# ---------------------------------------------------------------------------
# Vectorized DSL evaluator (mirrors packages.rule_engine semantics)
# ---------------------------------------------------------------------------

def vectorized_evaluate(expression: dict[str, Any], columns: dict[str, pd.Series], parameters: dict[str, float]) -> pd.Series:
    """Evaluate one DSL expression over per-symbol columns (aligned indexes)."""
    def value(node: Any) -> Any:
        if isinstance(node, (int, float, bool)):
            return node
        op, payload = next(iter(node.items()))
        if op == "param":
            return float(parameters[payload])
        if op == "metric":
            name = payload["name"]
            offset = int(payload["offset"])
            if name in BASE_METRICS:
                key = name
            else:
                key = indicator_key(name, payload.get("window"))
            column = columns[key]
            return column.shift(-offset) if offset else column
        if op == "context":
            key = indicator_key(payload["name"], payload["window"])
            return columns[key] >= payload["min_count"]
        children = [value(child) for child in (payload if isinstance(payload, list) else [payload])]
        if op == "all":
            return _reduce_all(children)
        if op == "any":
            return _reduce_any(children)
        if op == "not":
            return ~children[0] if not isinstance(children[0], (int, float, bool)) else not children[0]
        if op == "add":
            return children[0] + children[1]
        if op == "sub":
            return children[0] - children[1]
        if op == "mul":
            return children[0] * children[1]
        if op == "div":
            return children[0] / children[1]
        if op == "safe_div":
            divisor = children[1]
            return children[0].div(divisor).where(divisor != 0, 0.0) if isinstance(children[0], pd.Series) else (children[0] / divisor if divisor != 0 else 0.0)
        if op == "abs":
            return np.abs(children[0])
        if op in {"min", "max"}:
            reducer = np.minimum if op == "min" else np.maximum
            result = children[0]
            for item in children[1:]:
                result = reducer(result, item)
            return result
        if op == "gt":
            return children[0] > children[1]
        if op == "gte":
            return children[0] >= children[1]
        if op == "lt":
            return children[0] < children[1]
        if op == "lte":
            return children[0] <= children[1]
        if op == "eq":
            return children[0] == children[1]
        raise ValueError(f"未知操作: {op}")

    result = value(expression)
    if isinstance(result, np.ndarray):
        result = pd.Series(result, index=next(iter(columns.values())).index)
    if isinstance(result, pd.Series):
        return result.astype(bool)
    return pd.Series(result, index=next(iter(columns.values())).index).astype(bool)


def _reduce_all(children: list[Any]) -> Any:
    if all(isinstance(item, (int, float, bool)) for item in children):
        return bool(all(children))
    result = pd.Series(True, index=next(iter(columns_index(children))))
    for item in children:
        result &= item if isinstance(item, pd.Series) else bool(item)
    return result.astype(bool)


def _reduce_any(children: list[Any]) -> Any:
    if all(isinstance(item, (int, float, bool)) for item in children):
        return bool(any(children))
    result = pd.Series(False, index=next(iter(columns_index(children))))
    for item in children:
        result |= item if isinstance(item, pd.Series) else bool(item)
    return result.astype(bool)


def columns_index(children: list[Any]) -> Iterable[pd.Index]:
    for item in children:
        if isinstance(item, pd.Series):
            yield item.index


# ---------------------------------------------------------------------------
# Bounded search space: candle shapes + indicator families
# ---------------------------------------------------------------------------

def _m(name: str, offset: int = 0, window: int | None = None) -> dict[str, Any]:
    node: dict[str, Any] = {"metric": {"name": name, "offset": offset}}
    if window is not None:
        node["metric"]["window"] = window
    return node


def _p(name: str) -> dict[str, Any]:
    return {"param": name}


def _ctx(name: str, window: int, min_count: int) -> dict[str, Any]:
    return {"context": {"name": name, "window": window, "min_count": min_count}}


def _all(*children: dict[str, Any]) -> dict[str, Any]:
    return {"all": list(children)}


def _gte(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return {"gte": [left, right]}


def _lte(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return {"lte": [left, right]}


def _gt(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return {"gt": [left, right]}


def _lt(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return {"lt": [left, right]}


def _safe_div(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return {"safe_div": [left, right]}


def _mul(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return {"mul": [left, right]}


def _max(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return {"max": [left, right]}


def _ratio(name: str, denominator: str) -> dict[str, Any]:
    return _safe_div(_m(name), _max(_m(denominator), 0.01))


def _hammer_expression(parameters: dict[str, float], context: tuple[str, int, int] | None) -> dict[str, Any]:
    children = [
        _gte(_ratio("lower_shadow", "body"), _p("min_lower_shadow_body")),
        _lte(_ratio("upper_shadow", "range"), _p("max_upper_shadow_range")),
    ]
    if context:
        children.append(_ctx(*context))
    return _all(*children)


def _shooting_star_expression(parameters: dict[str, float], context: tuple[str, int, int] | None) -> dict[str, Any]:
    children = [
        _gte(_ratio("upper_shadow", "body"), _p("min_upper_shadow_body")),
        _lte(_ratio("lower_shadow", "range"), _p("max_lower_shadow_range")),
    ]
    if context:
        children.append(_ctx(*context))
    return _all(*children)


def _engulfing_expression(direction: str, min_body_ratio: float, context: bool) -> dict[str, Any]:
    if direction == "bullish":
        children = [
            _m("is_bullish"),
            _m("is_bearish", -1),
            _gt(_m("body"), _mul(_m("body", -1), min_body_ratio)),
            _gt(_m("close"), _m("open", -1)),
        ]
        if context:
            children.append(_ctx("lower_close_count", 5, 3))
    else:
        children = [
            _m("is_bearish"),
            _m("is_bullish", -1),
            _gt(_m("body"), _mul(_m("body", -1), min_body_ratio)),
            _lt(_m("close"), _m("open", -1)),
        ]
        if context:
            children.append(_ctx("higher_close_count", 5, 3))
    return _all(*children)


def _build_search_space() -> list[RuleDefinition]:
    candidates: list[RuleDefinition] = []

    def add(family: str, parameters: dict[str, float], expression: dict[str, Any]) -> None:
        index = len(candidates) + 1
        candidates.append(
            RuleDefinition(
                id=family,
                version=f"search.{index:04d}",
                name_zh=f"{family}（自动搜索）",
                expression=expression,
                parameters=parameters,
            )
        )

    hammer_contexts: list[tuple[str, int, int] | None] = [None, ("lower_close_count", 5, 3)]
    for min_shadow in (1.5, 2.0, 2.5, 3.0):
        for max_upper in (0.10, 0.15, 0.20, 0.30):
            for context in hammer_contexts:
                add(
                    "hammer", {"min_lower_shadow_body": min_shadow, "max_upper_shadow_range": max_upper},
                    _hammer_expression({"min_lower_shadow_body": min_shadow, "max_upper_shadow_range": max_upper}, context),
                )
    star_contexts: list[tuple[str, int, int] | None] = [None, ("higher_close_count", 5, 3)]
    for min_shadow in (1.5, 2.0, 2.5, 3.0):
        for max_lower in (0.10, 0.15, 0.20, 0.30):
            for context in star_contexts:
                add(
                    "shooting_star", {"min_upper_shadow_body": min_shadow, "max_lower_shadow_range": max_lower},
                    _shooting_star_expression({"min_upper_shadow_body": min_shadow, "max_lower_shadow_range": max_lower}, context),
                )
    doji_contexts: list[tuple[str, int, int] | None] = [None, ("lower_close_count", 5, 3), ("higher_close_count", 5, 3)]
    for max_body_range in (0.05, 0.10, 0.15, 0.20):
        for context in doji_contexts:
            children = [_lte(_ratio("body", "range"), _p("max_body_range"))]
            if context:
                children.append(_ctx(*context))
            add("doji", {"max_body_range": max_body_range}, _all(*children))
    for max_shadow in (0.03, 0.05, 0.10):
        for bias in (None, "bullish", "bearish"):
            children = [
                _lte(_ratio("upper_shadow", "range"), _p("max_shadow_range")),
                _lte(_ratio("lower_shadow", "range"), _p("max_shadow_range")),
            ]
            if bias == "bullish":
                children.append(_m("is_bullish"))
            elif bias == "bearish":
                children.append(_m("is_bearish"))
            add("marubozu", {"max_shadow_range": max_shadow}, _all(*children))
    for direction in ("bullish", "bearish"):
        for ratio in (1.0, 1.5, 2.0):
            for context in (False, True):
                add("engulfing", {"min_body_ratio": ratio}, _engulfing_expression(direction, ratio, context))
    for direction in ("up", "down"):
        for require_body in (False, True):
            children = [_gt(_m("open"), _m("high", -1)) if direction == "up" else _lt(_m("open"), _m("low", -1))]
            if require_body:
                children.append(_m("is_bullish") if direction == "up" else _m("is_bearish"))
            add("gap", {}, _all(*children))
    add("inside_bar", {}, _all(_lte(_m("high"), _m("high", -1)), _gte(_m("low"), _m("low", -1))))
    add("outside_bar", {}, _all(_gt(_m("close"), _m("high", -1)), _lt(_m("low"), _m("low", -1))))
    for fast, slow in ((5, 20), (5, 60), (5, 120), (10, 20), (10, 60), (10, 120), (20, 60), (20, 120)):
        for direction in ("up", "down"):
            if direction == "up":
                expression = _all(_gt(_m("sma", 0, fast), _m("sma", 0, slow)), _lte(_m("sma", -1, fast), _m("sma", -1, slow)))
            else:
                expression = _all(_lt(_m("sma", 0, fast), _m("sma", 0, slow)), _gte(_m("sma", -1, fast), _m("sma", -1, slow)))
            add("ma_cross", {"fast": float(fast), "slow": float(slow)}, expression)
    for window in (10, 20, 60):
        for above in (True, False):
            expression = _gt(_m("close"), _m("sma", 0, window)) if above else _lt(_m("close"), _m("sma", 0, window))
            add("price_vs_ma", {"window": float(window)}, expression)
    for window in (10, 20, 60):
        for rising in (True, False):
            expression = _gt(_m("sma", 0, window), _m("sma", -5, window)) if rising else _lt(_m("sma", 0, window), _m("sma", -5, window))
            add("ma_slope", {"window": float(window)}, expression)
    for direction in ("up", "down"):
        if direction == "up":
            expression = _all(_gt(_m("macd_dif", 0, 26), _m("macd_dea", 0, 26)), _lte(_m("macd_dif", -1, 26), _m("macd_dea", -1, 26)))
        else:
            expression = _all(_lt(_m("macd_dif", 0, 26), _m("macd_dea", 0, 26)), _gte(_m("macd_dif", -1, 26), _m("macd_dea", -1, 26)))
        add("macd_cross", {}, expression)
    for condition in ("positive", "negative", "cross_up", "cross_down"):
        if condition == "positive":
            expression = _gt(_m("macd_hist", 0, 26), 0)
        elif condition == "negative":
            expression = _lt(_m("macd_hist", 0, 26), 0)
        elif condition == "cross_up":
            expression = _all(_gt(_m("macd_hist", 0, 26), 0), _lte(_m("macd_hist", -1, 26), 0))
        else:
            expression = _all(_lt(_m("macd_hist", 0, 26), 0), _gte(_m("macd_hist", -1, 26), 0))
        add("macd_hist", {}, expression)
    add("rsi_level", {"threshold": 30.0}, _lt(_m("rsi", 0, 14), _p("threshold")))
    add("rsi_level", {"threshold": 70.0}, _gt(_m("rsi", 0, 14), _p("threshold")))
    add("rsi_cross", {"threshold": 30.0}, _all(_gte(_m("rsi", 0, 14), _p("threshold")), _lt(_m("rsi", -1, 14), _p("threshold"))))
    add("rsi_cross", {"threshold": 70.0}, _all(_lte(_m("rsi", 0, 14), _p("threshold")), _gt(_m("rsi", -1, 14), _p("threshold"))))
    add("bollinger", {}, _lt(_m("close"), _m("boll_lower", 0, 20)))
    add("bollinger", {}, _gt(_m("close"), _m("boll_upper", 0, 20)))
    for window in (5, 10, 20):
        for direction in ("up", "down"):
            for threshold in (0.03, 0.05, 0.08):
                expression = _gt(_m("roc", 0, window), _p("threshold")) if direction == "up" else _lt(_m("roc", 0, window), _p("threshold"))
                add("roc_threshold", {"window": float(window), "threshold": threshold}, expression)
    for window in (20, 60):
        add("breakout", {"window": float(window)}, _gt(_m("close"), _m("max_high", -1, window)))
        add("breakout", {"window": float(window)}, _lt(_m("close"), _m("min_low", -1, window)))
    for k in (1.5, 2.0, 3.0):
        for direction in ("any", "up", "down"):
            children = [_gt(_m("volume_ratio", 0, 20), _p("k"))]
            if direction == "up":
                children.append(_m("is_bullish"))
            elif direction == "down":
                children.append(_m("is_bearish"))
            add("volume_surge", {"k": k}, _all(*children))
    add(
        "combo_hammer_trend",
        {"min_lower_shadow_body": 2.0, "max_upper_shadow_range": 0.15},
        _all(_hammer_expression({"min_lower_shadow_body": 2.0, "max_upper_shadow_range": 0.15}, ("lower_close_count", 5, 3)), _gt(_m("close"), _m("sma", 0, 60))),
    )
    add(
        "combo_macd_trend",
        {},
        _all(_gt(_m("macd_dif", 0, 26), _m("macd_dea", 0, 26)), _lte(_m("macd_dif", -1, 26), _m("macd_dea", -1, 26)), _gt(_m("close"), _m("sma", 0, 60))),
    )
    add(
        "combo_cross_trend",
        {},
        _all(_gt(_m("sma", 0, 10), _m("sma", 0, 60)), _lte(_m("sma", -1, 10), _m("sma", -1, 60)), _gt(_m("close"), _m("sma", 0, 60))),
    )
    add(
        "combo_rsi_meanrev",
        {},
        _all(_lt(_m("rsi", 0, 14), 30.0), _lt(_m("close"), _m("sma", 0, 20))),
    )
    return candidates


def build_search_space() -> list[RuleDefinition]:
    definitions = _build_search_space()
    hashes = [compile_rule(item).semantic_hash for item in definitions]
    if len(set(hashes)) != len(hashes):
        raise ValueError("搜索空间出现重复规则语义")
    return definitions


def search_space_summary(definitions: list[RuleDefinition]) -> dict[str, Any]:
    families: dict[str, int] = defaultdict(int)
    for item in definitions:
        families[item.id] += 1
    return {
        "families": dict(sorted(families.items())),
        "candidates": len(definitions),
        "supported_metrics": sorted(BASE_METRICS | WINDOWED_METRICS),
    }


# ---------------------------------------------------------------------------
# Fast screening
# ---------------------------------------------------------------------------

def _limit_pct_vector(symbol: str, dates: pd.Series, is_st: pd.Series) -> np.ndarray:
    code = symbol.zfill(6)
    limit = np.where(is_st.astype(bool), 0.05, np.where(code.startswith("688"), 0.20, 0.10))
    if code.startswith(("300", "301")):
        from datetime import date as _date
        chinext_from = _date(2020, 8, 24)
        mask = np.array([item.date() >= chinext_from for item in dates])
        limit = np.where(mask, 0.20, limit)
    return limit


def _benchmark_maps(source: LocalParquetMarketData, config: SearchConfig) -> tuple[dict[date, float], dict[date, float], dict[date, str]]:
    benchmark_source = LocalParquetMarketData(source.root, config.benchmark_dataset)
    benchmark = benchmark_source.load(config.benchmark_symbol, config.start, config.end)
    open_by_date: dict[date, float] = {}
    close_by_date: dict[date, float] = {}
    regime_by_date: dict[date, str] = {}
    for index, item in enumerate(benchmark):
        day = item.timestamp.date()
        open_by_date[day] = item.open
        close_by_date[day] = item.close
        if index + 1 < config.market_regime_window:
            regime_by_date[day] = "unknown"
            continue
        window = benchmark[index + 1 - config.market_regime_window:index + 1]
        moving_average = sum(row.close for row in window) / config.market_regime_window
        regime_by_date[day] = "bullish" if item.close >= moving_average else "bearish"
    return open_by_date, close_by_date, regime_by_date


def screen_candidates(
    source: LocalParquetMarketData,
    symbols: list[str],
    definitions: list[RuleDefinition],
    config: SearchConfig,
    output_root: Path,
    *,
    universe_manifest: Path | None = None,
) -> dict[str, Any]:
    if not universe_manifest:
        raise ValueError("搜索筛选必须提供点时股票池 manifest")
    if config.out_of_sample_start is None:
        raise ValueError("搜索筛选必须提供验证集起始日期 out_of_sample_start")
    memberships = load_universe_memberships(universe_manifest)
    compiled = [compile_rule(item) for item in definitions]
    needed_indicators: set[str] = set()
    for rule in compiled:
        needed_indicators.update(rule.required_indicators)
    benchmark_open, benchmark_close, regime_by_date = _benchmark_maps(source, config)
    total_cost = 2.0 * (config.commission_bps_per_side + config.slippage_bps_per_side) / 10_000.0
    tolerance = config.limit_tolerance
    grouped: dict[str, dict[tuple[int, str], list[float]]] = {
        rule.semantic_hash: defaultdict(list) for rule in compiled
    }
    signal_counts: dict[str, int] = {rule.semantic_hash: 0 for rule in compiled}
    loaded = skipped = 0
    max_horizon = max(config.horizons)
    for symbol in sorted(set(symbols)):
        try:
            series = source.load(symbol, config.start, config.end)
        except (FileNotFoundError, ValueError):
            skipped += 1
            continue
        if len(series) <= max(rule.max_lookback for rule in compiled) + max_horizon + 1:
            skipped += 1
            continue
        loaded += 1
        frame = candles_to_frame(series)
        columns = compute_indicators(frame, needs=needed_indicators)
        columns.update(_base_columns(frame))
        open_array = frame["open"].to_numpy(dtype=float)
        close_array = frame["close"].to_numpy(dtype=float)
        volume_series = frame["volume"] if "volume" in frame.columns else None
        amount_series = frame["amount"] if "amount" in frame.columns else None
        prev_close = frame["prev_close"].to_numpy(dtype=float)
        is_st = frame["is_st"].fillna(False).astype(bool).to_numpy()
        dates = pd.to_datetime([item.timestamp for item in series])
        limit = _limit_pct_vector(symbol, pd.Series(dates), pd.Series(is_st))
        length = len(series)
        index_range = np.arange(length)
        membership = np.array([active_on(memberships, symbol, item.timestamp.date()) for item in series], dtype=bool) if memberships else np.ones(length, dtype=bool)
        amount_ok = np.ones(length, dtype=bool)
        if config.min_signal_amount is not None:
            if amount_series is not None:
                amount_ok = amount_series.fillna(0.0).to_numpy(dtype=float) >= config.min_signal_amount
            else:
                amount_ok = np.zeros(length, dtype=bool)
        valid_entry = np.zeros(length, dtype=bool)
        valid_exit = np.zeros(length, dtype=bool)
        entry_price = np.full(length, np.nan)
        for j in range(1, length):
            prior = prev_close[j]
            if open_array[j] > 0 and isfinite(prior) and prior > 0 and open_array[j] / prior - 1.0 < limit[j] - tolerance:
                valid_entry[j] = True
                entry_price[j] = open_array[j]
        exit_volume_ok = np.ones(length, dtype=bool)
        if volume_series is not None:
            exit_volume_ok = volume_series.isna().to_numpy() | (volume_series.to_numpy(dtype=float) > 0.0)
        exit_amount_ok = np.ones(length, dtype=bool)
        if amount_series is not None:
            exit_amount_ok = amount_series.isna().to_numpy() | (amount_series.to_numpy(dtype=float) > 0.0)
        for j in range(length):
            prior = prev_close[j]
            if close_array[j] > 0 and isfinite(prior) and prior > 0 and close_array[j] / prior - 1.0 > -limit[j] + tolerance:
                valid_exit[j] = True
        close_after_h = {h: pd.Series(close_array).shift(-h).to_numpy(dtype=float) for h in config.horizons}
        day_dates = [item.timestamp.date() for item in series]
        day_array = np.array(day_dates)
        benchmark_open_at = np.array([benchmark_open.get(day, np.nan) for day in day_dates], dtype=float)
        benchmark_close_at = np.array([benchmark_close.get(day, np.nan) for day in day_dates], dtype=float)
        regime_at = np.array([regime_by_date.get(day, "unknown") for day in day_dates])
        oos_mask = day_array >= config.out_of_sample_start
        for rule, definition in zip(compiled, definitions):
            signal = vectorized_evaluate(rule.normalized_expression, columns, definition.parameters)
            signal = np.array(signal.to_numpy(dtype=bool), copy=True)
            signal &= index_range >= rule.max_lookback
            signal &= membership
            signal &= amount_ok
            signal_indices = np.flatnonzero(signal)
            signal_counts[rule.semantic_hash] += int(signal_indices.size)
            buckets = grouped[rule.semantic_hash]
            for h in config.horizons:
                entry_indices = signal_indices + 1
                keep = entry_indices < length
                entry_indices = entry_indices[keep]
                exit_indices = entry_indices + h - 1
                keep = exit_indices < length
                entry_indices = entry_indices[keep]
                exit_indices = exit_indices[keep]
                if entry_indices.size == 0:
                    continue
                entry_ok = valid_entry[entry_indices] & valid_exit[exit_indices] & exit_volume_ok[exit_indices] & exit_amount_ok[exit_indices]
                entry_indices = entry_indices[entry_ok]
                exit_indices = exit_indices[entry_ok]
                if entry_indices.size == 0:
                    continue
                entry_price_values = entry_price[entry_indices]
                exit_price_values = close_after_h[h][entry_indices]
                entry_ok_final = entry_price_values > 0
                entry_indices = entry_indices[entry_ok_final]
                exit_indices = exit_indices[entry_ok_final]
                entry_price_values = entry_price_values[entry_ok_final]
                exit_price_values = exit_price_values[entry_ok_final]
                if entry_indices.size == 0:
                    continue
                raw_return = exit_price_values / entry_price_values - 1.0
                net_return = raw_return - total_cost
                benchmark_values = benchmark_close_at[exit_indices] / benchmark_open_at[entry_indices] - 1.0
                benchmark_values[~np.isfinite(benchmark_values)] = np.nan
                net_excess = net_return - benchmark_values
                selected_oos = oos_mask[entry_indices] & np.isfinite(net_excess)
                if not selected_oos.any():
                    continue
                regimes = regime_at[entry_indices - 1][selected_oos]
                values = net_excess[selected_oos]
                for regime in np.unique(regimes):
                    buckets[(h, str(regime))].extend(values[regimes == regime].tolist())
    results: dict[str, Any] = {}
    for rule, definition in zip(compiled, definitions):
        rows = (
            {"horizon_bars": h, "market_regime": regime, "net_excess_return": value}
            for (h, regime), values in grouped[rule.semantic_hash].items()
            for value in values
        )
        statistics = summarize_outcomes(rows)
        results[rule.semantic_hash] = {
            "definition": asdict(definition),
            "semantic_hash": rule.semantic_hash,
            "signals": signal_counts[rule.semantic_hash],
            "statistics": statistics,
        }
    _apply_cross_candidate_fdr(results, config.min_out_of_sample_observations)
    output_root.mkdir(parents=True, exist_ok=True)
    candidates_dir = output_root / "candidates"
    candidates_dir.mkdir(parents=True, exist_ok=True)
    ledger = []
    for rule, definition in zip(compiled, definitions):
        record = results[rule.semantic_hash]
        write_json(candidates_dir / f"{rule.semantic_hash.removeprefix('sha256:')[:16]}.json", record)
        ledger.append(_ledger_record(definition, record))
    write_json(output_root / "round.json", {
        "schema_version": "rule-search-round/v1",
        "candidates_total": len(ledger),
        "passed_screen": sum(1 for item in ledger if item["status"] == "passed_screen"),
        "loaded_symbols": loaded,
        "skipped_symbols": skipped,
        "candidates": ledger,
        "disclaimer": NOT_INVESTMENT_ADVICE,
    })
    return {
        "schema_version": "rule-search-round/v1",
        "loaded_symbols": loaded,
        "skipped_symbols": skipped,
        "candidates_total": len(ledger),
        "passed_screen": sum(1 for item in ledger if item["status"] == "passed_screen"),
        "best": sorted([item for item in ledger if item["best_group"]], key=lambda item: item["best_group"]["mean_net_excess_return"], reverse=True)[:10],
    }


def _base_columns(frame: pd.DataFrame) -> dict[str, pd.Series]:
    body = (frame["close"] - frame["open"]).abs()
    range_ = frame["high"] - frame["low"]
    return {
        "open": frame["open"],
        "high": frame["high"],
        "low": frame["low"],
        "close": frame["close"],
        "body": body,
        "range": range_,
        "upper_shadow": frame["high"] - frame[["open", "close"]].max(axis=1),
        "lower_shadow": frame[["open", "close"]].min(axis=1) - frame["low"],
        "is_bullish": frame["close"] > frame["open"],
        "is_bearish": frame["close"] < frame["open"],
    }


def _apply_cross_candidate_fdr(results: dict[str, Any], min_samples: int) -> None:
    all_groups: list[tuple[str, dict[str, Any]]] = []
    for semantic_hash, record in results.items():
        for group in record["statistics"].get("groups", []):
            if group.get("t_statistic") is not None:
                all_groups.append((semantic_hash, group))
    raw = [2.0 * (1.0 - NormalDist().cdf(abs(float(group["t_statistic"])))) for _, group in all_groups]
    if raw:
        from statsmodels.stats.multitest import multipletests
        rejected, adjusted, _, _ = multipletests(raw, alpha=0.05, method="fdr_bh")
    else:
        rejected, adjusted = [], []
    for (semantic_hash, group), raw_value, adjusted_value, reject in zip(all_groups, raw, adjusted, rejected):
        group["raw_p_value"] = float(raw_value)
        group["adjusted_p_value"] = float(adjusted_value)
        group["multiple_testing_reject"] = bool(reject)
    for semantic_hash, record in results.items():
        best = None
        for group in record["statistics"].get("groups", []):
            ci = group.get("confidence_interval") or {}
            passing = (
                group.get("sample_size", 0) >= min_samples
                and (group.get("mean_return") or 0.0) > 0
                and ci.get("lower") is not None
                and ci["lower"] > 0
                and bool(group.get("multiple_testing_reject", False))
            )
            if passing and (best is None or group["mean_return"] > best["mean_net_excess_return"]):
                best = {
                    "horizon_bars": group["horizon_bars"],
                    "market_regime": group["market_regime"],
                    "mean_net_excess_return": group["mean_return"],
                    "sample_size": group["sample_size"],
                    "adjusted_p_value": group["adjusted_p_value"],
                }
        record["best_group"] = best
        record["status"] = "passed_screen" if best else "rejected"


def _ledger_record(definition: RuleDefinition, record: dict[str, Any]) -> dict[str, Any]:
    return {
        "rule_id": definition.id,
        "version": definition.version,
        "semantic_hash": record["semantic_hash"],
        "definition": record["definition"],
        "signals": record["signals"],
        "outcomes_oos": record["statistics"]["outcomes_received"] - record["statistics"]["outcomes_excluded"],
        "best_group": record.get("best_group"),
        "status": record["status"],
    }


def build_search_protocol(
    definitions: list[RuleDefinition],
    symbols: list[str],
    config: SearchConfig,
    output_root: Path,
    *,
    universe_manifest: Path,
) -> dict[str, Any]:
    space = search_space_summary(definitions)
    identity = {
        "schema_version": "rule-search-protocol/v1",
        "status": "preregistered",
        "space": space,
        "candidates": [asdict(item) for item in definitions],
        "periods": {
            "research_start": config.start.isoformat() if config.start else None,
            "validation_start": config.out_of_sample_start.isoformat() if config.out_of_sample_start else None,
            "research_end": config.end.isoformat() if config.end else None,
            "final_lockbox_start": config.lockbox_start.isoformat() if config.lockbox_start else None,
        },
        "outcomes": {"primary_metric": "mean_net_excess_return", "horizons": list(config.horizons), "minimum_oos_observations": config.min_out_of_sample_observations},
        "execution": {"entry": "next_session_open", "exit": "fixed_horizon_close", "base_cost_bps_per_side": {"commission": config.commission_bps_per_side, "slippage": config.slippage_bps_per_side}},
        "analysis": {
            "benchmark_symbol": config.benchmark_symbol,
            "benchmark_dataset": config.benchmark_dataset,
            "market_regime_window": config.market_regime_window,
            "min_signal_amount": config.min_signal_amount,
            "skip_untradeable": config.skip_untradeable,
        },
        "universe_manifest": str(universe_manifest),
        "symbols": sorted(set(symbols)),
        "multiple_testing": {"method": "fdr_bh", "alpha": 0.05, "scope": "all_candidates_all_groups"},
        "publication": "blocked_until_human_approval_and_final_lockbox",
    }
    search_id = "search_" + sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()[:24]
    protocol = {**identity, "search_id": search_id, "created_at": datetime.now(timezone.utc).isoformat()}
    output_root.mkdir(parents=True, exist_ok=True)
    write_json(output_root / "search_protocol.json", protocol)
    return protocol
