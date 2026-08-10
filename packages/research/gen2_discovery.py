"""Gen2 outcome-blind candidate protocol and cross-generation trial governance.

This module deliberately does *not* run a screen or a backtest.  It freezes a
small context-wrapper grammar so that a future, genuinely unseen validation
window can be evaluated by a separate deterministic research pipeline.  The
wrapper keeps cross-series predicates outside the single-series rule DSL.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import random
import re
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from packages.contracts import RuleDefinition
from packages.research.auto_discovery import PUBLICATION_BLOCK
from packages.research.auto_discovery import discovery_semantic_hash
from packages.research.historical_trials import scan_historical_trial_references
from packages.research.json_store import write_json
from packages.rule_dsl import canonical_rule_logic, compile_rule, rule_logic_hash


GEN2_PROTOCOL_SCHEMA = "gen2-discovery-protocol/v1"
GEN2_CANDIDATE_SPACE_SCHEMA = "gen2-candidate-space/v1"
GLOBAL_LEDGER_POLICY_SCHEMA = "global-research-trial-ledger-policy/v1"
GLOBAL_LEDGER_ENTRY_SCHEMA = "global-research-trial-ledger-entry/v1"
GEN2_GRAMMAR_VERSION = "context-wrapper-grammar/v1"
_GENERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_MAX_GEN2_CANDIDATES = 32


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float):
        if not np.isfinite(value):
            raise ValueError("candidate identity cannot contain non-finite number")
        return 0.0 if value == 0 else value
    return value


def canonical_hash(value: Any) -> str:
    payload = json.dumps(_canonical(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + sha256(payload.encode("utf-8")).hexdigest()


def _write_new_json(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(f"不可变研究产物已存在，拒绝覆盖: {path}")
    write_json(path, dict(payload))


@dataclass(frozen=True, slots=True)
class Gen2Config:
    generation_id: str
    parent_generation_id: str
    parent_protocol_hash: str
    candidate_budget: int
    seed: int = 20260810
    grammar_version: str = GEN2_GRAMMAR_VERSION
    benchmark_symbol: str = "000300"

    def __post_init__(self) -> None:
        if not _GENERATION_ID.fullmatch(self.generation_id):
            raise ValueError("generation_id 非法")
        if not _GENERATION_ID.fullmatch(self.parent_generation_id):
            raise ValueError("parent_generation_id 非法")
        if self.generation_id == self.parent_generation_id:
            raise ValueError("generation_id 不能等于 parent_generation_id")
        if not self.parent_protocol_hash.startswith("sha256:"):
            raise ValueError("parent_protocol_hash 必须是 sha256")
        if not 1 <= self.candidate_budget <= _MAX_GEN2_CANDIDATES:
            raise ValueError(f"candidate_budget 必须在 [1, {_MAX_GEN2_CANDIDATES}] 内")
        if not self.grammar_version:
            raise ValueError("grammar_version 不能为空")
        if not self.benchmark_symbol:
            raise ValueError("benchmark_symbol 不能为空")


@dataclass(frozen=True, slots=True)
class Gen2Periods:
    research_start: date
    validation_start: date
    research_end: date
    final_lockbox_start: date

    def __post_init__(self) -> None:
        if not self.research_start < self.validation_start <= self.research_end < self.final_lockbox_start:
            raise ValueError("时间边界必须满足 research_start < validation_start <= research_end < final_lockbox_start")


def _metric(name: str, *, window: int | None = None, offset: int = 0) -> dict[str, Any]:
    value: dict[str, Any] = {"name": name, "offset": offset}
    if window is not None:
        value["window"] = window
    return {"metric": value}


def _param(name: str) -> dict[str, str]:
    return {"param": name}


def _base_rules() -> list[RuleDefinition]:
    """A finite set of base signals; filters supply the Gen2 AND composition."""
    return [
        RuleDefinition(
            id="gen2_rsi_oversold", version="grammar", name_zh="Gen2 RSI 超卖",
            expression={"lt": [_metric("rsi", window=14), _param("threshold")]},
            parameters={"threshold": 30.0},
        ),
        RuleDefinition(
            id="gen2_roc_down", version="grammar", name_zh="Gen2 短期下跌",
            expression={"lt": [_metric("roc", window=5), _param("threshold")]},
            parameters={"threshold": -0.05},
        ),
        RuleDefinition(
            id="gen2_breakdown", version="grammar", name_zh="Gen2 跌破前低",
            expression={"lt": [_metric("close"), _metric("min_low", window=20, offset=-1)]},
            parameters={},
        ),
    ]


def _filter_profiles() -> list[tuple[str, list[dict[str, Any]]]]:
    """Frozen cross-series filters, all observable at the signal bar close.

    ``relative_strength`` uses the security and frozen benchmark returns over
    the same exact dates.  No forward-fill is permitted for benchmark prices.
    """
    return [
        ("bear_reversal", [
            {"kind": "market_regime", "state": "below_sma", "window": 60, "observed_at": "signal_bar_close"},
            {"kind": "relative_strength", "window": 20, "operator": "lte", "threshold": 0.0, "benchmark_symbol": "__FROZEN__"},
            {"kind": "realized_volatility", "window": 20, "operator": "lte", "threshold": 0.06},
            {"kind": "volume_category", "window": 20, "operator": "gte", "multiple": 1.1},
        ]),
        ("bull_continuation", [
            {"kind": "market_regime", "state": "above_sma", "window": 60, "observed_at": "signal_bar_close"},
            {"kind": "relative_strength", "window": 20, "operator": "gte", "threshold": 0.02, "benchmark_symbol": "__FROZEN__"},
            {"kind": "realized_volatility", "window": 20, "operator": "lte", "threshold": 0.05},
            {"kind": "volume_category", "window": 20, "operator": "gte", "multiple": 1.1},
        ]),
        ("bear_high_vol", [
            {"kind": "market_regime", "state": "below_sma", "window": 60, "observed_at": "signal_bar_close"},
            {"kind": "relative_strength", "window": 10, "operator": "lte", "threshold": -0.01, "benchmark_symbol": "__FROZEN__"},
            {"kind": "realized_volatility", "window": 10, "operator": "gte", "threshold": 0.02},
            {"kind": "volume_category", "window": 10, "operator": "gte", "multiple": 1.2},
        ]),
        ("bull_low_vol", [
            {"kind": "market_regime", "state": "above_sma", "window": 60, "observed_at": "signal_bar_close"},
            {"kind": "relative_strength", "window": 10, "operator": "gte", "threshold": 0.0, "benchmark_symbol": "__FROZEN__"},
            {"kind": "realized_volatility", "window": 20, "operator": "lte", "threshold": 0.03},
            {"kind": "volume_category", "window": 20, "operator": "gte", "multiple": 1.0},
        ]),
    ]


def canonical_context_filters(filters: Iterable[Mapping[str, Any]], benchmark_symbol: str) -> list[dict[str, Any]]:
    """Normalize wrapper predicates independently of labels and input order."""
    normalized: list[dict[str, Any]] = []
    for raw in filters:
        if not isinstance(raw, Mapping):
            raise ValueError("Gen2 context filter 必须是对象")
        item = dict(raw)
        if item.get("benchmark_symbol") == "__FROZEN__":
            item["benchmark_symbol"] = benchmark_symbol
        kind = item.get("kind")
        allowed = {
            "market_regime": {"kind", "state", "window", "observed_at"},
            "relative_strength": {"kind", "window", "operator", "threshold", "benchmark_symbol"},
            "realized_volatility": {"kind", "window", "operator", "threshold"},
            "volume_category": {"kind", "window", "operator", "multiple"},
        }
        if kind not in allowed:
            raise ValueError(f"不支持的 Gen2 context filter: {kind}")
        if set(item) != allowed[kind]:
            raise ValueError(f"{kind} 字段必须严格等于 {sorted(allowed[kind])}")
        window = item.get("window")
        if isinstance(window, bool) or not isinstance(window, int) or window < 2:
            raise ValueError("所有 Gen2 context filter 必须有 window >= 2")
        if kind == "market_regime":
            if item.get("state") not in {"above_sma", "below_sma"}:
                raise ValueError("market_regime state 非法")
            if item.get("observed_at") != "signal_bar_close":
                raise ValueError("market_regime 必须在 signal_bar_close 可知")
        elif kind == "relative_strength":
            if item.get("benchmark_symbol") != benchmark_symbol or item.get("operator") not in {"gte", "lte"}:
                raise ValueError("relative_strength 必须引用冻结 benchmark 并指定 gte/lte")
            if isinstance(item["threshold"], bool) or not isinstance(item["threshold"], (int, float)) or not math.isfinite(float(item["threshold"])):
                raise ValueError("relative_strength threshold 必须是有限数值")
        elif kind == "realized_volatility":
            if item.get("operator") not in {"gte", "lte"}:
                raise ValueError("realized_volatility operator 非法")
            if isinstance(item["threshold"], bool) or not isinstance(item["threshold"], (int, float)) or not math.isfinite(float(item["threshold"])) or float(item["threshold"]) < 0:
                raise ValueError("realized_volatility threshold 必须是非负有限数值")
        elif kind == "volume_category":
            if (item.get("operator") != "gte" or isinstance(item["multiple"], bool) or not isinstance(item["multiple"], (int, float))
                    or not math.isfinite(float(item["multiple"])) or float(item["multiple"]) <= 0.0):
                raise ValueError("volume_category 只支持正倍数 gte")
        normalized.append(_canonical(item))
    if len({canonical_hash(item) for item in normalized}) != len(normalized):
        raise ValueError("Gen2 context filters 重复")
    return sorted(normalized, key=lambda item: canonical_hash(item))


def gen2_candidate_semantic_id(base: RuleDefinition, filters: Iterable[Mapping[str, Any]], benchmark_symbol: str) -> str:
    """Canonical wrapper identity: base behavior plus normalized context ANDs."""
    compile_rule(base)
    return canonical_hash({
        "schema_version": "gen2-context-candidate-logic/v1",
        "base_rule_logic": canonical_rule_logic(base),
        "context_filters": canonical_context_filters(filters, benchmark_symbol),
        "signal_observed_at": "bar_close",
        "executable_from": "next_bar_open",
    })


def _candidate_record(base: RuleDefinition, profile: str, filters: list[dict[str, Any]], config: Gen2Config) -> dict[str, Any]:
    frozen_filters = canonical_context_filters(filters, config.benchmark_symbol)
    semantic_id = gen2_candidate_semantic_id(base, frozen_filters, config.benchmark_symbol)
    return {
        "candidate_semantic_id": semantic_id,
        "base_rule_logic_hash": rule_logic_hash(base),
        "base_rule_semantic_hash": compile_rule(base).semantic_hash,
        "base_definition": asdict(base),
        "profile": profile,
        "context_filters": frozen_filters,
        "composition": "base_rule AND every_context_filter",
        "benchmark_symbol": config.benchmark_symbol,
        "outcome_blind": True,
        "evaluation_policy": {
            "signal_confirmed_at": "T close",
            "eligible_execution": "T+1 open",
            "benchmark_alignment": "exact_signal_and_lookback_dates_no_forward_fill",
            "missing_benchmark_or_history": "filter_false",
        },
    }


def generate_gen2_candidates(config: Gen2Config, *, excluded_semantic_ids: Iterable[str] = ()) -> list[dict[str, Any]]:
    excluded = set(excluded_semantic_ids)
    records = [
        _candidate_record(base, profile, filters, config)
        for base in _base_rules()
        for profile, filters in _filter_profiles()
    ]
    unique = {item["candidate_semantic_id"]: item for item in records}
    eligible = [unique[key] for key in sorted(unique) if key not in excluded]
    if config.candidate_budget > len(eligible):
        raise ValueError(f"candidate_budget={config.candidate_budget} 超过 Gen2 grammar 剩余容量 {len(eligible)}")
    return random.Random(config.seed).sample(eligible, config.candidate_budget)


def verify_gen1_protocol(protocol_path: Path) -> dict[str, Any]:
    """Verify a frozen Gen1 protocol before using it as lineage evidence."""
    payload = json.loads(protocol_path.read_text(encoding="utf-8"))
    identity = {key: value for key, value in payload.items() if key not in {"protocol_hash", "auto_discovery_protocol_id", "created_at"}}
    expected_hash = canonical_hash(identity)
    if payload.get("schema_version") != "auto-discovery-protocol/v1" or payload.get("protocol_hash") != expected_hash:
        raise ValueError("Gen1 protocol schema 或 protocol_hash 无效")
    expected_id = "auto_discovery_" + expected_hash.removeprefix("sha256:")[:24]
    if payload.get("auto_discovery_protocol_id") != expected_id:
        raise ValueError("Gen1 protocol id 无效")
    generation, periods = payload.get("generation"), payload.get("periods")
    if not isinstance(generation, Mapping) or not _GENERATION_ID.fullmatch(str(generation.get("generation_id", ""))):
        raise ValueError("Gen1 protocol generation 无效")
    if not isinstance(periods, Mapping):
        raise ValueError("Gen1 protocol periods 缺失")
    required_periods = ("research_start", "validation_start", "research_end", "final_lockbox_start")
    if any(not periods.get(key) for key in required_periods):
        raise ValueError("Gen1 protocol periods 不完整")
    parsed = {key: date.fromisoformat(str(periods[key])) for key in required_periods}
    if not parsed["research_start"] < parsed["validation_start"] <= parsed["research_end"] < parsed["final_lockbox_start"]:
        raise ValueError("Gen1 protocol periods 边界无效")
    candidates = payload.get("candidate_space", {}).get("candidates")
    if not isinstance(candidates, list) or len(candidates) != int(generation.get("candidate_budget", -1)):
        raise ValueError("Gen1 protocol candidate_space 无效")
    seen: set[str] = set()
    for record in candidates:
        if not isinstance(record, Mapping):
            raise ValueError("Gen1 protocol candidate 非法")
        definition = RuleDefinition(**record["definition"])
        compiled = compile_rule(definition)
        if record.get("rule_semantic_hash") != compiled.semantic_hash:
            raise ValueError("Gen1 protocol rule_semantic_hash 无效")
        if "discovery_semantic_hash" in record and record["discovery_semantic_hash"] != discovery_semantic_hash(definition):
            raise ValueError("Gen1 protocol discovery_semantic_hash 无效")
        if "rule_logic_hash" in record and record["rule_logic_hash"] != rule_logic_hash(definition):
            raise ValueError("Gen1 protocol rule_logic_hash 无效")
        if compiled.semantic_hash in seen:
            raise ValueError("Gen1 protocol candidate 重复")
        seen.add(compiled.semantic_hash)
    return payload


def load_gen1_candidate_references(protocol_path: Path) -> list[dict[str, Any]]:
    """Read only a frozen Gen1 protocol; no market data or lockbox is opened."""
    payload = verify_gen1_protocol(protocol_path)
    candidates = payload.get("candidate_space", {}).get("candidates")
    references: list[dict[str, Any]] = []
    for record in candidates:
        definition = RuleDefinition(**record["definition"])
        references.append({
            "source_generation_id": payload["generation"]["generation_id"],
            "source_protocol_hash": payload["protocol_hash"],
            "source_rule_semantic_hash": record["rule_semantic_hash"],
            "base_rule_logic_hash": rule_logic_hash(definition),
            "source": str(protocol_path),
        })
    return references


def _global_policy_path(ledger_root: Path) -> Path:
    return ledger_root / "policy.json"


def historical_trial_inventory(project_root: Path) -> dict[str, Any]:
    """Build a verified, read-only index of every outcome-touched rule artifact."""
    root = project_root.resolve()
    references: list[dict[str, Any]] = []
    for path in sorted((root / "data" / "rule_search").glob("**/round.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            candidates = payload.get("candidates")
            if not isinstance(candidates, list):
                raise ValueError("round candidates 缺失")
            for record in candidates:
                definition = RuleDefinition(**record["definition"])
                compiled = compile_rule(definition)
                if record.get("semantic_hash") != compiled.semantic_hash:
                    raise ValueError("round semantic_hash 无效")
                references.append({"rule_logic_hash": rule_logic_hash(definition), "source_kind": "rule_search_round", "source": str(path.relative_to(root)).replace("\\", "/"), "status": str(record.get("status", "unknown")), "rule_semantic_hash": compiled.semantic_hash})
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"历史 rule_search artifact 无法验证: {path}: {exc}") from exc
    for path in sorted((root / "data" / "auto_discovery").glob("**/trial_ledger.json")):
        try:
            protocol = verify_gen1_protocol(path.with_name("auto_discovery_protocol.json"))
            by_discovery = {item.get("discovery_semantic_hash"): item for item in protocol["candidate_space"]["candidates"]}
            payload = json.loads(path.read_text(encoding="utf-8"))
            trials = payload if isinstance(payload, list) else payload.get("trials")
            if not isinstance(trials, list):
                raise ValueError("trial ledger 不是列表")
            for trial in trials:
                record = by_discovery.get(trial.get("discovery_semantic_hash"))
                if record is None or trial.get("rule_semantic_hash") != record.get("rule_semantic_hash"):
                    raise ValueError("trial ledger 与冻结 protocol candidate 不一致")
                definition = RuleDefinition(**record["definition"])
                references.append({"rule_logic_hash": rule_logic_hash(definition), "source_kind": "auto_discovery_trial", "source": str(path.relative_to(root)).replace("\\", "/"), "status": str(trial.get("status", "unknown")), "rule_semantic_hash": record["rule_semantic_hash"]})
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"历史 auto_discovery artifact 无法验证: {path}: {exc}") from exc
    frozen = scan_historical_trial_references(root)
    if frozen["errors"]:
        raise ValueError("历史 frozen campaign artifact 无法验证: " + json.dumps(frozen["errors"], ensure_ascii=False))
    for record in frozen["historical_trial_references"]:
        definition = RuleDefinition(**record["definition"])
        if record.get("rule_logic_hash") != rule_logic_hash(definition) or record.get("rule_semantic_hash") != compile_rule(definition).semantic_hash:
            raise ValueError("历史 frozen campaign logic hash 无效")
        references.append({"rule_logic_hash": record["rule_logic_hash"], "source_kind": "frozen_campaign_adjudication", "source": record["metadata"]["adjudication_path"], "status": record["disposition"], "rule_semantic_hash": record["rule_semantic_hash"]})
    if not references:
        raise ValueError("历史试验 inventory 为空，拒绝静默创建 global budget")
    ordered = sorted(references, key=lambda item: (item["source_kind"], item["source"], item["rule_logic_hash"], item["rule_semantic_hash"]))
    return {"schema_version": "historical-trial-inventory/v1", "market_or_lockbox_data_read": False, "raw_trial_count": len(ordered), "unique_rule_logic_count": len({item["rule_logic_hash"] for item in ordered}), "references": ordered, "inventory_hash": canonical_hash(ordered)}


def initialize_global_trial_ledger(ledger_root: Path, *, global_trial_budget: int, legacy_trial_count: int, legacy_inventory_hash: str) -> dict[str, Any]:
    if global_trial_budget < 1 or legacy_trial_count < 0 or legacy_trial_count > global_trial_budget or not legacy_inventory_hash.startswith("sha256:"):
        raise ValueError("global/legacy trial budget 非法")
    path = _global_policy_path(ledger_root)
    desired = {
        "schema_version": GLOBAL_LEDGER_POLICY_SCHEMA,
        "global_trial_budget": global_trial_budget,
        "legacy_trial_count": legacy_trial_count,
        "legacy_inventory_hash": legacy_inventory_hash,
        "append_only_entries": "entries/<generation_id>.json",
        "final_lockbox_consumption": "forbidden",
    }
    desired["policy_hash"] = canonical_hash(desired)
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != desired:
            raise ValueError("全局试验账本 policy 已冻结且与本次配置不一致")
        return existing
    _write_new_json(path, desired)
    return desired


def load_global_trial_ledger(ledger_root: Path) -> dict[str, Any]:
    policy_path = _global_policy_path(ledger_root)
    if not policy_path.is_file():
        raise FileNotFoundError(f"全局试验账本 policy 不存在: {policy_path}")
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    expected = {key: value for key, value in policy.items() if key != "policy_hash"}
    if policy.get("schema_version") != GLOBAL_LEDGER_POLICY_SCHEMA or policy.get("policy_hash") != canonical_hash(expected):
        raise ValueError("全局试验账本 policy 已损坏")
    entries: list[dict[str, Any]] = []
    directory = ledger_root / "entries"
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        entry = json.loads(path.read_text(encoding="utf-8"))
        identity = {key: value for key, value in entry.items() if key not in {"entry_hash", "recorded_at"}}
        if entry.get("schema_version") != GLOBAL_LEDGER_ENTRY_SCHEMA or entry.get("entry_hash") != canonical_hash(identity):
            raise ValueError(f"全局试验账本 entry 已损坏: {path}")
        entries.append(entry)
    generations = [entry["generation_id"] for entry in entries]
    if len(set(generations)) != len(generations):
        raise ValueError("全局试验账本含有重复 generation_id")
    semantic_ids = [item for entry in entries for item in entry["candidate_semantic_ids"]]
    if len(set(semantic_ids)) != len(semantic_ids):
        raise ValueError("全局试验账本含有重复 candidate semantic id")
    used = int(policy["legacy_trial_count"]) + sum(int(entry["candidate_budget"]) for entry in entries)
    if used > int(policy["global_trial_budget"]):
        raise ValueError("全局试验账本已超过冻结预算")
    return {"policy": policy, "entries": entries, "used_trial_count": used, "remaining_trial_count": int(policy["global_trial_budget"]) - used}


def _require_fresh_window(periods: Gen2Periods, parent_research_end: date) -> None:
    if periods.validation_start <= parent_research_end:
        raise ValueError("Gen2 validation_start 必须晚于父代 research_end；2022–2026 不得标记为 fresh OOS")


def verify_parent_generation_closure(comparison_result_path: Path) -> dict[str, Any]:
    """Validate the eliminated parent result before reassigning its lockbox."""
    result = json.loads(comparison_result_path.read_text(encoding="utf-8"))
    if not isinstance(result, Mapping): raise ValueError("parent closure result must be object")
    identity = {key: value for key, value in result.items() if key not in {"completed_at", "result_hash"}}
    if result.get("result_hash") != canonical_hash(identity): raise ValueError("parent closure result hash invalid")
    if result.get("final_lockbox_read") is not False or result.get("approval") != "forbidden" or result.get("publication") != "forbidden":
        raise ValueError("parent closure lockbox/approval state invalid")
    ranking = result.get("ranking")
    if not isinstance(ranking, list) or not ranking or any(not str(item.get("status", "")).startswith("research_eliminated_") for item in ranking):
        raise ValueError("parent closure has survivor/non-eliminated candidate")
    protocol_path = comparison_result_path.parent / "comparison_protocol.json"
    protocol = json.loads(protocol_path.read_text(encoding="utf-8")) if protocol_path.is_file() else None
    from packages.research.candidate_comparison import verify_comparison_protocol
    protocol_check = verify_comparison_protocol(protocol) if isinstance(protocol, Mapping) else {"status": "invalid"}
    oos = protocol.get("oos") if isinstance(protocol, Mapping) else None
    if protocol_check.get("status") != "valid" or not isinstance(oos, Mapping) or not oos.get("lockbox_start"):
        raise ValueError("parent closure comparison protocol/lockbox missing")
    if result.get("comparison_id") != protocol.get("comparison_id") or result.get("comparison_hash") != protocol.get("comparison_hash"):
        raise ValueError("parent closure result/protocol identity mismatch")
    protocol_ids = sorted(str(item.get("candidate")) for item in protocol.get("candidates", []) if isinstance(item, Mapping))
    result_ids = sorted(str(item.get("candidate")) for item in ranking)
    if protocol_ids != result_ids:
        raise ValueError("parent closure result/protocol candidate mismatch")
    lockbox = date.fromisoformat(str(oos["lockbox_start"]))
    return {"result_hash": result["result_hash"], "source_result": comparison_result_path.name, "parent_lockbox_start": lockbox.isoformat(), "parent_oos": {"start": oos.get("start"), "end": oos.get("end")}, "candidate_ids": result_ids}


def build_gen2_protocol(
    config: Gen2Config,
    periods: Gen2Periods,
    *,
    parent_research_end: date,
    global_ledger: Mapping[str, Any],
    parent_closure: Mapping[str, Any],
    gen1_references: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Freeze an outcome-blind Gen2 candidate set after all fail-closed checks."""
    _require_fresh_window(periods, parent_research_end)
    parent_lockbox = date.fromisoformat(str(parent_closure["parent_lockbox_start"]))
    if periods.validation_start < parent_lockbox or periods.final_lockbox_start <= parent_lockbox:
        raise ValueError("Gen2 rollover validation/new lockbox boundary invalid")
    parent_refs = list(gen1_references)
    if not parent_refs:
        raise ValueError("Gen2 必须提供已验证的父代 candidate references")
    if ({str(item.get("source_generation_id")) for item in parent_refs} != {config.parent_generation_id}
            or {str(item.get("source_protocol_hash")) for item in parent_refs} != {config.parent_protocol_hash}):
        raise ValueError("Gen2 parent generation/protocol hash 与已验证父代 references 不一致")
    policy = global_ledger["policy"]
    if int(global_ledger["remaining_trial_count"]) < config.candidate_budget:
        raise ValueError("全局 trial budget 不足，拒绝创建 Gen2 generation")
    existing = global_ledger["entries"]
    if config.generation_id in {entry["generation_id"] for entry in existing}:
        raise ValueError("generation_id 已在全局账本登记")
    excluded = {semantic for entry in existing for semantic in entry["candidate_semantic_ids"]}
    candidates = generate_gen2_candidates(config, excluded_semantic_ids=excluded)
    gen1_base_hashes = {str(item["base_rule_logic_hash"]) for item in parent_refs}
    for candidate in candidates:
        candidate["cross_generation_deduplication"] = {
            "exact_candidate_already_tested": False,
            "base_logic_previously_tested_in_gen1": candidate["base_rule_logic_hash"] in gen1_base_hashes,
            "decision": "new_contextual_composite" if candidate["base_rule_logic_hash"] in gen1_base_hashes else "new_base_and_contextual_composite",
        }
    identity = {
        "schema_version": GEN2_PROTOCOL_SCHEMA,
        "status": "preregistered_no_screen_run",
        "generation": {
            "generation_id": config.generation_id,
            "parent_generation_id": config.parent_generation_id,
            "parent_protocol_hash": config.parent_protocol_hash,
            "candidate_budget": config.candidate_budget,
            "global_trial_budget": policy["global_trial_budget"],
            "global_ledger_policy_hash": policy["policy_hash"],
            "global_used_before_generation": global_ledger["used_trial_count"],
            "global_used_after_generation": int(global_ledger["used_trial_count"]) + config.candidate_budget,
        },
        "grammar": {"version": config.grammar_version, "seed": config.seed, "benchmark_symbol": config.benchmark_symbol, "outcome_blind": True, "cross_series_wrapper_not_rule_dsl": True},
        "periods": {**asdict(periods), "parent_research_end": parent_research_end.isoformat(), "planned_fresh_oos": True, "final_lockbox_read": False},
        "parent_closure": {**dict(parent_closure), "transition": "unused_parent_lockbox_reassigned_to_child_validation_after_parent_research_closed", "read_at_prereg": False},
        "preregistered_at": datetime.now(timezone.utc).isoformat(),
        "candidate_space": {"schema_version": GEN2_CANDIDATE_SPACE_SCHEMA, "candidates": candidates},
        "governance": {
            "2022_2026_fresh_oos": "forbidden",
            "final_lockbox_consumption": "forbidden",
            "screen_or_backtest_in_this_stage": "forbidden",
            "approval": "forbidden",
            "publication": PUBLICATION_BLOCK,
        },
    }
    protocol_hash = canonical_hash(identity)
    return {**identity, "protocol_hash": protocol_hash, "protocol_id": "gen2_" + protocol_hash.removeprefix("sha256:")[:24], "created_at": datetime.now(timezone.utc).isoformat()}


def verify_gen2_protocol(protocol: Mapping[str, Any], *, ledger: Mapping[str, Any], parent_protocol_path: Path, parent_closure_result_path: Path) -> None:
    """Recompute every security-relevant binding before a write-once append."""
    identity = {key: value for key, value in protocol.items() if key not in {"protocol_hash", "protocol_id", "created_at"}}
    expected_hash = canonical_hash(identity)
    if protocol.get("schema_version") != GEN2_PROTOCOL_SCHEMA or protocol.get("protocol_hash") != expected_hash or protocol.get("protocol_id") != "gen2_" + expected_hash.removeprefix("sha256:")[:24]:
        raise ValueError("Gen2 protocol schema/hash/id 无效")
    if protocol.get("status") != "preregistered_no_screen_run":
        raise ValueError("Gen2 protocol status 无效")
    generation, periods, space = protocol.get("generation"), protocol.get("periods"), protocol.get("candidate_space")
    parent = verify_gen1_protocol(parent_protocol_path)
    if (not isinstance(generation, Mapping) or not _GENERATION_ID.fullmatch(str(generation.get("generation_id", "")))
            or not _GENERATION_ID.fullmatch(str(generation.get("parent_generation_id", "")))
            or generation.get("generation_id") == generation.get("parent_generation_id")
            or not str(generation.get("parent_protocol_hash", "")).startswith("sha256:")):
        raise ValueError("Gen2 generation id 无效")
    if generation["parent_generation_id"] != parent["generation"]["generation_id"] or generation["parent_protocol_hash"] != parent["protocol_hash"]:
        raise ValueError("Gen2 parent protocol binding 无效")
    if not isinstance(periods, Mapping) or periods.get("final_lockbox_read") is not False or periods.get("planned_fresh_oos") is not True:
        raise ValueError("Gen2 periods/lockbox binding 无效")
    parsed = {key: date.fromisoformat(str(periods[key])) for key in ("research_start", "validation_start", "research_end", "final_lockbox_start", "parent_research_end")}
    _require_fresh_window(Gen2Periods(parsed["research_start"], parsed["validation_start"], parsed["research_end"], parsed["final_lockbox_start"]), parsed["parent_research_end"])
    if parsed["parent_research_end"] != date.fromisoformat(str(parent["periods"]["research_end"])):
        raise ValueError("Gen2 parent research_end binding 无效")
    closure = protocol.get("parent_closure")
    if not isinstance(closure, Mapping) or closure.get("transition") != "unused_parent_lockbox_reassigned_to_child_validation_after_parent_research_closed" or closure.get("read_at_prereg") is not False:
        raise ValueError("Gen2 parent closure binding invalid")
    lockbox = date.fromisoformat(str(closure.get("parent_lockbox_start")))
    if parsed["validation_start"] < lockbox or parsed["final_lockbox_start"] <= lockbox:
        raise ValueError("Gen2 rollover validation/new lockbox boundary invalid")
    verified = verify_parent_generation_closure(parent_closure_result_path)
    expected = {**verified, "transition": closure["transition"], "read_at_prereg": False}
    if dict(closure) != expected: raise ValueError("Gen2 parent closure result binding invalid")
    preregistered_at = datetime.fromisoformat(str(protocol.get("preregistered_at", "")))
    if preregistered_at.tzinfo is None or preregistered_at >= datetime.combine(parsed["validation_start"], datetime.min.time(), tzinfo=timezone.utc):
        raise ValueError("Gen2 必须在 planned validation_start 前预注册")
    if not isinstance(space, Mapping) or space.get("schema_version") != GEN2_CANDIDATE_SPACE_SCHEMA or not isinstance(space.get("candidates"), list):
        raise ValueError("Gen2 candidate space 无效")
    candidates = space["candidates"]
    if len(candidates) != int(generation.get("candidate_budget", -1)):
        raise ValueError("Gen2 candidate budget 无效")
    current_entries = [entry for entry in ledger["entries"] if entry["generation_id"] == generation["generation_id"]]
    if len(current_entries) > 1:
        raise ValueError("Gen2 current generation appears multiple times in ledger")
    prior_entries = [entry for entry in ledger["entries"] if entry["generation_id"] != generation["generation_id"]]
    candidate_ids = sorted(str(item["candidate_semantic_id"]) for item in candidates)
    if current_entries:
        entry = current_entries[0]
        if (entry.get("candidate_budget") != len(candidates) or entry.get("candidate_semantic_ids") != candidate_ids
                or entry.get("parent_generation_id") != generation["parent_generation_id"]
                or entry.get("status") != "preregistered_no_screen_run"
                or entry.get("final_lockbox_read") is not False
                or canonical_hash(entry.get("period_roles")) != canonical_hash(protocol.get("periods"))):
            raise ValueError("Gen2 ledger entry binding invalid")
        artifacts = entry.get("artifact_references")
        if not isinstance(artifacts, Mapping):
            raise ValueError("Gen2 ledger artifact references invalid")
        try:
            protocol_artifact = Path(str(artifacts["protocol"])); space_artifact = Path(str(artifacts["candidate_space"]))
            if (not protocol_artifact.is_file() or not space_artifact.is_file()
                    or protocol_artifact.name != "gen2_protocol.json" or space_artifact.name != "candidate_space.json"
                    or protocol_artifact.parent != space_artifact.parent):
                raise ValueError("Gen2 ledger artifact paths invalid")
            frozen_protocol = json.loads(protocol_artifact.read_text(encoding="utf-8"))
            frozen_space = json.loads(space_artifact.read_text(encoding="utf-8"))
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("Gen2 ledger artifact read failed") from exc
        if canonical_hash(frozen_protocol) != canonical_hash(dict(protocol)) or canonical_hash(frozen_space) != canonical_hash(space):
            raise ValueError("Gen2 ledger artifacts differ from supplied frozen protocol")
        used_before = int(ledger["used_trial_count"]) - len(candidates)
    else:
        used_before = int(ledger["used_trial_count"])
    if (generation.get("global_trial_budget") != ledger["policy"]["global_trial_budget"]
            or generation.get("global_ledger_policy_hash") != ledger["policy"]["policy_hash"]
            or generation.get("global_used_before_generation") != used_before):
        raise ValueError("Gen2 protocol 未绑定当前全局账本")
    if generation.get("global_used_after_generation") != used_before + len(candidates):
        raise ValueError("Gen2 global budget 计算无效")
    seen: set[str] = set()
    benchmark_symbol = protocol.get("grammar", {}).get("benchmark_symbol")
    grammar = protocol.get("grammar")
    if (not isinstance(grammar, Mapping) or not isinstance(benchmark_symbol, str) or not benchmark_symbol
            or grammar.get("version") != GEN2_GRAMMAR_VERSION
            or grammar.get("outcome_blind") is not True
            or grammar.get("cross_series_wrapper_not_rule_dsl") is not True):
        raise ValueError("Gen2 benchmark protocol binding 无效")
    parent_refs = load_gen1_candidate_references(parent_protocol_path)
    parent_logic_hashes = {item["base_rule_logic_hash"] for item in parent_refs}
    for candidate in candidates:
        base = RuleDefinition(**candidate["base_definition"])
        expected = gen2_candidate_semantic_id(base, candidate["context_filters"], str(candidate["benchmark_symbol"]))
        if (candidate.get("candidate_semantic_id") != expected or expected in seen or candidate.get("benchmark_symbol") != benchmark_symbol
                or candidate.get("base_rule_logic_hash") != rule_logic_hash(base) or candidate.get("base_rule_semantic_hash") != compile_rule(base).semantic_hash
                or candidate.get("composition") != "base_rule AND every_context_filter"
                or candidate.get("evaluation_policy") != {"signal_confirmed_at": "T close", "eligible_execution": "T+1 open", "benchmark_alignment": "exact_signal_and_lookback_dates_no_forward_fill", "missing_benchmark_or_history": "filter_false"}):
            raise ValueError("Gen2 candidate semantic id 无效或重复")
        tested = rule_logic_hash(base) in parent_logic_hashes
        expected_dedup = {"exact_candidate_already_tested": False, "base_logic_previously_tested_in_gen1": tested, "decision": "new_contextual_composite" if tested else "new_base_and_contextual_composite"}
        if candidate.get("cross_generation_deduplication") != expected_dedup:
            raise ValueError("Gen2 cross-generation deduplication audit 无效")
        seen.add(expected)
    # The hash proves the document is internally consistent, but alone would
    # not prove that a manually substituted rule came from the frozen finite
    # grammar.  Recreate the seeded candidate draw and compare every record.
    # This verifier is intentionally pre-registration only: the current
    # generation must not yet appear in the append-only ledger.
    try:
        config = Gen2Config(
            str(generation["generation_id"]), str(generation["parent_generation_id"]),
            str(generation["parent_protocol_hash"]), int(generation["candidate_budget"]),
            seed=int(grammar["seed"]), grammar_version=str(grammar["version"]),
            benchmark_symbol=str(benchmark_symbol),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Gen2 frozen grammar/config invalid") from exc
    registered = {semantic for entry in prior_entries for semantic in entry["candidate_semantic_ids"]}
    expected_candidates = generate_gen2_candidates(config, excluded_semantic_ids=registered)
    for candidate in expected_candidates:
        tested = candidate["base_rule_logic_hash"] in parent_logic_hashes
        candidate["cross_generation_deduplication"] = {
            "exact_candidate_already_tested": False,
            "base_logic_previously_tested_in_gen1": tested,
            "decision": "new_contextual_composite" if tested else "new_base_and_contextual_composite",
        }
    if candidates != expected_candidates:
        raise ValueError("Gen2 candidate space differs from frozen grammar draw")


def preregister_gen2_generation(protocol: Mapping[str, Any], *, output_root: Path, ledger_root: Path, parent_protocol_path: Path, parent_closure_result_path: Path) -> dict[str, Any]:
    """Write protocol/candidate-space once, then append one immutable ledger entry."""
    if output_root.exists():
        raise FileExistsError(f"Gen2 输出目录已存在，拒绝覆盖: {output_root}")
    ledger = load_global_trial_ledger(ledger_root)
    verify_gen2_protocol(protocol, ledger=ledger, parent_protocol_path=parent_protocol_path, parent_closure_result_path=parent_closure_result_path)
    generation = protocol["generation"]
    if int(ledger["remaining_trial_count"]) < int(generation["candidate_budget"]):
        raise ValueError("全局 trial budget 不足，拒绝登记")
    if generation["generation_id"] in {entry["generation_id"] for entry in ledger["entries"]}:
        raise ValueError("generation_id 已在全局账本登记")
    candidate_ids = [str(item["candidate_semantic_id"]) for item in protocol["candidate_space"]["candidates"]]
    if len(candidate_ids) != int(generation["candidate_budget"]) or len(set(candidate_ids)) != len(candidate_ids):
        raise ValueError("Gen2 protocol candidate budget 或 semantic id 不一致")
    recorded_ids = {item for entry in ledger["entries"] for item in entry["candidate_semantic_ids"]}
    if recorded_ids.intersection(candidate_ids):
        raise ValueError("Gen2 candidate semantic id 已在全局账本登记")
    if protocol.get("periods", {}).get("final_lockbox_read") is not False:
        raise ValueError("预注册协议不得声称已读取最终锁箱")
    output_root.mkdir(parents=True, exist_ok=False)
    try:
        _write_new_json(output_root / "gen2_protocol.json", protocol)
        _write_new_json(output_root / "candidate_space.json", protocol["candidate_space"])
        entry = {
            "schema_version": GLOBAL_LEDGER_ENTRY_SCHEMA,
            "generation_id": generation["generation_id"],
            "parent_generation_id": generation["parent_generation_id"],
            "candidate_budget": generation["candidate_budget"],
            "candidate_semantic_ids": sorted(candidate_ids),
            "period_roles": protocol["periods"],
            "status": "preregistered_no_screen_run",
            "elimination_reason": None,
            "artifact_references": {"protocol": str((output_root / "gen2_protocol.json").resolve()), "candidate_space": str((output_root / "candidate_space.json").resolve())},
            "final_lockbox_read": False,
        }
        entry["entry_hash"] = canonical_hash(entry)
        entry["recorded_at"] = datetime.now(timezone.utc).isoformat()
        _write_new_json(ledger_root / "entries" / f"{generation['generation_id']}.json", entry)
    except Exception:
        # Existing partial output is deliberately preserved for audit; callers
        # must use a new generation/output path rather than silently retrying.
        raise
    return {"output_root": str(output_root), "ledger_entry": str(ledger_root / "entries" / f"{generation['generation_id']}.json"), "protocol_id": protocol["protocol_id"]}


def apply_context_filters(asset: pd.DataFrame, benchmark: pd.DataFrame, base_signal: pd.Series, filters: Iterable[Mapping[str, Any]], *, benchmark_symbol: str) -> pd.Series:
    """Evaluate wrapper filters at T close; unavailable history is always false.

    Inputs must have unique monotonic datetime indices and ``close``.  The
    benchmark is only exact-date aligned; it is never forward-filled.
    """
    if not asset.index.equals(base_signal.index):
        raise ValueError("base_signal index 必须与 asset 对齐")
    if not pd.api.types.is_bool_dtype(base_signal.dtype):
        raise ValueError("base_signal 必须为 bool/nullable-bool 序列")
    for label, frame in (("asset", asset), ("benchmark", benchmark)):
        if not isinstance(frame.index, pd.DatetimeIndex) or not frame.index.is_monotonic_increasing or not frame.index.is_unique:
            raise ValueError(f"{label} index 必须是唯一递增 DatetimeIndex")
        if "close" not in frame:
            raise ValueError(f"{label} 缺少 close")
    if "volume" not in asset:
        raise ValueError("asset 缺少 volume")
    for label, values in (("asset.close", asset["close"]), ("asset.volume", asset["volume"]), ("benchmark.close", benchmark["close"])):
        if not pd.api.types.is_numeric_dtype(values.dtype):
            raise ValueError(f"{label} 必须是数值")
        numeric = values.astype(float)
        if (~np.isfinite(numeric) | (numeric <= 0.0)).any():
            raise ValueError(f"{label} 必须为有限正值")
    result = base_signal.fillna(False).astype(bool).copy()
    aligned_benchmark_close = benchmark["close"].reindex(asset.index)
    for raw in canonical_context_filters(filters, benchmark_symbol):
        kind, window = raw["kind"], int(raw["window"])
        if kind == "market_regime":
            average = aligned_benchmark_close.rolling(window=window, min_periods=window).mean()
            mask = aligned_benchmark_close > average if raw["state"] == "above_sma" else aligned_benchmark_close < average
        elif kind == "relative_strength":
            asset_return = asset["close"].pct_change(periods=window, fill_method=None)
            benchmark_return = aligned_benchmark_close.pct_change(periods=window, fill_method=None)
            difference = asset_return - benchmark_return
            mask = difference >= float(raw["threshold"]) if raw["operator"] == "gte" else difference <= float(raw["threshold"])
            # Endpoint-only pct_change would otherwise quietly bridge a missing
            # benchmark session.  Require every date in the common return
            # window to be present, rather than inventing an aligned value.
            complete_window = aligned_benchmark_close.notna().rolling(window=window + 1, min_periods=window + 1).sum() == window + 1
            mask &= complete_window
        elif kind == "realized_volatility":
            volatility = asset["close"].pct_change(fill_method=None).rolling(window=window, min_periods=window).std(ddof=1)
            mask = volatility >= float(raw["threshold"]) if raw["operator"] == "gte" else volatility <= float(raw["threshold"])
        else:  # volume_category
            ratio = asset["volume"] / asset["volume"].rolling(window=window, min_periods=window).mean()
            mask = ratio >= float(raw["multiple"])
        result &= mask.fillna(False).astype(bool)
    return result.astype(bool)
