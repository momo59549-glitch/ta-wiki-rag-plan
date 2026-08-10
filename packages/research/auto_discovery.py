"""Deterministic, outcome-blind automatic discovery for bounded DSL rules.

This module is deliberately a *research-screening* layer.  It creates a
finite grammar before loading outcomes, delegates all outcome calculation to
``screen_candidates``, and records state-specific evidence in a separate
registry.  Nothing here publishes a rule, creates a campaign, or authorizes an
order.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import random
import re
from typing import Any

from packages.contracts import RuleDefinition
from packages.market_data import LocalParquetMarketData
from packages.research.json_store import write_json
from packages.research.rule_search import (
    SearchConfig,
    build_search_data_snapshot,
    build_search_protocol,
    screen_candidates,
)
from packages.rule_dsl import compile_rule, rule_logic_hash


AUTO_DISCOVERY_SCHEMA = "auto-discovery-protocol/v1"
REGISTRY_SCHEMA = "regime-candidate-registry/v1"
TRIAL_LEDGER_SCHEMA = "auto-discovery-trial-ledger/v1"
GRAMMAR_VERSION = "restricted-ta-grammar/v1"
MAX_CANDIDATE_BUDGET = 256
# The current restricted grammar contains 100 unique logic definitions under
# its default complexity limits.  A later grammar version may extend it, but a
# generation must never pretend this finite v1 space is inexhaustible.
GRAMMAR_V1_UNIQUE_LOGIC_CAPACITY = 100
REGIMES = frozenset({"bullish", "bearish", "unknown"})
PUBLICATION_BLOCK = "blocked_until_frozen_campaign_final_lockbox_and_human_approval"
_GENERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_COMPARISON_OPS = frozenset({"gt", "gte", "lt", "lte", "eq"})


@dataclass(frozen=True, slots=True)
class DiscoveryConfig:
    """Frozen, outcome-blind grammar and lifecycle controls for one generation."""

    generation_id: str
    candidate_budget: int = 64
    seed: int = 20260809
    grammar_version: str = GRAMMAR_VERSION
    max_ast_nodes: int = 24
    max_conditions: int = 3
    parent_generation_id: str | None = None
    parent_archive_id: str | None = None
    prior_cumulative_candidate_budget: int = 0
    revalidation_days: int = 90
    min_revalidation_observations: int = 100
    min_mean_net_excess_return: float = 0.0
    max_mean_return_drop: float = 0.02
    retire_when_ci_lower_nonpositive: bool = True

    def __post_init__(self) -> None:
        if not _GENERATION_ID.fullmatch(self.generation_id):
            raise ValueError("generation_id 只能包含字母、数字、._-，且不能以符号开头")
        if self.parent_generation_id is not None:
            if not _GENERATION_ID.fullmatch(self.parent_generation_id):
                raise ValueError("parent_generation_id 非法")
            if self.parent_generation_id == self.generation_id:
                raise ValueError("parent_generation_id 不能等于 generation_id")
        if not 1 <= self.candidate_budget <= MAX_CANDIDATE_BUDGET:
            raise ValueError(f"candidate_budget 必须在 [1, {MAX_CANDIDATE_BUDGET}] 内")
        if not self.grammar_version:
            raise ValueError("grammar_version 不能为空")
        if self.max_ast_nodes < 3:
            raise ValueError("max_ast_nodes 至少为 3")
        if self.max_conditions < 1:
            raise ValueError("max_conditions 至少为 1")
        if self.prior_cumulative_candidate_budget < 0:
            raise ValueError("prior_cumulative_candidate_budget 不能为负")
        if self.revalidation_days < 1:
            raise ValueError("revalidation_days 至少为 1")
        if self.min_revalidation_observations < 1:
            raise ValueError("min_revalidation_observations 至少为 1")
        if self.max_mean_return_drop < 0:
            raise ValueError("max_mean_return_drop 不能为负")


@dataclass(frozen=True, slots=True)
class _GrammarCandidate:
    family: str
    expression: dict[str, Any]
    parameters: dict[str, float]


def _metric(name: str, offset: int = 0, window: int | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": name, "offset": offset}
    if window is not None:
        payload["window"] = window
    return {"metric": payload}


def _param(name: str) -> dict[str, Any]:
    return {"param": name}


def _context(name: str, window: int, min_count: int) -> dict[str, Any]:
    return {"context": {"name": name, "window": window, "min_count": min_count}}


def _all(*children: Any) -> dict[str, Any]:
    return {"all": list(children)}


def _gt(left: Any, right: Any) -> dict[str, Any]:
    return {"gt": [left, right]}


def _gte(left: Any, right: Any) -> dict[str, Any]:
    return {"gte": [left, right]}


def _lt(left: Any, right: Any) -> dict[str, Any]:
    return {"lt": [left, right]}


def _lte(left: Any, right: Any) -> dict[str, Any]:
    return {"lte": [left, right]}


def _eq(left: Any, right: Any) -> dict[str, Any]:
    return {"eq": [left, right]}


def _safe_div(left: Any, right: Any) -> dict[str, Any]:
    return {"safe_div": [left, right]}


def _max(left: Any, right: Any) -> dict[str, Any]:
    return {"max": [left, right]}


def _ratio(name: str, denominator: str) -> dict[str, Any]:
    return _safe_div(_metric(name), _max(_metric(denominator), 0.01))


def _hammer(parameters: dict[str, float], *, context: bool = False) -> dict[str, Any]:
    children: list[Any] = [
        _gte(_ratio("lower_shadow", "body"), _param("min_lower_shadow_body")),
        _lte(_ratio("upper_shadow", "range"), _param("max_upper_shadow_range")),
    ]
    if context:
        children.append(_context("lower_close_count", 5, 3))
    return _all(*children)


def _shooting_star(parameters: dict[str, float], *, context: bool = False) -> dict[str, Any]:
    children: list[Any] = [
        _gte(_ratio("upper_shadow", "body"), _param("min_upper_shadow_body")),
        _lte(_ratio("lower_shadow", "range"), _param("max_lower_shadow_range")),
    ]
    if context:
        children.append(_context("higher_close_count", 5, 3))
    return _all(*children)


def _grammar_candidates() -> list[_GrammarCandidate]:
    """Return the complete finite grammar without looking at any market outcome."""
    candidates: list[_GrammarCandidate] = []

    for window in (10, 20, 60):
        candidates.append(_GrammarCandidate("price_above_sma", _gt(_metric("close"), _metric("sma", window=window)), {"window": float(window)}))
        candidates.append(_GrammarCandidate("price_below_sma", _lt(_metric("close"), _metric("sma", window=window)), {"window": float(window)}))
        candidates.append(_GrammarCandidate("sma_rising", _gt(_metric("sma", window=window), _metric("sma", offset=-5, window=window)), {"window": float(window)}))
        candidates.append(_GrammarCandidate("sma_falling", _lt(_metric("sma", window=window), _metric("sma", offset=-5, window=window)), {"window": float(window)}))

    for fast, slow in ((5, 20), (5, 60), (10, 20), (10, 60), (20, 60)):
        candidates.append(
            _GrammarCandidate(
                "ma_cross_up",
                _all(
                    _gt(_metric("sma", window=fast), _metric("sma", window=slow)),
                    _lte(_metric("sma", offset=-1, window=fast), _metric("sma", offset=-1, window=slow)),
                ),
                {"fast": float(fast), "slow": float(slow)},
            )
        )
        candidates.append(
            _GrammarCandidate(
                "ma_cross_down",
                _all(
                    _lt(_metric("sma", window=fast), _metric("sma", window=slow)),
                    _gte(_metric("sma", offset=-1, window=fast), _metric("sma", offset=-1, window=slow)),
                ),
                {"fast": float(fast), "slow": float(slow)},
            )
        )

    for window in (7, 14, 21):
        for threshold in (25.0, 30.0, 35.0):
            candidates.append(_GrammarCandidate("rsi_oversold", _lt(_metric("rsi", window=window), _param("threshold")), {"window": float(window), "threshold": threshold}))
        for threshold in (65.0, 70.0, 75.0):
            candidates.append(_GrammarCandidate("rsi_overbought", _gt(_metric("rsi", window=window), _param("threshold")), {"window": float(window), "threshold": threshold}))

    for window in (5, 10, 20):
        for threshold in (0.03, 0.05):
            candidates.append(_GrammarCandidate("roc_positive", _gt(_metric("roc", window=window), _param("threshold")), {"window": float(window), "threshold": threshold}))
            candidates.append(_GrammarCandidate("roc_negative", _lt(_metric("roc", window=window), _param("threshold")), {"window": float(window), "threshold": -threshold}))

    for window in (20, 60):
        candidates.append(_GrammarCandidate("breakout_high", _gt(_metric("close"), _metric("max_high", offset=-1, window=window)), {"window": float(window)}))
        candidates.append(_GrammarCandidate("breakout_low", _lt(_metric("close"), _metric("min_low", offset=-1, window=window)), {"window": float(window)}))

    for multiple in (1.5, 2.0, 3.0):
        candidates.append(_GrammarCandidate("volume_surge_up", _all(_gt(_metric("volume_ratio", window=20), _param("multiple")), _eq(_metric("is_bullish"), True)), {"multiple": multiple}))
        candidates.append(_GrammarCandidate("volume_surge_down", _all(_gt(_metric("volume_ratio", window=20), _param("multiple")), _eq(_metric("is_bearish"), True)), {"multiple": multiple}))

    for min_shadow in (1.5, 2.0, 2.5):
        for max_upper in (0.10, 0.20):
            params = {"min_lower_shadow_body": min_shadow, "max_upper_shadow_range": max_upper}
            candidates.append(_GrammarCandidate("hammer", _hammer(params), params))
            candidates.append(_GrammarCandidate("hammer_context", _hammer(params, context=True), params))
            star_params = {"min_upper_shadow_body": min_shadow, "max_lower_shadow_range": max_upper}
            candidates.append(_GrammarCandidate("shooting_star", _shooting_star(star_params), star_params))
            candidates.append(_GrammarCandidate("shooting_star_context", _shooting_star(star_params, context=True), star_params))

    for direction, context_name in (("bullish", "lower_close_count"), ("bearish", "higher_close_count")):
        candidates.append(
            _GrammarCandidate(
                # This is intentionally not called ``engulfing``: it asserts
                # opposite-colour adjacent candles plus trend context, but it
                # does not assert that the current real body covers the prior
                # real body.  The name must not overstate its semantics.
                f"two_candle_reversal_context_{direction}",
                _all(
                    _eq(_metric(f"is_{direction}"), True),
                    _eq(_metric("is_bearish" if direction == "bullish" else "is_bullish", offset=-1), True),
                    _context(context_name, 5, 3),
                ),
                {},
            )
        )

    for max_body_range in (0.05, 0.10, 0.15):
        candidates.append(
            _GrammarCandidate(
                "doji_after_decline",
                _all(_lte(_ratio("body", "range"), _param("max_body_range")), _context("lower_close_count", 5, 3)),
                {"max_body_range": max_body_range},
            )
        )

    # Finite combinations are constructed explicitly.  They remain bounded by
    # the same AST/condition gates as single-component candidates.
    for rsi_window, rsi_threshold, trend_window in ((14, 30.0, 20), (14, 35.0, 60), (21, 30.0, 20)):
        candidates.append(
            _GrammarCandidate(
                "combo_rsi_trend",
                _all(
                    _lt(_metric("rsi", window=rsi_window), _param("rsi_threshold")),
                    _lt(_metric("close"), _metric("sma", window=trend_window)),
                    _context("lower_close_count", 5, 3),
                ),
                {"rsi_window": float(rsi_window), "rsi_threshold": rsi_threshold, "trend_window": float(trend_window)},
            )
        )
    for min_shadow, ma_window in ((1.5, 20), (2.0, 20), (2.0, 60)):
        candidates.append(
            _GrammarCandidate(
                "combo_hammer_trend",
                _all(
                    _gte(_ratio("lower_shadow", "body"), _param("min_lower_shadow_body")),
                    _lte(_ratio("upper_shadow", "range"), _param("max_upper_shadow_range")),
                    _lt(_metric("close"), _metric("sma", window=ma_window)),
                ),
                {"min_lower_shadow_body": min_shadow, "max_upper_shadow_range": 0.20, "ma_window": float(ma_window)},
            )
        )
    for volume_multiple, breakout_window in ((1.5, 20), (2.0, 20), (2.0, 60)):
        candidates.append(
            _GrammarCandidate(
                "combo_breakout_volume",
                _all(
                    _gt(_metric("close"), _metric("max_high", offset=-1, window=breakout_window)),
                    _gt(_metric("volume_ratio", window=20), _param("multiple")),
                    _eq(_metric("is_bullish"), True),
                ),
                {"multiple": volume_multiple, "breakout_window": float(breakout_window)},
            )
        )
    return candidates


def ast_node_count(expression: Any) -> int:
    """Count DSL object nodes (literals do not consume an AST-node budget)."""
    if isinstance(expression, dict):
        return 1 + sum(ast_node_count(value) for value in expression.values())
    if isinstance(expression, list):
        return sum(ast_node_count(value) for value in expression)
    return 0


def condition_count(expression: Any) -> int:
    """Count relational/context conditions, independent of arithmetic subtrees."""
    if not isinstance(expression, dict):
        if isinstance(expression, list):
            return sum(condition_count(item) for item in expression)
        return 0
    op, value = next(iter(expression.items()))
    current = 1 if op in _COMPARISON_OPS or op == "context" else 0
    return current + condition_count(value)


def metric_offsets(expression: Any) -> tuple[int, ...]:
    """Return every metric offset so tests and protocol checks can audit it."""
    if isinstance(expression, dict):
        op, value = next(iter(expression.items()))
        if op == "metric":
            return (int(value["offset"]),)
        return metric_offsets(value)
    if isinstance(expression, list):
        return tuple(offset for item in expression for offset in metric_offsets(item))
    return ()


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def _sha256_identity(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_json_default)
    return "sha256:" + sha256(payload.encode("utf-8")).hexdigest()


def _require_frozen_search_boundaries(config: SearchConfig) -> None:
    values = (config.start, config.out_of_sample_start, config.end, config.lockbox_start)
    if any(value is None for value in values):
        raise ValueError("自动发现必须显式冻结 start、out_of_sample_start、end 和 lockbox_start")
    assert config.start is not None
    assert config.out_of_sample_start is not None
    assert config.end is not None
    assert config.lockbox_start is not None
    if not config.start < config.out_of_sample_start <= config.end < config.lockbox_start:
        raise ValueError("自动发现时间边界必须满足 start < OOS <= end < lockbox")


def discovery_semantic_hash(definition: RuleDefinition) -> str:
    """Hash rule logic independent of generated id/version for lineage deduplication."""
    return _sha256_identity(
        {
            "expression": _canonical(definition.expression),
            "parameters": dict(sorted(definition.parameters.items())),
            "warmup_bars": definition.warmup_bars,
            "observed_at": definition.observed_at,
            "executable_from": definition.executable_from,
        }
    )


def _validate_generated_definition(definition: RuleDefinition, config: DiscoveryConfig) -> None:
    compiled = compile_rule(definition)
    if any(offset > 0 for offset in metric_offsets(definition.expression)):
        raise ValueError("自动发现候选含有未来 offset")
    if ast_node_count(definition.expression) > config.max_ast_nodes:
        raise ValueError("自动发现候选超过 max_ast_nodes")
    if condition_count(definition.expression) > config.max_conditions:
        raise ValueError("自动发现候选超过 max_conditions")
    # compile_rule is intentionally called even after the local checks: the
    # production DSL compiler remains the sole authority on allowed syntax.
    if compiled.max_lookback < 0:  # pragma: no cover - defensive contract
        raise ValueError("规则 lookback 非法")


def _eligible_grammar_candidates(
    config: DiscoveryConfig,
    excluded_semantic_hashes: Iterable[str],
    excluded_logic_hashes: Iterable[str] = (),
) -> list[tuple[str, _GrammarCandidate]]:
    """Return finite, deduplicated grammar entries available to a generation."""
    excluded = set(excluded_semantic_hashes)
    excluded_logic = set(excluded_logic_hashes)
    unique: dict[str, _GrammarCandidate] = {}
    for item in _grammar_candidates():
        provisional = RuleDefinition(
            id=f"auto_{item.family}",
            version="grammar",
            name_zh=f"自动发现 {item.family}",
            expression=item.expression,
            parameters=item.parameters,
        )
        _validate_generated_definition(provisional, config)
        logic_hash = discovery_semantic_hash(provisional)
        compiler_hash = compile_rule(provisional).semantic_hash
        behavior_hash = rule_logic_hash(provisional)
        if logic_hash in excluded or compiler_hash in excluded or behavior_hash in excluded_logic:
            continue
        unique.setdefault(logic_hash, item)
    return [(logic_hash, unique[logic_hash]) for logic_hash in sorted(unique)]


def available_candidate_capacity(
    config: DiscoveryConfig,
    *,
    excluded_semantic_hashes: Iterable[str] = (),
    excluded_logic_hashes: Iterable[str] = (),
) -> int:
    """Return the remaining finite grammar capacity before a generation runs."""
    return len(_eligible_grammar_candidates(config, excluded_semantic_hashes, excluded_logic_hashes))


def generate_candidates(
    config: DiscoveryConfig,
    *,
    excluded_semantic_hashes: Iterable[str] = (),
    excluded_logic_hashes: Iterable[str] = (),
) -> list[RuleDefinition]:
    """Generate exactly the preregistered budget from a finite grammar.

    The function accepts no data, outcomes, or scores.  It can only exclude
    archived logic hashes supplied by an earlier generation, so this generation
    cannot tune its grammar after looking at its own result.
    """
    available = _eligible_grammar_candidates(config, excluded_semantic_hashes, excluded_logic_hashes)
    if config.candidate_budget > len(available):
        raise ValueError(
            "candidate_budget="
            f"{config.candidate_budget} 超过当前代剩余有限 grammar 容量 {len(available)}；"
            "请降低预算或发布新的 grammar_version，而不是复用已归档逻辑"
        )
    randomizer = random.Random(config.seed)
    selected = randomizer.sample(available, config.candidate_budget)
    definitions: list[RuleDefinition] = []
    for ordinal, (_, item) in enumerate(selected, start=1):
        definition = RuleDefinition(
            id=f"auto_{item.family}",
            version=f"{config.generation_id}.{ordinal:04d}",
            name_zh=f"自动发现 {item.family}",
            expression=deepcopy(item.expression),
            parameters=dict(item.parameters),
        )
        _validate_generated_definition(definition, config)
        definitions.append(definition)
    compiler_hashes = [compile_rule(item).semantic_hash for item in definitions]
    logic_hashes = [discovery_semantic_hash(item) for item in definitions]
    if len(set(compiler_hashes)) != len(compiler_hashes) or len(set(logic_hashes)) != len(logic_hashes):
        raise ValueError("自动发现 grammar 出现重复语义")
    return definitions


def candidate_space_payload(definitions: list[RuleDefinition], config: DiscoveryConfig) -> dict[str, Any]:
    """Return a fully auditable candidate-space artifact for a frozen protocol."""
    records = []
    for definition in definitions:
        compiled = compile_rule(definition)
        records.append(
            {
                "rule_semantic_hash": compiled.semantic_hash,
                "discovery_semantic_hash": discovery_semantic_hash(definition),
                "rule_logic_hash": rule_logic_hash(definition),
                "definition": asdict(definition),
                "ast_nodes": ast_node_count(definition.expression),
                "conditions": condition_count(definition.expression),
                "metric_offsets": list(metric_offsets(definition.expression)),
                "required_indicators": list(compiled.required_indicators),
                "max_lookback": compiled.max_lookback,
            }
        )
    return {
        "schema_version": "auto-discovery-candidate-space/v1",
        "generation_id": config.generation_id,
        "grammar_version": config.grammar_version,
        "candidate_budget": config.candidate_budget,
        "seed": config.seed,
        "outcome_blind": True,
        "candidates": records,
    }


def _write_new_json(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(f"不可变研究产物已存在，拒绝覆盖: {path}")
    write_json(path, dict(payload))


def build_auto_discovery_protocol(
    definitions: list[RuleDefinition],
    symbols: list[str],
    search_config: SearchConfig,
    discovery_config: DiscoveryConfig,
    *,
    search_protocol_id: str,
    data_snapshot: Mapping[str, Any],
    universe_manifest: Path,
    output: Path | None = None,
) -> dict[str, Any]:
    """Freeze grammar, lineage and state lifecycle before screen outcomes exist."""
    _require_frozen_search_boundaries(search_config)
    if not data_snapshot or data_snapshot.get("status") == "not_bound":
        raise ValueError("自动发现协议必须绑定数据快照")
    if len(definitions) != discovery_config.candidate_budget:
        raise ValueError("候选数量必须恰好等于冻结的 candidate_budget")
    space = candidate_space_payload(definitions, discovery_config)
    rule_hashes = [item["rule_semantic_hash"] for item in space["candidates"]]
    lineage_hashes = [item["discovery_semantic_hash"] for item in space["candidates"]]
    logic_hashes = [item["rule_logic_hash"] for item in space["candidates"]]
    if (
        len(set(rule_hashes)) != len(rule_hashes)
        or len(set(lineage_hashes)) != len(lineage_hashes)
        or len(set(logic_hashes)) != len(logic_hashes)
    ):
        raise ValueError("协议候选存在重复 semantic hash")
    if any(offset > 0 for item in space["candidates"] for offset in item["metric_offsets"]):
        raise ValueError("协议候选包含未来 offset")
    if any(item["ast_nodes"] > discovery_config.max_ast_nodes for item in space["candidates"]):
        raise ValueError("协议候选超过 AST 复杂度上限")
    if any(item["conditions"] > discovery_config.max_conditions for item in space["candidates"]):
        raise ValueError("协议候选超过条件复杂度上限")
    identity = {
        "schema_version": AUTO_DISCOVERY_SCHEMA,
        "status": "preregistered",
        "generation": {
            "generation_id": discovery_config.generation_id,
            "parent_generation_id": discovery_config.parent_generation_id,
            "parent_archive_id": discovery_config.parent_archive_id,
            "candidate_budget": discovery_config.candidate_budget,
            "prior_cumulative_candidate_budget": discovery_config.prior_cumulative_candidate_budget,
            "cumulative_candidate_budget": discovery_config.prior_cumulative_candidate_budget + discovery_config.candidate_budget,
            "same_generation_mutation": "forbidden",
            "next_generation_requires_new_validation_window_and_lockbox_boundary": True,
        },
        "grammar": {
            "version": discovery_config.grammar_version,
            "outcome_blind": True,
            "seed": discovery_config.seed,
            "fixed_candidate_budget": discovery_config.candidate_budget,
            "max_ast_nodes": discovery_config.max_ast_nodes,
            "max_conditions": discovery_config.max_conditions,
            "positive_metric_offsets_forbidden": True,
            "semantic_hash_deduplication": "definition_lineage_independent_of_generated_id_version",
            "rule_logic_hash_deduplication": "canonical_expression_with_resolved_parameters_independent_of_id_version_label",
        },
        "candidate_space": space,
        "source_search_protocol_id": search_protocol_id,
        "periods": {
            "research_start": search_config.start.isoformat() if search_config.start else None,
            "validation_start": search_config.out_of_sample_start.isoformat() if search_config.out_of_sample_start else None,
            "research_end": search_config.end.isoformat() if search_config.end else None,
            "final_lockbox_start": search_config.lockbox_start.isoformat() if search_config.lockbox_start else None,
        },
        "validation": {
            "out_of_sample_required": True,
            "point_in_time_universe_required": True,
            "multiple_testing": "fdr_bh_all_candidates_all_groups",
            "cost_stress_multipliers": list(search_config.cost_stress_multipliers),
            "minimum_oos_observations": search_config.min_out_of_sample_observations,
            "multi_horizon_required": search_config.require_multiple_horizons,
            "dedup_jaccard": search_config.dedup_jaccard,
        },
        "revalidation_policy": {
            "revalidation_days": discovery_config.revalidation_days,
            "min_observations": discovery_config.min_revalidation_observations,
            "min_mean_net_excess_return": discovery_config.min_mean_net_excess_return,
            "max_mean_return_drop": discovery_config.max_mean_return_drop,
            "retire_when_ci_lower_nonpositive": discovery_config.retire_when_ci_lower_nonpositive,
            "new_oos_window_required": True,
            "lockbox_must_remain_unread": True,
        },
        "universe_manifest": str(universe_manifest),
        "symbols": sorted(set(symbols)),
        "data_snapshot": deepcopy(dict(data_snapshot)),
        "publication": PUBLICATION_BLOCK,
        "campaign_promotion": "eligible_only_requires_separate_frozen_campaign_and_human_approval",
    }
    protocol_hash = _sha256_identity(identity)
    payload = {
        **identity,
        "protocol_hash": protocol_hash,
        "auto_discovery_protocol_id": "auto_discovery_" + protocol_hash.removeprefix("sha256:")[:24],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if output is not None:
        _write_new_json(output, payload)
    return payload


def _parse_date(value: date | str, *, field: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是 ISO 日期") from exc


def _group_key(group: Mapping[str, Any]) -> tuple[int, str]:
    return int(group["horizon_bars"]), str(group["market_regime"])


def _group_evidence(group: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "horizon_bars": int(group["horizon_bars"]),
        "market_regime": str(group["market_regime"]),
        "mean_net_excess_return": float(group["mean_return"]),
        "sample_size": int(group["sample_size"]),
        "adjusted_p_value": group.get("adjusted_p_value"),
        "multiple_testing_reject": bool(group.get("multiple_testing_reject", False)),
        "confidence_interval": deepcopy(group.get("confidence_interval")),
    }


def _screen_evidence_passes(group: Mapping[str, Any], search_config: SearchConfig) -> bool:
    confidence_interval = group.get("confidence_interval") or {}
    return (
        int(group.get("sample_size", 0)) >= search_config.min_out_of_sample_observations
        and float(group.get("mean_return", 0.0)) > 0.0
        and confidence_interval.get("lower") is not None
        and float(confidence_interval["lower"]) > 0.0
        and bool(group.get("multiple_testing_reject", False))
    )


def _state_evidence(
    state: str,
    passing_groups: list[Mapping[str, Any]],
    detail: Mapping[str, Any],
    search_config: SearchConfig,
) -> dict[str, Any] | None:
    groups = [group for group in passing_groups if str(group.get("market_regime")) == state]
    horizons = sorted({int(group["horizon_bars"]) for group in groups})
    if len(horizons) < search_config.require_multiple_horizons:
        return None
    base_groups = {_group_key(group): group for group in detail.get("statistics", {}).get("groups", [])}
    evidence_groups: list[dict[str, Any]] = []
    pressure: list[dict[str, Any]] = []
    for passing in groups:
        key = _group_key(passing)
        base = base_groups.get(key)
        if base is None or base.get("confidence_interval") is None:
            raise ValueError(f"通过筛选的 {key} 缺少完整置信区间证据")
        if not _screen_evidence_passes(base, search_config):
            raise ValueError(f"通过筛选的 {key} 未满足样本、FDR 或置信区间门槛")
        evidence_groups.append(_group_evidence(base))
        scenarios = []
        for multiplier in search_config.cost_stress_multipliers:
            stress_group = next(
                (
                    item
                    for item in detail.get("stress_statistics", {}).get(str(multiplier), {}).get("groups", [])
                    if _group_key(item) == key
                ),
                None,
            )
            if stress_group is None:
                raise ValueError(f"通过筛选的 {key} 缺少 {multiplier}x 成本压力证据")
            if not _screen_evidence_passes(stress_group, search_config):
                raise ValueError(f"通过筛选的 {key} 未通过 {multiplier}x 成本压力门槛")
            scenarios.append({"multiplier": multiplier, **_group_evidence(stress_group)})
        pressure.append({"horizon_bars": key[0], "scenarios": scenarios})
    best = max(evidence_groups, key=lambda item: (item["mean_net_excess_return"], item["horizon_bars"]))
    return {
        "market_regime": state,
        "best_group": best,
        "groups": sorted(evidence_groups, key=lambda item: item["horizon_bars"]),
        "sample_size": sum(item["sample_size"] for item in evidence_groups),
        "fdr": {
            "method": "fdr_bh",
            "scope": "all_candidates_all_groups",
            "adjusted_p_values": [item["adjusted_p_value"] for item in evidence_groups],
        },
        "confidence_intervals": [item["confidence_interval"] for item in evidence_groups],
        "cost_pressure": {"all_scenarios_recorded": True, "by_horizon": pressure},
        "multi_period_evidence": {
            "required_horizons": search_config.require_multiple_horizons,
            "passing_horizons": horizons,
            "passed": True,
        },
    }


def _canonical_json(value: Any) -> str:
    return json.dumps(_canonical(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_json_default)


def _rule_definition_from_payload(value: Any, *, label: str) -> RuleDefinition:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} 必须是 RuleDefinition 对象")
    try:
        return RuleDefinition(**dict(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 不是有效 RuleDefinition") from exc


def _search_protocol_identity(protocol: Mapping[str, Any]) -> dict[str, Any]:
    return {key: deepcopy(value) for key, value in protocol.items() if key not in {"search_id", "created_at"}}


def _verify_search_protocol_integrity(protocol: Mapping[str, Any]) -> str:
    identity = _search_protocol_identity(protocol)
    digest = sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    expected_id = "search_" + digest[:24]
    if protocol.get("search_id") != expected_id:
        raise ValueError("search_protocol search_id 与冻结内容哈希不一致")
    return expected_id


def _auto_protocol_identity(protocol: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(value)
        for key, value in protocol.items()
        if key not in {"protocol_hash", "auto_discovery_protocol_id", "created_at"}
    }


def _verify_auto_protocol_integrity(protocol: Mapping[str, Any]) -> str:
    expected_hash = _sha256_identity(_auto_protocol_identity(protocol))
    if protocol.get("protocol_hash") != expected_hash:
        raise ValueError("auto discovery protocol_hash 与冻结内容不一致")
    expected_id = "auto_discovery_" + expected_hash.removeprefix("sha256:")[:24]
    if protocol.get("auto_discovery_protocol_id") != expected_id:
        raise ValueError("auto discovery protocol id 与 protocol_hash 不一致")
    return expected_hash


def _validated_preregistered_candidates(
    search_protocol: Mapping[str, Any],
    auto_discovery_protocol: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    """Validate both frozen protocols and return their shared candidate mapping."""
    search_id = _verify_search_protocol_integrity(search_protocol)
    _verify_auto_protocol_integrity(auto_discovery_protocol)
    if auto_discovery_protocol.get("source_search_protocol_id") != search_id:
        raise ValueError("自动发现协议与搜索协议身份不匹配")
    if _canonical_json(search_protocol.get("data_snapshot")) != _canonical_json(auto_discovery_protocol.get("data_snapshot")):
        raise ValueError("自动发现协议与搜索协议的数据快照不完全一致")
    if search_protocol.get("universe_manifest") != auto_discovery_protocol.get("universe_manifest"):
        raise ValueError("自动发现协议与搜索协议的股票池 manifest 不一致")
    if search_protocol.get("symbols") != auto_discovery_protocol.get("symbols"):
        raise ValueError("自动发现协议与搜索协议的股票池证券列表不一致")
    if search_protocol.get("periods") != auto_discovery_protocol.get("periods"):
        raise ValueError("自动发现协议与搜索协议时间边界不匹配")

    space = auto_discovery_protocol.get("candidate_space", {})
    if space.get("schema_version") != "auto-discovery-candidate-space/v1":
        raise ValueError("自动发现候选空间 schema 不受支持")
    records = space.get("candidates")
    if not isinstance(records, list):
        raise ValueError("自动发现候选空间缺少 candidates")
    expected: dict[str, Mapping[str, Any]] = {}
    preregistered_definitions: list[str] = []
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise ValueError("自动发现候选空间含有非法候选记录")
        definition = _rule_definition_from_payload(record.get("definition"), label=f"candidate_space.candidates[{index}].definition")
        compiled = compile_rule(definition)
        semantic_hash = str(record.get("rule_semantic_hash", ""))
        if not semantic_hash or compiled.semantic_hash != semantic_hash:
            raise ValueError("预登记候选的正式 semantic_hash 与 definition 不一致")
        if discovery_semantic_hash(definition) != record.get("discovery_semantic_hash"):
            raise ValueError("预登记候选的跨代 semantic_hash 与 definition 不一致")
        stored_logic_hash = record.get("rule_logic_hash")
        if stored_logic_hash is not None and rule_logic_hash(definition) != stored_logic_hash:
            raise ValueError("预登记候选的 rule_logic_hash 与 definition 不一致")
        if int(record.get("ast_nodes", -1)) != ast_node_count(definition.expression):
            raise ValueError("预登记候选 AST 计数与 definition 不一致")
        if int(record.get("conditions", -1)) != condition_count(definition.expression):
            raise ValueError("预登记候选条件计数与 definition 不一致")
        if list(record.get("metric_offsets", [])) != list(metric_offsets(definition.expression)):
            raise ValueError("预登记候选 offset 记录与 definition 不一致")
        if list(record.get("required_indicators", [])) != list(compiled.required_indicators):
            raise ValueError("预登记候选指标依赖与 definition 不一致")
        if int(record.get("max_lookback", -1)) != compiled.max_lookback:
            raise ValueError("预登记候选 lookback 与 definition 不一致")
        if semantic_hash in expected:
            raise ValueError("预登记候选空间出现重复正式 semantic_hash")
        expected[semantic_hash] = record
        preregistered_definitions.append(_canonical_json(record["definition"]))
    search_definitions = search_protocol.get("candidates")
    if not isinstance(search_definitions, list) or [_canonical_json(item) for item in search_definitions] != preregistered_definitions:
        raise ValueError("搜索协议候选定义与自动发现预登记候选空间不完全一致")
    return expected


def _validate_screen_candidate_detail(
    semantic_hash: str,
    detail: Mapping[str, Any],
    expected: Mapping[str, Any],
    search_id: str,
) -> None:
    if detail.get("semantic_hash") != semantic_hash:
        raise ValueError("candidate detail semantic_hash 与预登记候选不一致")
    if detail.get("search_id") != search_id:
        raise ValueError("candidate detail search_id 与冻结搜索协议不一致")
    if _canonical_json(detail.get("definition")) != _canonical_json(expected["definition"]):
        raise ValueError("candidate detail definition 与预登记候选不一致")
    definition = _rule_definition_from_payload(detail.get("definition"), label="candidate detail definition")
    if compile_rule(definition).semantic_hash != semantic_hash:
        raise ValueError("candidate detail definition 的正式 semantic_hash 不一致")


def _registry_origin_identity(registry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(value)
        for key, value in registry.items()
        if key
        not in {
            "origin_registry_hash",
            "registry_id",
            "registry_hash",
            "registry_state_id",
            "previous_registry_hash",
            "lifecycle_revision",
            "last_lifecycle_check_at",
            "created_at",
        }
    }


def _registry_state_identity(registry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(value)
        for key, value in registry.items()
        if key not in {"registry_hash", "registry_state_id", "created_at"}
    }


def _refresh_registry_state_hash(registry: dict[str, Any]) -> None:
    state_hash = _sha256_identity(_registry_state_identity(registry))
    registry["registry_hash"] = state_hash
    registry["registry_state_id"] = "regime_registry_state_" + state_hash.removeprefix("sha256:")[:24]


def _verify_registry_state_hash(registry: Mapping[str, Any]) -> None:
    expected_hash = _sha256_identity(_registry_state_identity(registry))
    if registry.get("registry_hash") != expected_hash:
        raise ValueError("状态注册表内容与 registry_hash 不一致")
    expected_state_id = "regime_registry_state_" + expected_hash.removeprefix("sha256:")[:24]
    if registry.get("registry_state_id") != expected_state_id:
        raise ValueError("状态注册表 registry_state_id 与 registry_hash 不一致")


def build_regime_candidate_registry(
    round_payload: Mapping[str, Any],
    search_protocol: Mapping[str, Any],
    auto_discovery_protocol: Mapping[str, Any],
    search_config: SearchConfig,
    *,
    screened_at: date,
    candidate_records: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Turn screen evidence into an isolated, state-specific research registry."""
    if round_payload.get("schema_version") != "rule-search-round/v1":
        raise ValueError("round payload schema 不受支持")
    if search_protocol.get("schema_version") != "rule-search-protocol/v1":
        raise ValueError("search protocol schema 不受支持")
    if auto_discovery_protocol.get("schema_version") != AUTO_DISCOVERY_SCHEMA:
        raise ValueError("auto discovery protocol schema 不受支持")
    _require_frozen_search_boundaries(search_config)
    if not search_protocol.get("universe_manifest"):
        raise ValueError("状态注册表必须关联点时股票池 manifest")
    if search_protocol.get("data_snapshot", {}).get("status") == "not_bound":
        raise ValueError("状态注册表拒绝未绑定数据快照的搜索协议")
    if search_protocol.get("multiple_testing", {}).get("method") != "fdr_bh":
        raise ValueError("状态注册表要求 FDR-BH 搜索协议")
    expected = _validated_preregistered_candidates(search_protocol, auto_discovery_protocol)
    search_id = _verify_search_protocol_integrity(search_protocol)
    if round_payload.get("search_id") != search_id:
        raise ValueError("筛选轮次与搜索协议身份不匹配")
    ledger = list(round_payload.get("candidates", []))
    actual_hashes = [str(item.get("semantic_hash")) for item in ledger]
    if len(actual_hashes) != len(set(actual_hashes)) or set(actual_hashes) != set(expected):
        raise ValueError("round 候选与预登记候选空间不完全一致")
    for item in ledger:
        semantic_hash = str(item["semantic_hash"])
        if _canonical_json(item.get("definition")) != _canonical_json(expected[semantic_hash]["definition"]):
            raise ValueError("round candidate definition 与预登记候选不一致")
        definition = _rule_definition_from_payload(item.get("definition"), label="round candidate definition")
        if compile_rule(definition).semantic_hash != semantic_hash:
            raise ValueError("round candidate definition 的正式 semantic_hash 不一致")
    for detail_hash, detail in candidate_records.items():
        semantic_hash = str(detail_hash)
        if semantic_hash not in expected:
            raise ValueError("candidate detail 包含未预登记候选")
        if not isinstance(detail, Mapping):
            raise ValueError("candidate detail 必须是对象")
        _validate_screen_candidate_detail(semantic_hash, detail, expected[semantic_hash], search_id)
    revalidation = auto_discovery_protocol["revalidation_policy"]
    active_candidates = []
    for item in sorted(ledger, key=lambda row: str(row["semantic_hash"])):
        semantic_hash = str(item["semantic_hash"])
        state_records = []
        if item.get("status") == "passed_screen":
            detail = candidate_records.get(semantic_hash)
            if detail is None:
                raise ValueError(f"通过候选缺少详细筛选记录: {semantic_hash}")
            for state in sorted(REGIMES):
                evidence = _state_evidence(state, list(item.get("passing_groups", [])), detail, search_config)
                if evidence is None:
                    continue
                valid_until = screened_at + timedelta(days=int(revalidation["revalidation_days"]))
                state_records.append(
                    {
                        "market_regime": state,
                        "status": "active",
                        "validated_at": screened_at.isoformat(),
                        "revalidation_due": valid_until.isoformat(),
                        "valid_until": valid_until.isoformat(),
                        "retired_at": None,
                        "retirement_reason": None,
                        "drift_history": [],
                        "evidence": evidence,
                    }
                )
        eligible = bool(state_records)
        active_candidates.append(
            {
                "rule_semantic_hash": semantic_hash,
                "discovery_semantic_hash": expected[semantic_hash]["discovery_semantic_hash"],
                "rule_logic_hash": rule_logic_hash(_rule_definition_from_payload(expected[semantic_hash]["definition"], label="preregistered candidate definition")),
                "definition": deepcopy(expected[semantic_hash]["definition"]),
                "definition_source": "preregistered_candidate_space",
                "screen_status": item.get("status"),
                "screen_rejection_reason": item.get("rejection_reason"),
                "signals": item.get("signals"),
                "eligible_for_frozen_campaign": eligible,
                "promotion_status": "requires_human_review_before_frozen_campaign" if eligible else "not_eligible",
                "approval": {
                    "status": "not_approved",
                    "automatic_approval": False,
                    "required_for_campaign": True,
                },
                "publication": {"status": PUBLICATION_BLOCK, "automatic_execution": False},
                "states": state_records,
            }
        )
    payload: dict[str, Any] = {
        "schema_version": REGISTRY_SCHEMA,
        "auto_discovery_protocol_id": auto_discovery_protocol["auto_discovery_protocol_id"],
        "auto_discovery_protocol_hash": auto_discovery_protocol["protocol_hash"],
        "source_search_id": search_id,
        "generation": deepcopy(auto_discovery_protocol["generation"]),
        "periods": deepcopy(auto_discovery_protocol["periods"]),
        "screened_at": screened_at.isoformat(),
        "revalidation_policy": deepcopy(revalidation),
        "selection_policy": {
            "only_exact_current_regime": True,
            "requires_active_unexpired_state": True,
            "research_selection_only": True,
            "execution_authorization": "never_granted_by_registry",
        },
        "candidates": active_candidates,
        "publication": PUBLICATION_BLOCK,
        "disclaimer": "状态候选注册表仅用于研究审阅；不得自动发布、下单或宣称盈利保证。",
    }
    origin_registry_hash = _sha256_identity(_registry_origin_identity(payload))
    payload["origin_registry_hash"] = origin_registry_hash
    payload["registry_id"] = "regime_registry_" + origin_registry_hash.removeprefix("sha256:")[:24]
    payload["lifecycle_revision"] = 0
    _refresh_registry_state_hash(payload)
    payload["created_at"] = datetime.now(timezone.utc).isoformat()
    return payload


def select_current_regime_candidates(
    registry: Mapping[str, Any],
    current_regime: str,
    as_of: date,
) -> list[dict[str, Any]]:
    """Return active evidence for one exact regime, never an execution approval."""
    if registry.get("schema_version") != REGISTRY_SCHEMA:
        raise ValueError("状态候选注册表 schema 不受支持")
    _verify_registry_state_hash(registry)
    if current_regime not in REGIMES:
        raise ValueError("current_regime 必须为 bullish、bearish 或 unknown")
    selected: list[dict[str, Any]] = []
    for candidate in registry.get("candidates", []):
        if not candidate.get("eligible_for_frozen_campaign", False):
            continue
        for state in candidate.get("states", []):
            if state.get("market_regime") != current_regime or state.get("status") != "active":
                continue
            if as_of >= _parse_date(state["valid_until"], field="valid_until"):
                continue
            selected.append(
                {
                    "rule_semantic_hash": candidate["rule_semantic_hash"],
                    "discovery_semantic_hash": candidate["discovery_semantic_hash"],
                    "rule_logic_hash": candidate.get("rule_logic_hash"),
                    "definition": deepcopy(candidate["definition"]),
                    "market_regime": current_regime,
                    "validated_at": state["validated_at"],
                    "revalidation_due": state["revalidation_due"],
                    "evidence": deepcopy(state["evidence"]),
                    "eligible_for_frozen_campaign": True,
                    "promotion_status": candidate["promotion_status"],
                    "approval_status": candidate["approval"]["status"],
                    "execution_authorization": "blocked",
                    "publication": PUBLICATION_BLOCK,
                }
            )
    return sorted(
        selected,
        key=lambda item: (
            -float(item["evidence"]["best_group"]["mean_net_excess_return"]),
            item["rule_semantic_hash"],
        ),
    )


def verify_regime_candidate_registry(registry: Mapping[str, Any]) -> dict[str, Any]:
    """Verify the mutable registry-state seal before any promotion decision."""
    if registry.get("schema_version") != REGISTRY_SCHEMA:
        raise ValueError("状态候选注册表 schema 不受支持")
    _verify_registry_state_hash(registry)
    return {
        "status": "valid",
        "registry_id": registry.get("registry_id"),
        "registry_origin_hash": registry.get("origin_registry_hash"),
        "registry_state_hash": registry.get("registry_hash"),
        "lifecycle_revision": registry.get("lifecycle_revision"),
    }


def rule_logic_reference(
    definition: RuleDefinition,
    *,
    source_kind: str,
    source_id: str,
    disposition: str = "known_rule",
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Make an auditable ID-independent duplicate reference for one rule."""
    compiled = compile_rule(definition)
    return {
        "source_kind": source_kind,
        "source_id": source_id,
        "disposition": disposition,
        "rule_logic_hash": rule_logic_hash(definition),
        "rule_semantic_hash": compiled.semantic_hash,
        "definition": asdict(definition),
        "metadata": deepcopy(dict(metadata or {})),
    }


def _verified_logic_reference(reference: Mapping[str, Any]) -> dict[str, Any]:
    definition = _rule_definition_from_payload(reference.get("definition"), label="logic duplicate reference definition")
    compiled = compile_rule(definition)
    logic_hash = rule_logic_hash(definition)
    if reference.get("rule_logic_hash") != logic_hash:
        raise ValueError("logic duplicate reference 的 rule_logic_hash 与定义不一致")
    if reference.get("rule_semantic_hash") not in (None, compiled.semantic_hash):
        raise ValueError("logic duplicate reference 的 rule_semantic_hash 与定义不一致")
    source_kind = str(reference.get("source_kind", ""))
    source_id = str(reference.get("source_id", ""))
    if not source_kind or not source_id:
        raise ValueError("logic duplicate reference 缺少 source_kind/source_id")
    return {
        "source_kind": source_kind,
        "source_id": source_id,
        "disposition": str(reference.get("disposition", "known_rule")),
        "rule_logic_hash": logic_hash,
        "rule_semantic_hash": compiled.semantic_hash,
        "definition_hash": _sha256_identity(asdict(definition)),
        "metadata": deepcopy(dict(reference.get("metadata") or {})),
    }


def archive_catalog_or_historical_duplicates(
    registry: Mapping[str, Any],
    *,
    catalog_references: Iterable[Mapping[str, Any]] = (),
    historical_trial_references: Iterable[Mapping[str, Any]] = (),
    as_of: date,
) -> dict[str, Any]:
    """Archive candidate logic already represented by catalog or past trials.

    A rejected historical trial is terminal for the same canonical logic:
    candidates receive ``historical_duplicate`` plus ``archived_negative`` and
    cannot consume another frozen-Campaign trial merely by changing id/version.
    Catalog-only duplicates are also removed from the "new lead" pool, but are
    not mislabeled as negative evidence.
    """
    verify_regime_candidate_registry(registry)
    catalog = [_verified_logic_reference(item) for item in catalog_references]
    historical = [_verified_logic_reference(item) for item in historical_trial_references]
    catalog_by_logic: dict[str, list[dict[str, Any]]] = defaultdict(list)
    historical_by_logic: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in catalog:
        catalog_by_logic[item["rule_logic_hash"]].append(item)
    for item in historical:
        historical_by_logic[item["rule_logic_hash"]].append(item)

    result = deepcopy(dict(registry))
    audit_rows: list[dict[str, Any]] = []
    for candidate in result.get("candidates", []):
        definition = _rule_definition_from_payload(candidate.get("definition"), label="registry candidate definition")
        logic_hash = rule_logic_hash(definition)
        candidate["rule_logic_hash"] = logic_hash
        catalog_matches = deepcopy(catalog_by_logic.get(logic_hash, []))
        historical_matches = deepcopy(historical_by_logic.get(logic_hash, []))
        negative_matches = [
            item
            for item in historical_matches
            if item["disposition"] in {"reject_publication", "archived_negative", "rejected"}
        ]
        status = "no_known_duplicate"
        if negative_matches:
            status = "historical_duplicate"
            candidate["historical_deduplication"] = {
                "status": "historical_duplicate",
                "archive_status": "archived_negative",
                "reason": "logic_equivalent_to_historical_rejected_trial",
                "matched_references": negative_matches,
                "archived_at": as_of.isoformat(),
            }
            candidate["archive_status"] = "archived_negative"
            candidate["eligible_for_frozen_campaign"] = False
            candidate["promotion_status"] = "historical_duplicate_archived_negative"
            for state in candidate.get("states", []):
                if state.get("status") != "retired":
                    state["status"] = "retired"
                    state["retired_at"] = as_of.isoformat()
                    state["retirement_reason"] = "historical_duplicate_archived_negative"
                    state.setdefault("archive_history", []).append(
                        {"as_of": as_of.isoformat(), "reason": "historical_duplicate_archived_negative"}
                    )
        elif catalog_matches:
            status = "catalog_duplicate"
            candidate["catalog_deduplication"] = {
                "status": "catalog_duplicate",
                "reason": "logic_equivalent_to_registered_catalog_rule",
                "matched_references": catalog_matches,
                "archived_at": as_of.isoformat(),
            }
            candidate["archive_status"] = "archived_catalog_duplicate"
            candidate["eligible_for_frozen_campaign"] = False
            candidate["promotion_status"] = "catalog_duplicate_not_new_lead"
            for state in candidate.get("states", []):
                if state.get("status") != "retired":
                    state["status"] = "retired"
                    state["retired_at"] = as_of.isoformat()
                    state["retirement_reason"] = "catalog_duplicate_not_new_lead"
                    state.setdefault("archive_history", []).append(
                        {"as_of": as_of.isoformat(), "reason": "catalog_duplicate_not_new_lead"}
                    )
        candidate["logic_deduplication"] = {
            "status": status,
            "rule_logic_hash": logic_hash,
            "catalog_matches": catalog_matches,
            "historical_trial_matches": historical_matches,
        }
        if catalog_matches or historical_matches:
            audit_rows.append(
                {
                    "rule_semantic_hash": candidate["rule_semantic_hash"],
                    "rule_logic_hash": logic_hash,
                    "status": status,
                    "catalog_match_count": len(catalog_matches),
                    "historical_trial_match_count": len(historical_matches),
                }
            )
    result.setdefault("logic_deduplication_history", []).append(
        {
            "as_of": as_of.isoformat(),
            "catalog_reference_count": len(catalog),
            "historical_trial_reference_count": len(historical),
            "matches": sorted(audit_rows, key=lambda item: item["rule_semantic_hash"]),
            "automatic_campaign_execution": False,
            "publication": PUBLICATION_BLOCK,
        }
    )
    result["previous_registry_hash"] = registry["registry_hash"]
    result["lifecycle_revision"] = int(registry.get("lifecycle_revision", 0)) + 1
    result["last_lifecycle_check_at"] = as_of.isoformat()
    _refresh_registry_state_hash(result)
    return result


def _retire_state(state: dict[str, Any], *, reason: str, as_of: date, evidence: Mapping[str, Any] | None = None) -> None:
    if state.get("status") == "retired":
        return
    state["status"] = "retired"
    state["retired_at"] = as_of.isoformat()
    state["retirement_reason"] = reason
    if evidence is not None:
        state.setdefault("drift_history", []).append(deepcopy(dict(evidence)))


def retire_expired_or_drifted(
    registry: Mapping[str, Any],
    *,
    as_of: date,
    revalidation_results: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Retire stale/drifted state evidence using only a new, non-lockbox window.

    A valid revalidation can extend a state's validity.  Insufficient or
    non-significant revalidation cannot silently keep it selectable.
    """
    if registry.get("schema_version") != REGISTRY_SCHEMA:
        raise ValueError("状态候选注册表 schema 不受支持")
    _verify_registry_state_hash(registry)
    result = deepcopy(dict(registry))
    policy = result["revalidation_policy"]
    lockbox_start = _parse_date(result["periods"]["final_lockbox_start"], field="final_lockbox_start")
    by_hash = {str(key): value for key, value in (revalidation_results or {}).items()}
    for candidate in result.get("candidates", []):
        if candidate.get("historical_deduplication", {}).get("status") == "historical_duplicate":
            candidate["eligible_for_frozen_campaign"] = False
            candidate["promotion_status"] = "historical_duplicate_archived_negative"
            continue
        if candidate.get("catalog_deduplication", {}).get("status") == "catalog_duplicate":
            candidate["eligible_for_frozen_campaign"] = False
            candidate["promotion_status"] = "catalog_duplicate_not_new_lead"
            continue
        semantic_hash = candidate["rule_semantic_hash"]
        supplied = by_hash.get(semantic_hash)
        for state in candidate.get("states", []):
            if state.get("status") == "retired":
                continue
            if as_of >= _parse_date(state["valid_until"], field="valid_until"):
                _retire_state(state, reason="expired", as_of=as_of)
                continue
            if supplied is None:
                continue
            supplied_state = str(supplied.get("market_regime", ""))
            if supplied_state != state["market_regime"]:
                # Evidence for a different state must never retire or refresh
                # this state-specific registration.
                continue
            if not supplied.get("validation_window_id") or supplied.get("is_new_oos") is not True:
                raise ValueError("重验证必须提供 validation_window_id 且明确 is_new_oos=true")
            validation_end = _parse_date(supplied.get("validation_end"), field="validation_end")
            if validation_end > as_of:
                raise ValueError("重验证结束日期不能晚于 as_of")
            if validation_end <= _parse_date(state["validated_at"], field="validated_at"):
                raise ValueError("重验证必须使用上次 validated_at 之后的新样本外窗口")
            if validation_end >= lockbox_start:
                raise ValueError("重验证不得读取最终锁箱")
            observations = int(supplied.get("sample_size", 0))
            if observations < int(policy["min_observations"]):
                state["status"] = "needs_revalidation"
                state["revalidation_status"] = "insufficient_sample"
                state.setdefault("drift_history", []).append({"as_of": as_of.isoformat(), "status": "insufficient_sample", "evidence": deepcopy(dict(supplied))})
                continue
            mean_return = float(supplied.get("mean_net_excess_return"))
            confidence_interval = supplied.get("confidence_interval") or {}
            lower = confidence_interval.get("lower")
            baseline = float(state["evidence"]["best_group"]["mean_net_excess_return"])
            fdr_ok = bool(supplied.get("multiple_testing_reject", False))
            drift = (
                mean_return < float(policy["min_mean_net_excess_return"])
                or baseline - mean_return > float(policy["max_mean_return_drop"])
                or (bool(policy["retire_when_ci_lower_nonpositive"]) and (lower is None or float(lower) <= 0.0))
                or not fdr_ok
            )
            state.setdefault("drift_history", []).append({"as_of": as_of.isoformat(), "status": "drifted" if drift else "revalidated", "evidence": deepcopy(dict(supplied))})
            if drift:
                _retire_state(state, reason="drift_triggered", as_of=as_of)
                continue
            valid_until = validation_end + timedelta(days=int(policy["revalidation_days"]))
            state["status"] = "active"
            state["validated_at"] = validation_end.isoformat()
            state["revalidation_due"] = valid_until.isoformat()
            state["valid_until"] = valid_until.isoformat()
            state["revalidation_status"] = "passed"
        if candidate.get("states") and not any(state.get("status") == "active" for state in candidate["states"]):
            candidate["eligible_for_frozen_campaign"] = False
            candidate["promotion_status"] = "retired_or_revalidation_required"
        elif any(state.get("status") == "active" for state in candidate.get("states", [])):
            candidate["eligible_for_frozen_campaign"] = True
            candidate["promotion_status"] = "requires_human_review_before_frozen_campaign"
    result["previous_registry_hash"] = registry["registry_hash"]
    result["lifecycle_revision"] = int(registry.get("lifecycle_revision", 0)) + 1
    result["last_lifecycle_check_at"] = as_of.isoformat()
    _refresh_registry_state_hash(result)
    return result


def archived_discovery_semantic_hashes(registry: Mapping[str, Any]) -> set[str]:
    """Extract cross-generation logic hashes from an archived registry."""
    if registry.get("schema_version") != REGISTRY_SCHEMA:
        raise ValueError("状态候选注册表 schema 不受支持")
    _verify_registry_state_hash(registry)
    return {str(item["discovery_semantic_hash"]) for item in registry.get("candidates", [])}


def _trial_ledger(
    round_payload: Mapping[str, Any],
    auto_protocol: Mapping[str, Any],
) -> dict[str, Any]:
    lookup = {
        item["rule_semantic_hash"]: item["discovery_semantic_hash"]
        for item in auto_protocol["candidate_space"]["candidates"]
    }
    trials = []
    for row in sorted(round_payload.get("candidates", []), key=lambda item: str(item["semantic_hash"])):
        trials.append(
            {
                "rule_semantic_hash": row["semantic_hash"],
                "discovery_semantic_hash": lookup[row["semantic_hash"]],
                "rule_id": row["rule_id"],
                "version": row["version"],
                "status": row["status"],
                "rejection_reason": row.get("rejection_reason"),
                "signals": row.get("signals"),
                "outcomes_oos": row.get("outcomes_oos"),
                "passing_groups": deepcopy(row.get("passing_groups", [])),
            }
        )
    return {
        "schema_version": TRIAL_LEDGER_SCHEMA,
        "auto_discovery_protocol_id": auto_protocol["auto_discovery_protocol_id"],
        "source_search_id": auto_protocol["source_search_protocol_id"],
        "generation": deepcopy(auto_protocol["generation"]),
        "trials": trials,
        "publication": PUBLICATION_BLOCK,
    }


def _report(registry: Mapping[str, Any], *, as_of: date) -> str:
    lines = [
        "# 自动发现状态候选报告",
        "",
        f"- 自动发现协议：`{registry['auto_discovery_protocol_id']}`",
        f"- 搜索协议：`{registry['source_search_id']}`",
        f"- 代次：`{registry['generation']['generation_id']}`",
        f"- 筛选日期：`{registry['screened_at']}`",
        f"- 当前选择视角：`{as_of}`",
        "",
        "## 状态专属候选",
        "",
        "| 规则语义 | 市场状态 | 最佳周期 | 样本 | FDR p | 均值净超额 | 到期/复验日 |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    rows = []
    for candidate in registry["candidates"]:
        for state in candidate["states"]:
            if state["status"] != "active":
                continue
            best = state["evidence"]["best_group"]
            rows.append((candidate["rule_semantic_hash"], state, best))
    for semantic_hash, state, best in sorted(rows):
        lines.append(
            "| {hash} | {regime} | {horizon} | {sample} | {fdr:.4g} | {mean:.4%} | {until} |".format(
                hash=semantic_hash,
                regime=state["market_regime"],
                horizon=best["horizon_bars"],
                sample=best["sample_size"],
                fdr=float(best["adjusted_p_value"]),
                mean=float(best["mean_net_excess_return"]),
                until=state["valid_until"],
            )
        )
    if not rows:
        lines.append("| - | - | - | - | - | - | - |")
    lines.extend(
        [
            "",
            "## 强制门禁",
            "",
            "- 本表仅为研究筛选；即使标记为 eligible_for_frozen_campaign，也不代表规则已批准、已发布或可交易。",
            "- 每个市场状态必须保留样本量、FDR、置信区间、成本压力和多周期证据；状态改变、到期或漂移均使其退出当前选择。",
            "- 晋升仍需新的冻结 Campaign、最终锁箱和人工审批。本系统不会自动下单或保证盈利。",
            "",
        ]
    )
    return "\n".join(lines)


def run_auto_discovery(
    source: LocalParquetMarketData,
    symbols: list[str],
    search_config: SearchConfig,
    discovery_config: DiscoveryConfig,
    output_root: Path,
    *,
    universe_manifest: Path,
    excluded_semantic_hashes: Iterable[str] = (),
    excluded_logic_hashes: Iterable[str] = (),
) -> dict[str, Any]:
    """Run one immutable generation: preregister -> screen -> registry -> report."""
    if output_root.exists():
        raise FileExistsError(f"自动发现输出目录已存在，拒绝重跑: {output_root}")
    if search_config.end is None:
        raise ValueError("自动发现必须冻结 search_config.end 作为筛选日期")
    definitions = generate_candidates(
        discovery_config,
        excluded_semantic_hashes=excluded_semantic_hashes,
        excluded_logic_hashes=excluded_logic_hashes,
    )
    data_snapshot = build_search_data_snapshot(source, symbols, search_config, universe_manifest=universe_manifest)
    search_protocol = build_search_protocol(
        definitions,
        symbols,
        search_config,
        output_root,
        universe_manifest=universe_manifest,
        data_snapshot=data_snapshot,
    )
    auto_protocol = build_auto_discovery_protocol(
        definitions,
        symbols,
        search_config,
        discovery_config,
        search_protocol_id=search_protocol["search_id"],
        data_snapshot=data_snapshot,
        universe_manifest=universe_manifest,
        output=output_root / "auto_discovery_protocol.json",
    )
    _write_new_json(output_root / "candidate_space.json", candidate_space_payload(definitions, discovery_config))
    summary = screen_candidates(
        source,
        symbols,
        definitions,
        search_config,
        output_root,
        universe_manifest=universe_manifest,
        search_protocol_id=search_protocol["search_id"],
    )
    round_payload = json.loads((output_root / "round.json").read_text(encoding="utf-8"))
    candidate_records = {
        row["semantic_hash"]: json.loads(
            (output_root / "candidates" / f"{row['semantic_hash'].removeprefix('sha256:')[:16]}.json").read_text(encoding="utf-8")
        )
        for row in round_payload["candidates"]
    }
    trial_ledger = _trial_ledger(round_payload, auto_protocol)
    _write_new_json(output_root / "trial_ledger.json", trial_ledger)
    registry = build_regime_candidate_registry(
        round_payload,
        search_protocol,
        auto_protocol,
        search_config,
        screened_at=search_config.end,
        candidate_records=candidate_records,
    )
    _write_new_json(output_root / "regime_candidate_registry.json", registry)
    (output_root / "report.md").write_text(_report(registry, as_of=search_config.end), encoding="utf-8")
    return {
        "schema_version": "auto-discovery-run/v1",
        "auto_discovery_protocol_id": auto_protocol["auto_discovery_protocol_id"],
        "search_id": search_protocol["search_id"],
        "registry_id": registry["registry_id"],
        "candidates_total": summary["candidates_total"],
        "passed_screen": summary["passed_screen"],
        "eligible_for_frozen_campaign": sum(1 for item in registry["candidates"] if item["eligible_for_frozen_campaign"]),
        "publication": PUBLICATION_BLOCK,
        "output_root": str(output_root),
    }
