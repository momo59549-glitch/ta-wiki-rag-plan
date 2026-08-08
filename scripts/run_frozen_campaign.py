"""Execute exactly one preregistered strategy campaign without parameter overrides."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.agents import FileResearchTeam, TeamConfig
from packages.market_data import CompositeParquetMarketData
from packages.research import PipelineConfig
from packages.research.json_store import write_json
from packages.rule_dsl import compile_rule
from packages.rules import get_rule


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--model-data", type=Path, default=Path(r"H:\股票模型\Model\data"))
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args()

    campaign = args.campaign.resolve()
    protocol_path = campaign / "experiment_protocol.json"
    if not protocol_path.is_file():
        parser.error(f"campaign protocol not found: {protocol_path}")
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    protocol_id = str(protocol.get("protocol_id", ""))
    if not protocol_id.startswith("protocol_"):
        parser.error("campaign protocol_id is invalid")
    output_root = (args.output_root or REPOSITORY_ROOT / "data" / "strategy_test_executions" / protocol_id).resolve()
    request_path = output_root / "execution_request.json"
    if request_path.exists() or (output_root.exists() and any(output_root.glob("case_*"))):
        parser.error(f"protocol already has an execution record; refusing an unregistered retry: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)

    periods = protocol["periods"]
    outcomes = protocol["outcomes"]
    execution = protocol["execution"]["base_cost_bps_per_side"]
    analysis = protocol["analysis"]
    pipeline = PipelineConfig(
        horizons=tuple(int(item) for item in outcomes["horizons"]),
        start=_date(periods["research_start"]),
        end=_date(periods["research_end"]),
        benchmark_symbol=analysis["benchmark_symbol"],
        benchmark_dataset=analysis["benchmark_dataset"] or "etf_cache",
        commission_bps_per_side=float(execution["commission"]),
        slippage_bps_per_side=float(execution["slippage"]),
        out_of_sample_start=_date(periods["validation_start"]),
        market_regime_window=int(analysis["market_regime_window"]),
        min_signal_amount=analysis["min_signal_amount"],
        skip_untradeable=bool(analysis["skip_untradeable"]),
        universe_manifest=str(protocol["universe_manifest"]),
        lockbox_start=_date(periods["final_lockbox_start"]),
    )
    team_config = TeamConfig(
        pipeline=pipeline,
        min_out_of_sample_observations=int(outcomes["minimum_oos_observations"]),
        max_candidate_trials=int(protocol["validation"]["max_candidate_trials"]),
    )
    source = CompositeParquetMarketData(
        args.model_data,
        ("trend_cache", "tushare_daily_cache", "tushare_incremental_cache"),
    )
    rule = compile_rule(get_rule(protocol["rule"]["id"]))
    started_at = datetime.now(timezone.utc)
    request = {
        "schema_version": "frozen-campaign-execution/v1",
        "status": "running",
        "campaign": str(campaign),
        "protocol_id": protocol_id,
        "protocol_hash": protocol["protocol_hash"],
        "dataset_snapshot_id": protocol["dataset_snapshot_id"],
        "code_snapshot_id": protocol["code_version"],
        "candidate_trial_number": 1,
        "started_at": started_at,
        "strategy_parameters_overridable": False,
        "final_lockbox_consumed": False,
        "progress_glob": str(output_root / "case_*" / "research_run" / "*" / "progress.json"),
    }
    write_json(request_path, request)
    print(json.dumps({"status": "started", "protocol_id": protocol_id, "output_root": str(output_root), "progress_glob": request["progress_glob"]}, ensure_ascii=False), flush=True)
    try:
        case_dir = FileResearchTeam(source, output_root).run(
            list(protocol["symbols"]),
            rule,
            team_config,
            frozen_campaign=campaign,
        )
    except Exception as exc:
        write_json(request_path, {**request, "status": "failed", "finished_at": datetime.now(timezone.utc), "error_type": type(exc).__name__})
        raise
    case = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    write_json(request_path, {**request, "status": "completed", "finished_at": datetime.now(timezone.utc), "case_dir": str(case_dir), "case_id": case["case_id"], "qa_status": case["qa_status"]})
    print(json.dumps({"status": "completed", "case_dir": str(case_dir), **case}, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
