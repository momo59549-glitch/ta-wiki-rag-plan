from __future__ import annotations

from datetime import datetime
from math import sqrt
from typing import Any

from packages.contracts import Candle, ConditionResult, RuleEvaluation
from packages.rule_dsl import CompiledRule, indicator_key
from packages.rule_dsl.compiler import WINDOWED_METRICS, MACD_FAST, MACD_SLOW, MACD_SIGNAL


def _base_metric(candle: Candle, name: str) -> float | bool:
    if name == "open": return candle.open
    if name == "high": return candle.high
    if name == "low": return candle.low
    if name == "close": return candle.close
    body = abs(candle.close - candle.open)
    if name == "body": return body
    if name == "range": return candle.high - candle.low
    if name == "lower_shadow": return min(candle.open, candle.close) - candle.low
    if name == "upper_shadow": return candle.high - max(candle.open, candle.close)
    if name == "is_bullish": return candle.close > candle.open
    if name == "is_bearish": return candle.close < candle.open
    raise ValueError(f"未知指标: {name}")


def _ema_value(values: list[float], target: int, span: int) -> float:
    alpha = 2.0 / (span + 1.0)
    result = values[0]
    for item in values[1:target + 1]:
        result = alpha * item + (1.0 - alpha) * result
    return result


def _windowed_value(candles: list[Candle], target: int, name: str, window: int, key: str, indicators: Any) -> float:
    """Return a windowed indicator value, preferring the precomputed column."""
    if indicators is not None and key in indicators:
        return float(indicators[key][target])
    if name in {"sma", "max_high", "min_low", "boll_upper", "boll_lower"} and target < window - 1:
        raise IndexError("窗口数据不足")
    if name == "roc" and target < window:
        raise IndexError("窗口数据不足")
    if name == "sma":
        return sum(item.close for item in candles[target - window + 1:target + 1]) / window
    if name == "ema":
        return _ema_value([item.close for item in candles], target, window)
    if name == "roc":
        previous = candles[target - window].close
        return candles[target].close / previous - 1.0
    if name == "volume_ratio":
        volumes = [item.volume for item in candles[target - window + 1:target + 1]]
        average = sum(item for item in volumes if item is not None) / window
        current = candles[target].volume
        if current is None:
            return 0.0
        return current / average if average > 0.0 else 0.0
    if name in {"max_high", "min_low"}:
        values = [getattr(item, "high" if name == "max_high" else "low") for item in candles[target - window + 1:target + 1]]
        return max(values) if name == "max_high" else min(values)
    if name == "rsi":
        alpha = 1.0 / window
        average_gain = average_loss = 0.0
        for i in range(1, target + 1):
            delta = candles[i].close - candles[i - 1].close
            average_gain = alpha * max(delta, 0.0) + (1.0 - alpha) * average_gain
            average_loss = alpha * max(-delta, 0.0) + (1.0 - alpha) * average_loss
        if average_loss == 0.0:
            return 100.0 if average_gain > 0.0 else 50.0
        if average_gain == 0.0:
            return 0.0
        return 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    if name in {"boll_upper", "boll_lower"}:
        closes = [item.close for item in candles[target - window + 1:target + 1]]
        average = sum(closes) / window
        variance = sum((item - average) ** 2 for item in closes) / window
        stddev = sqrt(variance)
        return average + 2.0 * stddev if name == "boll_upper" else average - 2.0 * stddev
    if name in {"macd_dif", "macd_dea", "macd_hist"}:
        closes = [item.close for item in candles]
        differences = [_ema_value(closes, i, MACD_FAST) - _ema_value(closes, i, MACD_SLOW) for i in range(target + 1)]
        if name == "macd_dif":
            return differences[target]
        signal = _ema_value(differences, target, MACD_SIGNAL)
        return signal if name == "macd_dea" else differences[target] - signal
    raise ValueError(f"未知窗口指标: {name}")


def _metric(candles: list[Candle], index: int, name: str, window: int | None, offset: int, indicators: Any) -> float | bool:
    target = index + offset
    if name in WINDOWED_METRICS:
        key = indicator_key(name, window)
        return _windowed_value(candles, target, name, int(window), key, indicators)
    if target < 0:
        raise IndexError("历史数据不足")
    return _base_metric(candles[target], name)


def _value(node: Any, series: list[Candle], index: int, parameters: dict[str, float], indicators: Any) -> float | bool:
    if isinstance(node, (int, float, bool)): return node
    op, value = next(iter(node.items()))
    if op == "param": return parameters[value]
    if op == "metric":
        return _metric(series, index, value["name"], value.get("window"), value["offset"], indicators)
    if op == "context":
        window, minimum = value["window"], value["min_count"]
        if index < window: raise IndexError("上下文数据不足")
        if value["name"] == "lower_close_count":
            return sum(series[i].close < series[i - 1].close for i in range(index - window + 1, index)) >= minimum
        return sum(series[i].close > series[i - 1].close for i in range(index - window + 1, index)) >= minimum
    children = value if isinstance(value, list) else [value]
    vals = [_value(child, series, index, parameters, indicators) for child in children]
    if op == "all": return all(vals)
    if op == "any": return any(vals)
    if op == "not": return not vals[0]
    if op == "add": return sum(vals)
    if op == "sub": return vals[0] - vals[1]
    if op == "mul":
        result = 1
        for item in vals: result *= item
        return result
    if op == "abs": return abs(vals[0])
    if op == "min": return min(vals)
    if op == "max": return max(vals)
    if op == "div": return vals[0] / vals[1]
    if op == "safe_div": return 0.0 if vals[1] == 0 else vals[0] / vals[1]
    if op == "gt": return vals[0] > vals[1]
    if op == "gte": return vals[0] >= vals[1]
    if op == "lt": return vals[0] < vals[1]
    if op == "lte": return vals[0] <= vals[1]
    if op == "eq": return vals[0] == vals[1]
    raise ValueError(f"未知操作: {op}")


def _conditions(node: Any, series: list[Candle], index: int, params: dict[str, float], indicators: Any, path: str = "expression") -> list[ConditionResult]:
    op, value = next(iter(node.items()))
    if op == "all":
        return [item for i, child in enumerate(value) for item in _conditions(child, series, index, params, indicators, f"{path}.all[{i}]")]
    if op in {"gt", "gte", "lt", "lte", "eq"}:
        actual = _value(value[0], series, index, params, indicators)
        threshold = _value(value[1], series, index, params, indicators)
        passed = _value(node, series, index, params, indicators)
        return [ConditionResult(path, op, actual, threshold, passed, op)]
    result = _value(node, series, index, params, indicators)
    return [ConditionResult(path, op, result, True, bool(result), op)]


def evaluate(
    series: list[Candle],
    as_of_index: int,
    rule: CompiledRule,
    parameters: dict[str, float] | None = None,
    *,
    indicators: dict[str, list[float]] | None = None,
) -> RuleEvaluation:
    if as_of_index < 0 or as_of_index >= len(series):
        raise ValueError("as_of_index 超出 CandleSeries")
    params = rule.definition.parameters | (parameters or {})
    observed = series[as_of_index].timestamp
    executable: datetime | None = series[as_of_index + 1].timestamp if as_of_index + 1 < len(series) else None
    try:
        if as_of_index < rule.max_lookback:
            raise IndexError("warmup 数据不足")
        # Every candle used by the rule must have been available by the
        # decision time.  ``None`` remains a compatibility default equal to
        # the bar timestamp; production adapters always populate it.
        for candle in series[as_of_index - rule.max_lookback:as_of_index + 1]:
            visible_at = candle.available_at or candle.timestamp
            if visible_at > observed:
                raise ValueError(f"未来数据不可见: {visible_at.isoformat()} > {observed.isoformat()}")
        conditions = tuple(_conditions(rule.normalized_expression, series, as_of_index, params, indicators))
        matched = bool(_value(rule.normalized_expression, series, as_of_index, params, indicators))
        return RuleEvaluation(matched, "matched" if matched else "not_matched", observed, executable, conditions, rule.semantic_hash)
    except (IndexError, ZeroDivisionError) as exc:
        return RuleEvaluation(False, "insufficient_data", observed, executable, (), rule.semantic_hash, (str(exc),))
    except (TypeError, ValueError) as exc:
        return RuleEvaluation(False, "data_error", observed, executable, (), rule.semantic_hash, (str(exc),))
