"""Immutable experiment preregistration for strategy-test campaigns."""
from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Iterable

from packages.rule_dsl import CompiledRule


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


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
        "rule": {"id": rule.definition.id, "version": rule.definition.version, "semantic_hash": rule.semantic_hash, "parameters": dict(rule.definition.parameters)},
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
    protocol_hash = "sha256:" + sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
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
