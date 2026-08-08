"""Freeze a strategy-test campaign without running the strategy."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.market_data import CompositeParquetMarketData, LocalParquetMarketData, audit_market_data_quality, build_strong_snapshot, load_point_in_time_universe
from packages.research import PipelineConfig, build_code_snapshot, build_experiment_protocol, evaluate_strategy_readiness
from packages.rule_dsl import compile_rule
from packages.rules import get_rule


def _date(value: str) -> date:
    return date.fromisoformat(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-data", type=Path, default=Path(r"H:\股票模型\Model\data"))
    parser.add_argument("--universe-manifest", type=Path, required=True)
    parser.add_argument("--rule", default="hammer")
    parser.add_argument("--start", type=_date, required=True)
    parser.add_argument("--oos-start", type=_date, required=True)
    parser.add_argument("--end", type=_date, required=True)
    parser.add_argument("--lockbox-start", type=_date, required=True)
    parser.add_argument("--horizons", type=int, nargs="+", default=[1, 3, 5, 10, 20])
    parser.add_argument("--commission-bps", type=float, default=3.0)
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    parser.add_argument("--min-oos-observations", type=int, default=300)
    parser.add_argument("--max-candidate-trials", type=int, default=20)
    parser.add_argument("--output-root", type=Path, default=Path("data/strategy_test_campaigns"))
    args = parser.parse_args()

    campaign_id = "campaign_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    campaign = args.output_root / campaign_id
    source = CompositeParquetMarketData(args.model_data, ("trend_cache", "tushare_daily_cache", "tushare_incremental_cache"))
    symbols = source.symbols()
    active_symbols, _ = load_point_in_time_universe(args.universe_manifest, args.end)
    benchmark = LocalParquetMarketData(args.model_data, "etf_cache")
    code = build_code_snapshot(REPOSITORY_ROOT, campaign / "code_snapshot.json")
    dataset = build_strong_snapshot(source, symbols, campaign / "dataset_snapshot.json", extra_sources=(("benchmark", benchmark, ("000001",)),))
    audit_market_data_quality(
        source, symbols, campaign / "market_data_quality.json", as_of=args.end,
        active_at_end=active_symbols, minimum_bars=max(args.horizons) + 2,
    )
    config = PipelineConfig(
        horizons=tuple(args.horizons), start=args.start, end=args.end,
        benchmark_symbol="000001", benchmark_dataset="etf_cache",
        commission_bps_per_side=args.commission_bps, slippage_bps_per_side=args.slippage_bps,
        out_of_sample_start=args.oos_start, universe_manifest=str(args.universe_manifest),
        lockbox_start=args.lockbox_start,
    )
    protocol = build_experiment_protocol(
        compile_rule(get_rule(args.rule)), config, symbols, dataset["dataset_snapshot_id"],
        campaign / "experiment_protocol.json", minimum_oos_observations=args.min_oos_observations,
        max_candidate_trials=args.max_candidate_trials, code_snapshot_id=code["code_snapshot_id"],
    )
    readiness = evaluate_strategy_readiness(
        project_root=REPOSITORY_ROOT, source=source, universe_manifest=args.universe_manifest,
        as_of=args.end, dataset_snapshot_path=campaign / "dataset_snapshot.json",
        code_snapshot_path=campaign / "code_snapshot.json", protocol_path=campaign / "experiment_protocol.json",
        data_quality_path=campaign / "market_data_quality.json",
        output=campaign / "readiness_report.json",
    )
    print(json.dumps({"campaign_dir": str(campaign), "status": readiness["status"], "protocol_id": protocol["protocol_id"], "checks": readiness["checks"]}, ensure_ascii=False, indent=2))
    return 0 if readiness["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
