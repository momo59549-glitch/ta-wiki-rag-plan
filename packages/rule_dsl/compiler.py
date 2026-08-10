"""受限 JSON AST 编译器：只接受白名单节点，永不使用 eval/exec。"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
from typing import Any

from packages.contracts import RuleDefinition


class RuleCompileError(ValueError):
    pass


_OPS = {"all", "any", "not", "gt", "gte", "lt", "lte", "eq", "add", "sub", "mul", "div", "safe_div", "abs", "min", "max"}
_METRICS = {"open", "high", "low", "close", "body", "range", "upper_shadow", "lower_shadow", "is_bullish", "is_bearish"}
WINDOWED_METRICS = {
    "sma", "ema", "rsi", "roc", "max_high", "min_low",
    "boll_upper", "boll_lower", "macd_dif", "macd_dea", "macd_hist",
    "volume_ratio",
}
_CONTEXTS = {"lower_close_count", "higher_close_count"}
_COMMUTATIVE_ASSOCIATIVE_OPS = {"all", "any", "add", "mul", "min", "max"}

MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
BOLL_MULT = 2.0


def indicator_key(name: str, window: int | None = None) -> str:
    """Canonical key shared by the compiler, engine and vectorized indicators."""
    if name in {"macd_dif"}:
        return f"{name}:{MACD_FAST}:{MACD_SLOW}"
    if name in {"macd_dea", "macd_hist"}:
        return f"{name}:{MACD_FAST}:{MACD_SLOW}:{MACD_SIGNAL}"
    if name in {"boll_upper", "boll_lower"}:
        if window is None:
            raise ValueError("boll_upper/boll_lower 需要 window")
        return f"{name}:{window}:{BOLL_MULT}"
    if window is None:
        raise ValueError(f"{name} 需要 window")
    return f"{name}:{window}"


@dataclass(frozen=True, slots=True)
class CompiledRule:
    definition: RuleDefinition
    normalized_expression: dict[str, Any]
    semantic_hash: str
    max_lookback: int
    required_indicators: tuple[str, ...] = ()


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def _logic_sort_key(value: Any) -> str:
    """Stable sort key for commutative DSL children."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _canonical_logic(node: Any, parameters: dict[str, float]) -> Any:
    """Canonicalize rule behavior without its display/catalog identity.

    Parameter references are resolved to their frozen values, and the
    commutative/associative operators are flattened and sorted.  This makes
    ``rsi_oversold@1`` and an auto-discovery rule with another id/version (or
    another parameter label) compare by behavior rather than by provenance.
    ``compile_rule`` remains the syntax authority; callers invoke this only
    after successful compilation.
    """
    if isinstance(node, bool):
        return node
    if isinstance(node, (int, float)):
        number = float(node)
        if not math.isfinite(number):
            raise RuleCompileError("规则逻辑哈希不接受非有限数值")
        return 0.0 if number == 0.0 else number
    if not isinstance(node, dict) or len(node) != 1:
        raise RuleCompileError("规则逻辑哈希遇到非法 AST 节点")
    op, value = next(iter(node.items()))
    if op == "param":
        if not isinstance(value, str) or value not in parameters:
            raise RuleCompileError(f"未知参数: {value}")
        return _canonical_logic(parameters[value], parameters)
    if op in {"metric", "context"}:
        return {op: _canonical(value)}

    children = value if isinstance(value, list) else [value]
    normalized_children = [_canonical_logic(child, parameters) for child in children]
    if op in _COMMUTATIVE_ASSOCIATIVE_OPS:
        flattened: list[Any] = []
        for child in normalized_children:
            if isinstance(child, dict) and set(child) == {op} and isinstance(child[op], list):
                flattened.extend(child[op])
            else:
                flattened.append(child)
        normalized_children = sorted(flattened, key=_logic_sort_key)
    return {op: normalized_children}


def _validate(node: Any, parameters: dict[str, float], required: set[str]) -> int:
    if isinstance(node, (int, float, bool)):
        return 0
    if not isinstance(node, dict) or len(node) != 1:
        raise RuleCompileError("每个 AST 节点必须是仅含一个操作符的对象")
    op, value = next(iter(node.items()))
    if op == "param":
        if not isinstance(value, str) or value not in parameters:
            raise RuleCompileError(f"未知参数: {value}")
        return 0
    if op == "metric":
        if not isinstance(value, dict) or not {"name", "offset"} <= set(value) <= {"name", "offset", "window"}:
            raise RuleCompileError("metric 必须包含 name、offset，可选 window")
        name = value["name"]
        if name not in _METRICS | WINDOWED_METRICS or not isinstance(value["offset"], int):
            raise RuleCompileError("metric 名称或 offset 非法")
        if value["offset"] > 0:
            raise RuleCompileError("禁止未来引用：metric.offset 必须 <= 0")
        window = value.get("window")
        if name in WINDOWED_METRICS:
            if not isinstance(window, int) or window < 2:
                raise RuleCompileError(f"{name} 必须带 window >= 2")
            required.add(indicator_key(name, window))
            if name.startswith("macd_"):
                return MACD_SLOW + MACD_SIGNAL - value["offset"]
            return window - 1 - value["offset"]
        if window is not None:
            raise RuleCompileError(f"{name} 不支持 window")
        return -value["offset"]
    if op == "context":
        if not isinstance(value, dict) or set(value) != {"name", "window", "min_count"}:
            raise RuleCompileError("context 必须包含 name、window、min_count")
        if value["name"] not in _CONTEXTS or not isinstance(value["window"], int) or value["window"] < 2:
            raise RuleCompileError("context 仅支持正窗口的 lower/higher_close_count")
        if not isinstance(value["min_count"], int) or not 1 <= value["min_count"] <= value["window"]:
            raise RuleCompileError("context.min_count 超出窗口")
        required.add(indicator_key(value["name"], value["window"]))
        return value["window"]
    if op not in _OPS:
        raise RuleCompileError(f"不支持的 DSL 操作符: {op}")
    children = value if isinstance(value, list) else [value]
    if op == "not" and len(children) != 1:
        raise RuleCompileError("not 只接受一个操作数")
    if op in {"gt", "gte", "lt", "lte", "eq", "sub", "div", "safe_div"} and len(children) != 2:
        raise RuleCompileError(f"{op} 只接受两个操作数")
    if op in {"all", "any", "add", "mul", "min", "max"} and not children:
        raise RuleCompileError(f"{op} 不能为空")
    return max((_validate(child, parameters, required) for child in children), default=0)


def compile_rule(definition: RuleDefinition) -> CompiledRule:
    required: set[str] = set()
    max_lookback = _validate(definition.expression, definition.parameters, required)
    normalized = _canonical(definition.expression)
    payload = {"id": definition.id, "version": definition.version, "parameters": definition.parameters, "expression": normalized}
    semantic_hash = "sha256:" + sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return CompiledRule(definition, normalized, semantic_hash, max(max_lookback, definition.warmup_bars), tuple(sorted(required)))


def canonical_rule_logic(definition: RuleDefinition) -> dict[str, Any]:
    """Return the ID-independent, execution-relevant rule logic signature.

    ``warmup_bars`` is intentionally not part of the *logic* identity: it is
    an evaluation-start policy rather than a signal condition.  The campaign
    bridge freezes it separately in a full-definition hash, while this helper
    can still detect an equivalent catalog signal whose warmup policy differs.
    """
    compile_rule(definition)
    return {
        "schema_version": "rule-logic/v1",
        "expression": _canonical_logic(definition.expression, definition.parameters),
        "observed_at": definition.observed_at,
        "executable_from": definition.executable_from,
    }


def rule_logic_hash(definition: RuleDefinition) -> str:
    """Hash canonical behavior independently of id, version, and labels."""
    payload = canonical_rule_logic(definition)
    return "sha256:" + sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def rule_definition_hash(definition: RuleDefinition) -> str:
    """Hash every RuleDefinition field for immutable campaign binding."""
    payload = {
        "schema_version": "rule-definition/v1",
        "id": definition.id,
        "version": definition.version,
        "name_zh": definition.name_zh,
        "expression": _canonical(definition.expression),
        "parameters": _canonical(definition.parameters),
        "warmup_bars": definition.warmup_bars,
        "observed_at": definition.observed_at,
        "executable_from": definition.executable_from,
    }
    return "sha256:" + sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
