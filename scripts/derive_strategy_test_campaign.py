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

from packages.market_data import CompositeParquetMarketData
from packages.research import PipelineConfig, build_code_snapshot, build_experiment_protocol, evaluate_strategy_readiness
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

    campaign_id = "campaign_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    campaign = (args.output_root / campaign_id).resolve()
    if campaign.exists():
        parser.error(f"refusing to overwrite campaign: {campaign}")
    campaign.mkdir(parents=True)

    for name in ("dataset_snapshot.json", "market_data_quality.json"):
        shutil.copy2(source_campaign / name, campaign / name)

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
    code = build_code_snapshot(REPOSITORY_ROOT, campaign / "code_snapshot.json")
    rule = compile_rule(get_rule(old_protocol["rule"]["id"]))
    protocol = build_experiment_protocol(
        rule,
        config,
        old_protocol["symbols"],
        old_protocol["dataset_snapshot_id"],
        campaign / "experiment_protocol.json",
        minimum_oos_observations=int(outcomes["minimum_oos_observations"]),
        max_candidate_trials=int(validation["max_candidate_trials"]),
        code_snapshot_id=code["code_snapshot_id"],
    )
    data_source = CompositeParquetMarketData(
        args.model_data,
        ("trend_cache", "tushare_daily_cache", "tushare_incremental_cache"),
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
    )
    derivation = {
        "schema_version": "strategy-campaign-derivation/v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_campaign": str(source_campaign),
        "source_protocol_id": old_protocol["protocol_id"],
        "reused_dataset_snapshot_id": old_protocol["dataset_snapshot_id"],
        "new_code_snapshot_id": code["code_snapshot_id"],
        "new_protocol_id": protocol["protocol_id"],
        "strategy_executed": False,
    }
    (campaign / "campaign_derivation.json").write_text(json.dumps(derivation, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"campaign_dir": str(campaign), "status": readiness["status"], "protocol_id": protocol["protocol_id"], "checks": readiness["checks"]}, ensure_ascii=False, indent=2))
    return 0 if readiness["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
