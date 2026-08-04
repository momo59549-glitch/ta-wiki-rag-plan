from __future__ import annotations

from datetime import datetime
from typing import Any

from packages.contracts import Candle, ConditionResult, RuleEvaluation
from packages.rule_dsl import CompiledRule


def _metric(candle: Candle, name: str) -> float | bool:
    body = abs(candle.close - candle.open)
    if name == "body": return body
    if name == "range": return candle.high - candle.low
    if name == "lower_shadow": return min(candle.open, candle.close) - candle.low
    if name == "upper_shadow": return candle.high - max(candle.open, candle.close)
    if name == "is_bullish": return candle.close > candle.open
    if name == "is_bearish": return candle.close < candle.open
    raise ValueError(f"未知指标: {name}")


def _value(node: Any, series: list[Candle], index: int, parameters: dict[str, float]) -> float | bool:
    if isinstance(node, (int, float, bool)): return node
    op, value = next(iter(node.items()))
    if op == "param": return parameters[value]
    if op == "metric":
        target = index + value["offset"]
        if target < 0: raise IndexError("历史数据不足")
        return _metric(series[target], value["name"])
    if op == "context":
        window, minimum = value["window"], value["min_count"]
        if index < window: raise IndexError("上下文数据不足")
        return sum(series[i].close < series[i - 1].close for i in range(index - window + 1, index)) >= minimum
    children = value if isinstance(value, list) else [value]
    vals = [_value(child, series, index, parameters) for child in children]
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


def _conditions(node: Any, series: list[Candle], index: int, params: dict[str, float], path: str = "expression") -> list[ConditionResult]:
    op, value = next(iter(node.items()))
    if op == "all":
        return [item for i, child in enumerate(value) for item in _conditions(child, series, index, params, f"{path}.all[{i}]")]
    if op in {"gt", "gte", "lt", "lte", "eq"}:
        actual = _value(value[0], series, index, params)
        threshold = _value(value[1], series, index, params)
        passed = _value(node, series, index, params)
        return [ConditionResult(path, op, actual, threshold, passed, op)]
    result = _value(node, series, index, params)
    return [ConditionResult(path, op, result, True, bool(result), op)]


def evaluate(series: list[Candle], as_of_index: int, rule: CompiledRule, parameters: dict[str, float] | None = None) -> RuleEvaluation:
    if as_of_index < 0 or as_of_index >= len(series):
        raise ValueError("as_of_index 超出 CandleSeries")
    params = rule.definition.parameters | (parameters or {})
    observed = series[as_of_index].timestamp
    executable: datetime | None = series[as_of_index + 1].timestamp if as_of_index + 1 < len(series) else None
    try:
        if as_of_index < rule.max_lookback:
            raise IndexError("warmup 数据不足")
        conditions = tuple(_conditions(rule.normalized_expression, series, as_of_index, params))
        matched = bool(_value(rule.normalized_expression, series, as_of_index, params))
        return RuleEvaluation(matched, "matched" if matched else "not_matched", observed, executable, conditions, rule.semantic_hash)
    except (IndexError, ZeroDivisionError) as exc:
        return RuleEvaluation(False, "insufficient_data", observed, executable, (), rule.semantic_hash, (str(exc),))
    except (TypeError, ValueError) as exc:
        return RuleEvaluation(False, "data_error", observed, executable, (), rule.semantic_hash, (str(exc),))
