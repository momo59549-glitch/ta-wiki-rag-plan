"""Derive a fresh preregistered campaign from an already verified data snapshot.

This command never runs a strategy and never mutates the source campaign.  It
copies the strong dataset and quality manifests, binds them to the current code
snapshot, creates a new protocol, and re-runs every readiness gate.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import shutil
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.market_data import CompositeParquetMarketData, verify_source_against_strong_snapshot
from packages.contracts import RuleDefinition
from packages.research import PipelineConfig, build_code_snapshot, build_experiment_protocol, evaluate_strategy_readiness, verify_experiment_protocol
from packages.research.promotion import build_frozen_campaign_rule, verify_auto_discovery_promotion_receipt
from packages.rule_dsl import compile_rule
from packages.rules import get_rule


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _resolve_project_path(value: str) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else REPOSITORY_ROOT / candidate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-campaign", type=Path, required=True)
    parser.add_argument("--model-data", type=Path, default=Path(r"H:\股票模型\Model\data"))
    parser.add_argument("--output-root", type=Path, default=Path("data/strategy_test_campaigns"))
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--rule", default=None, help="替换源协议的已登记规则 id（默认沿用源协议规则）")
    selection.add_argument("--promotion-receipt", type=Path, default=None, help="显式人工研究选择后的自动发现收据")
    args = parser.parse_args()

    source_campaign = args.from_campaign.resolve()
    required = ("dataset_snapshot.json", "market_data_quality.json", "experiment_protocol.json", "readiness_report.json")
    missing = [name for name in required if not (source_campaign / name).is_file()]
    if missing:
        parser.error("source campaign is incomplete: " + ", ".join(missing))

    old_readiness = json.loads((source_campaign / "readiness_report.json").read_text(encoding="utf-8"))
    if old_readiness.get("status") != "ready":
        parser.error("source campaign must have status=ready")
    old_protocol = json.loads((source_campaign / "experiment_protocol.json").read_text(encoding="utf-8"))
    if old_protocol.get("status") != "preregistered":
        parser.error("source protocol must be preregistered")
    source_protocol_check = verify_experiment_protocol(old_protocol)
    if source_protocol_check["status"] != "valid":
        parser.error("source campaign protocol integrity invalid: " + ", ".join(source_protocol_check["failures"]))
    periods = old_protocol["periods"]
    execution = old_protocol["execution"]["base_cost_bps_per_side"]
    outcomes = old_protocol["outcomes"]
    validation = old_protocol["validation"]
    analysis = old_protocol.get("analysis", {})
    universe_manifest = _resolve_project_path(old_protocol["universe_manifest"])
    config = PipelineConfig(
        horizons=tuple(int(item) for item in outcomes["horizons"]),
        start=_date(periods["research_start"]),
        end=_date(periods["research_end"]),
        benchmark_symbol=analysis.get("benchmark_symbol", "000001"),
        benchmark_dataset=analysis.get("benchmark_dataset") or "etf_cache",
        commission_bps_per_side=float(execution["commission"]),
        slippage_bps_per_side=float(execution["slippage"]),
        out_of_sample_start=_date(periods["validation_start"]),
        universe_manifest=str(Path(old_protocol["universe_manifest"])),
        lockbox_start=_date(periods["final_lockbox_start"]),
        market_regime_window=int(analysis.get("market_regime_window", 60)),
        min_signal_amount=analysis.get("min_signal_amount"),
        skip_untradeable=bool(analysis.get("skip_untradeable", True)),
    )
    receipt: dict | None = None
    promotion: dict | None = None
    frozen_rule: dict | None = None
    if args.promotion_receipt is not None:
        receipt_path = args.promotion_receipt.resolve()
        if not receipt_path.is_file():
            parser.error(f"promotion receipt not found: {receipt_path}")
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt_check = verify_auto_discovery_promotion_receipt(receipt)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            parser.error(f"invalid promotion receipt: {exc}")
        if receipt_check["status"] != "valid":
            parser.error("invalid promotion receipt: " + ", ".join(receipt_check["failures"]))
        rule = compile_rule(RuleDefinition(**receipt["selected_definition"]))
        frozen_rule = build_frozen_campaign_rule(receipt)
        promotion = {
            "source_kind": "auto_discovery",
            "promotion_receipt_id": receipt["receipt_id"],
            "promotion_receipt_hash": receipt["receipt_hash"],
            "source_registry_id": receipt["source"]["registry_id"],
            "source_registry_state_hash": receipt["source"]["registry_state_hash"],
            "source_auto_discovery_protocol_hash": receipt["source"]["auto_discovery_protocol_hash"],
            "source_search_id": receipt["source"]["source_search_id"],
            "source_rule_semantic_hash": receipt["source"]["source_rule_semantic_hash"],
            "source_rule_logic_hash": receipt["source"]["source_rule_logic_hash"],
            "research_only": True,
            "approval_status": "not_approved",
        }
        rule_id = rule.definition.id
    else:
        rule_id = args.rule or old_protocol["rule"]["id"]
        try:
            rule = compile_rule(get_rule(rule_id))
        except KeyError as exc:
            parser.error(str(exc))

    data_source = CompositeParquetMarketData(
        args.model_data,
        ("trend_cache", "tushare_daily_cache", "tushare_incremental_cache"),
    )
    source_data_check = verify_source_against_strong_snapshot(
        data_source,
        source_campaign / "dataset_snapshot.json",
        issue_reuse_token=True,
    )
    if source_data_check["status"] != "valid":
        parser.error("source campaign model-data is not its frozen dataset snapshot: " + ", ".join(item["reason"] for item in source_data_check["failures"]))

    campaign_id = "campaign_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    campaign = (args.output_root / campaign_id).resolve()
    if campaign.exists():
        parser.error(f"refusing to overwrite campaign: {campaign}")
    campaign.mkdir(parents=True)
    for name in ("dataset_snapshot.json", "market_data_quality.json"):
        shutil.copy2(source_campaign / name, campaign / name)
    if receipt is not None and frozen_rule is not None:
        (campaign / "promotion_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        (campaign / "frozen_rule_definition.json").write_text(json.dumps(frozen_rule, ensure_ascii=False, indent=2), encoding="utf-8")
    code = build_code_snapshot(REPOSITORY_ROOT, campaign / "code_snapshot.json")
    protocol = build_experiment_protocol(
        rule,
        config,
        old_protocol["symbols"],
        old_protocol["dataset_snapshot_id"],
        campaign / "experiment_protocol.json",
        minimum_oos_observations=int(outcomes["minimum_oos_observations"]),
        max_candidate_trials=int(validation["max_candidate_trials"]),
        code_snapshot_id=code["code_snapshot_id"],
        promotion=promotion,
    )
    readiness = evaluate_strategy_readiness(
        project_root=REPOSITORY_ROOT,
        source=data_source,
        universe_manifest=universe_manifest,
        as_of=config.end,
        dataset_snapshot_path=campaign / "dataset_snapshot.json",
        code_snapshot_path=campaign / "code_snapshot.json",
        protocol_path=campaign / "experiment_protocol.json",
        data_quality_path=campaign / "market_data_quality.json",
        output=campaign / "readiness_report.json",
        source_check=source_data_check,
    )
    derivation = {
        "schema_version": "strategy-campaign-derivation/v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_campaign": str(source_campaign),
        "source_protocol_id": old_protocol["protocol_id"],
        "reused_dataset_snapshot_id": old_protocol["dataset_snapshot_id"],
        "new_code_snapshot_id": code["code_snapshot_id"],
        "new_protocol_id": protocol["protocol_id"],
        "rule_id": rule_id,
        "rule_version": rule.definition.version,
        "rule_semantic_hash": rule.semantic_hash,
        "rule_source": "receipt_bound_auto_discovery" if receipt is not None else "catalog",
        "promotion_receipt_id": receipt["receipt_id"] if receipt is not None else None,
        "promotion_receipt_hash": receipt["receipt_hash"] if receipt is not None else None,
        "strategy_executed": False,
    }
    (campaign / "campaign_derivation.json").write_text(json.dumps(derivation, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"campaign_dir": str(campaign), "status": readiness["status"], "protocol_id": protocol["protocol_id"], "checks": readiness["checks"]}, ensure_ascii=False, indent=2))
    return 0 if readiness["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
