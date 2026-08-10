"""Immutable experiment preregistration for strategy-test campaigns."""
from __future__ import annotations

from datetime import date, datetime, timezone
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from packages.contracts import RuleDefinition
from packages.rule_dsl import CompiledRule, compile_rule, rule_definition_hash, rule_logic_hash


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _hash_identity(identity: Mapping[str, Any]) -> str:
    return "sha256:" + sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _protocol_identity(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return exactly the immutable portion used for a protocol hash."""
    fields = (
        "schema_version",
        "status",
        "rule",
        "dataset_snapshot_id",
        "universe_manifest",
        "symbols",
        "periods",
        "outcomes",
        "validation",
        "execution",
        "analysis",
        "code_version",
        "publication",
    )
    identity = {field: payload.get(field) for field in fields}
    if "promotion" in payload:
        identity["promotion"] = payload["promotion"]
    return identity


def _definition_from_payload(payload: Any) -> RuleDefinition:
    if not isinstance(payload, Mapping):
        raise ValueError("协议 rule.definition 必须是对象")
    allowed = {"id", "version", "name_zh", "expression", "parameters", "warmup_bars", "observed_at", "executable_from"}
    if set(payload) - allowed:
        raise ValueError("协议 rule.definition 含不支持字段")
    required = {"id", "version", "name_zh", "expression"}
    if not required <= set(payload):
        raise ValueError("协议 rule.definition 缺少必填字段")
    return RuleDefinition(
        id=str(payload["id"]),
        version=str(payload["version"]),
        name_zh=str(payload["name_zh"]),
        expression=dict(payload["expression"]),
        parameters=dict(payload.get("parameters", {})),
        warmup_bars=int(payload.get("warmup_bars", 0)),
        observed_at=str(payload.get("observed_at", "bar_close")),
        executable_from=str(payload.get("executable_from", "next_bar_open")),
    )


def verify_experiment_protocol(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Verify an immutable protocol before readiness or execution.

    Legacy catalog-reference protocols remain readable for historical records,
    but a protocol carrying a full definition must prove every definition hash.
    New auto-discovery promotions require the latter form.
    """
    failures: list[str] = []
    if payload.get("schema_version") != "experiment-protocol/v1":
        failures.append("schema_version")
    identity = _protocol_identity(payload)
    expected_hash = _hash_identity(identity)
    expected_id = "protocol_" + expected_hash.removeprefix("sha256:")[:24]
    if payload.get("protocol_hash") != expected_hash:
        failures.append("protocol_hash")
    if payload.get("protocol_id") != expected_id:
        failures.append("protocol_id")

    definition_status = "legacy_catalog_reference"
    rule_payload = payload.get("rule")
    if not isinstance(rule_payload, Mapping):
        failures.append("rule")
    elif "definition" in rule_payload:
        definition_status = "full_definition_bound"
        try:
            definition = _definition_from_payload(rule_payload["definition"])
            compiled = compile_rule(definition)
            if rule_payload.get("id") != definition.id or rule_payload.get("version") != definition.version:
                failures.append("rule_identity")
            if rule_payload.get("parameters") != definition.parameters:
                failures.append("rule_parameters")
            if rule_payload.get("semantic_hash") != compiled.semantic_hash:
                failures.append("rule_semantic_hash")
            if rule_payload.get("definition_hash") != rule_definition_hash(definition):
                failures.append("rule_definition_hash")
            if rule_payload.get("logic_hash") != rule_logic_hash(definition):
                failures.append("rule_logic_hash")
        except (TypeError, ValueError) as exc:
            failures.append(f"rule_definition:{exc}")
    return {
        "status": "valid" if not failures else "invalid",
        "protocol_id": payload.get("protocol_id"),
        "protocol_hash": payload.get("protocol_hash"),
        "expected_protocol_hash": expected_hash,
        "expected_protocol_id": expected_id,
        "definition_status": definition_status,
        "failures": failures,
    }


def build_experiment_protocol(
    rule: CompiledRule,
    pipeline_config: Any,
    symbols: Iterable[str],
    dataset_snapshot_id: str,
    output: Path,
    *,
    minimum_oos_observations: int,
    max_candidate_trials: int = 20,
    code_snapshot_id: str | None = None,
    promotion: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if max_candidate_trials < 1:
        raise ValueError("max_candidate_trials 必须为正整数")
    horizons = tuple(int(item) for item in pipeline_config.horizons)
    lockbox_start = getattr(pipeline_config, "lockbox_start", None)
    reasons = []
    if pipeline_config.start is None or pipeline_config.end is None:
        reasons.append("研究起止日期必须显式冻结")
    if pipeline_config.out_of_sample_start is None:
        reasons.append("缺少验证集起始日期")
    if lockbox_start is None:
        reasons.append("缺少最终锁箱起始日期")
    if pipeline_config.end and lockbox_start and pipeline_config.end >= lockbox_start:
        reasons.append("研究结束日期必须早于锁箱起始日期")
    if pipeline_config.start and pipeline_config.out_of_sample_start and pipeline_config.start >= pipeline_config.out_of_sample_start:
        reasons.append("验证集必须晚于研究起始日期")
    if pipeline_config.out_of_sample_start and lockbox_start and pipeline_config.out_of_sample_start >= lockbox_start:
        reasons.append("锁箱必须晚于验证集起始日期")
    if not pipeline_config.universe_manifest:
        reasons.append("缺少点时股票池 manifest")

    base_commission = float(pipeline_config.commission_bps_per_side)
    base_slippage = float(pipeline_config.slippage_bps_per_side)
    identity = {
        "schema_version": "experiment-protocol/v1",
        "status": "preregistered",
        "rule": {
            "id": rule.definition.id,
            "version": rule.definition.version,
            "semantic_hash": rule.semantic_hash,
            "logic_hash": rule_logic_hash(rule.definition),
            "definition_hash": rule_definition_hash(rule.definition),
            "parameters": dict(rule.definition.parameters),
            "definition": asdict(rule.definition),
        },
        "dataset_snapshot_id": dataset_snapshot_id,
        "universe_manifest": str(pipeline_config.universe_manifest) if pipeline_config.universe_manifest else None,
        "symbols": sorted(set(symbols)),
        "periods": {"research_start": _iso(pipeline_config.start), "validation_start": _iso(pipeline_config.out_of_sample_start), "research_end": _iso(pipeline_config.end), "final_lockbox_start": _iso(lockbox_start)},
        "outcomes": {"primary_metric": "mean_net_excess_return", "horizons": list(horizons), "minimum_oos_observations": minimum_oos_observations},
        "validation": {"engine": "skfolio.WalkForward", "purge_size": max(horizons), "multiple_testing": "fdr_bh", "alpha": 0.05, "max_candidate_trials": max_candidate_trials},
        "execution": {
            "entry": "next_session_open", "exit": "fixed_horizon_close",
            "base_cost_bps_per_side": {"commission": base_commission, "slippage": base_slippage},
            "stress_cost_scenarios": [
                {"name": "2x", "commission": base_commission * 2, "slippage": base_slippage * 2},
                {"name": "3x", "commission": base_commission * 3, "slippage": base_slippage * 3},
            ],
        },
        "analysis": {
            "benchmark_symbol": pipeline_config.benchmark_symbol,
            "benchmark_dataset": pipeline_config.benchmark_dataset if pipeline_config.benchmark_symbol else None,
            "market_regime_window": int(pipeline_config.market_regime_window),
            "min_signal_amount": pipeline_config.min_signal_amount,
            "skip_untradeable": bool(pipeline_config.skip_untradeable),
        },
        "code_version": code_snapshot_id or os.environ.get("TA_CODE_VERSION", "working-tree-recorded-at-runtime"),
        "publication": "blocked_until_validation_lockbox_and_human_approval",
    }
    if promotion is not None:
        identity["promotion"] = dict(promotion)
    protocol_hash = _hash_identity(identity)
    payload = {
        **identity,
        "protocol_id": "protocol_" + protocol_hash.removeprefix("sha256:")[:24],
        "protocol_hash": protocol_hash,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "readiness": {"status": "ready" if not reasons else "incomplete", "reasons": reasons},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
