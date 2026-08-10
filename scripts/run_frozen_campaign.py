"""Execute exactly one preregistered strategy campaign without parameter overrides."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys
from uuid import uuid4

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.agents import FileResearchTeam, TeamConfig
from packages.contracts import RuleDefinition
from packages.market_data import CompositeParquetMarketData, verify_source_against_strong_snapshot
from packages.research import PipelineConfig, verify_experiment_protocol
from packages.research.json_store import write_json
from packages.research.run_artifacts import canonical_hash, verify_checkpoint
from packages.research.promotion import verify_frozen_campaign_rule
from packages.rule_dsl import compile_rule, rule_definition_hash, rule_logic_hash
from packages.rules import get_rule


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _compiled_rule_for_campaign(campaign: Path, protocol: dict) -> tuple[object, str]:
    """Load only the definition frozen inside the Campaign/protocol.

    Legacy catalog-reference Campaigns remain runnable for historical work;
    new Campaigns use a full protocol definition, and promoted auto-discovery
    Campaigns additionally require the receipt-bound local rule record.
    """
    frozen_rule_path = campaign / "frozen_rule_definition.json"
    receipt_path = campaign / "promotion_receipt.json"
    if frozen_rule_path.exists() or receipt_path.exists():
        if not frozen_rule_path.is_file() or not receipt_path.is_file():
            raise ValueError("自动发现冻结 Campaign 必须同时包含 frozen_rule_definition.json 与 promotion_receipt.json")
        frozen_payload = json.loads(frozen_rule_path.read_text(encoding="utf-8"))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        frozen_check = verify_frozen_campaign_rule(frozen_payload, receipt)
        if frozen_check["status"] != "valid":
            raise ValueError("冻结候选定义校验失败: " + ", ".join(frozen_check["failures"]))
        definition = RuleDefinition(**frozen_check["definition"])
        rule = compile_rule(definition)
        promotion = protocol.get("promotion", {})
        if (
            promotion.get("promotion_receipt_id") != receipt.get("receipt_id")
            or promotion.get("promotion_receipt_hash") != receipt.get("receipt_hash")
            or protocol.get("rule", {}).get("definition_hash") != rule_definition_hash(definition)
            or protocol.get("rule", {}).get("logic_hash") != rule_logic_hash(definition)
        ):
            raise ValueError("冻结候选收据与协议规则绑定不一致")
        return rule, "receipt_bound_auto_discovery"
    if "definition" in protocol.get("rule", {}):
        definition = RuleDefinition(**protocol["rule"]["definition"])
        return compile_rule(definition), "protocol_full_definition"
    return compile_rule(get_rule(protocol["rule"]["id"])), "legacy_catalog_reference"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--model-data", type=Path, default=Path(r"H:\股票模型\Model\data"))
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--resume", action="store_true", help="resume the exact registered interrupted execution")
    parser.add_argument("--batch-size", type=int, help="symbols per atomic batch; frozen once execution starts")
    args = parser.parse_args()

    campaign = args.campaign.resolve()
    protocol_path = campaign / "experiment_protocol.json"
    if not protocol_path.is_file():
        parser.error(f"campaign protocol not found: {protocol_path}")
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    protocol_check = verify_experiment_protocol(protocol)
    if protocol_check["status"] != "valid":
        parser.error("campaign protocol integrity invalid: " + ", ".join(protocol_check["failures"]))
    protocol_id = str(protocol["protocol_id"])
    try:
        rule, rule_source = _compiled_rule_for_campaign(campaign, protocol)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    output_root = (args.output_root or REPOSITORY_ROOT / "data" / "strategy_test_executions" / protocol_id).resolve()
    request_path = output_root / "execution_request.json"
    if not args.resume and (request_path.exists() or (output_root.exists() and any(output_root.glob("case_*")))):
        parser.error(f"protocol already has an execution record; refusing an unregistered retry: {output_root}")
    if args.resume and not request_path.is_file():
        parser.error("no registered interrupted execution to resume; derive a new Campaign")
    previous_request = json.loads(request_path.read_text(encoding="utf-8")) if args.resume else None
    if args.resume and (
        previous_request.get("schema_version") != "frozen-campaign-execution/v2"
        or not previous_request.get("case_id")
        or not previous_request.get("run_id")
        or not previous_request.get("checkpoint_path")
    ):
        parser.error("legacy interrupted run has no safe checkpoint; it cannot be resumed—derive a new Campaign")
    if args.resume and previous_request.get("status") not in {"interrupted", "running"}:
        parser.error(f"execution status {previous_request.get('status')!r} is not resumable; derive a new Campaign")
    batch_size = int(previous_request["batch_size"]) if args.resume and args.batch_size is None else (args.batch_size or 25)
    if batch_size < 1:
        parser.error("batch-size must be positive")

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
    try:
        FileResearchTeam.preflight_frozen_campaign(campaign, list(protocol["symbols"]), rule, team_config)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error("campaign metadata preflight failed: " + str(exc))
    case_id = str(previous_request["case_id"]) if previous_request else "case_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
    run_id = str(previous_request["run_id"]) if previous_request else datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
    execution_identity = {
        "schema_version": "research-execution-identity/v1",
        "case_id": case_id,
        "run_id": run_id,
        "experiment_protocol_id": protocol_id,
        "experiment_protocol_hash": protocol["protocol_hash"],
        "code_snapshot_id": protocol["code_version"],
        "dataset_snapshot_id": protocol["dataset_snapshot_id"],
        "dataset_snapshot_manifest": "dataset_snapshot_manifest.json",
        "rule_id": rule.definition.id,
        "rule_version": rule.definition.version,
        "rule_semantic_hash": rule.semantic_hash,
        "rule_definition_hash": rule_definition_hash(rule.definition),
        "symbols": sorted(set(protocol["symbols"])),
        "pipeline_config_hash": canonical_hash(asdict(pipeline)),
        "batch_size": batch_size,
    }
    checkpoint_path = output_root / case_id / "research_run" / run_id / "checkpoint.json"
    if args.resume:
        request_bindings = {
            "campaign": str(campaign), "protocol_id": protocol_id, "protocol_hash": protocol["protocol_hash"],
            "dataset_snapshot_id": protocol["dataset_snapshot_id"], "code_snapshot_id": protocol["code_version"],
            "rule_definition_hash": rule_definition_hash(rule.definition), "rule_logic_hash": rule_logic_hash(rule.definition),
            "case_id": case_id, "run_id": run_id, "batch_size": batch_size,
            "execution_identity_hash": canonical_hash(execution_identity), "checkpoint_path": str(checkpoint_path),
        }
        mismatches = [key for key, value in request_bindings.items() if previous_request.get(key) != value]
        if mismatches:
            parser.error("resume identity mismatch: " + ", ".join(mismatches))
        if not checkpoint_path.is_file():
            parser.error("interrupted execution has no safe checkpoint; it cannot be resumed—derive a new Campaign")
        try:
            verify_checkpoint(checkpoint_path.parent, execution_identity)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            parser.error("resume checkpoint invalid: " + str(exc))
    source = CompositeParquetMarketData(
        args.model_data,
        ("trend_cache", "tushare_daily_cache", "tushare_incremental_cache"),
    )
    source_check = verify_source_against_strong_snapshot(
        source,
        campaign / "dataset_snapshot.json",
        issue_reuse_token=True,
    )
    if source_check["status"] != "valid":
        parser.error("campaign execution source is not the frozen dataset snapshot: " + ", ".join(item["reason"] for item in source_check["failures"]))
    public_source_check = {key: value for key, value in source_check.items() if not key.startswith("_")}
    output_root.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(timezone.utc)
    request = {
        "schema_version": "frozen-campaign-execution/v2",
        "status": "running",
        "campaign": str(campaign),
        "protocol_id": protocol_id,
        "protocol_hash": protocol["protocol_hash"],
        "dataset_snapshot_id": protocol["dataset_snapshot_id"],
        "code_snapshot_id": protocol["code_version"],
        "rule_source": rule_source,
        "rule_definition_hash": rule_definition_hash(rule.definition),
        "rule_logic_hash": rule_logic_hash(rule.definition),
        "source_snapshot_check": public_source_check,
        "candidate_trial_number": 1,
        "started_at": previous_request.get("started_at") if previous_request else started_at,
        "resumed_at": started_at if args.resume else None,
        "strategy_parameters_overridable": False,
        "final_lockbox_consumed": False,
        "progress_glob": str(output_root / "case_*" / "research_run" / "*" / "progress.json"),
        "case_id": case_id,
        "run_id": run_id,
        "batch_size": batch_size,
        "execution_identity_hash": canonical_hash(execution_identity),
        "checkpoint_path": str(checkpoint_path),
    }
    write_json(request_path, request)
    print(json.dumps({"status": "started", "protocol_id": protocol_id, "output_root": str(output_root), "progress_glob": request["progress_glob"]}, ensure_ascii=False), flush=True)
    try:
        case_dir = FileResearchTeam(source, output_root).run(
            list(protocol["symbols"]),
            rule,
            team_config,
            frozen_campaign=campaign,
            frozen_source_check=source_check,
            case_id=case_id,
            run_id=run_id,
            resume=args.resume,
            batch_size=batch_size,
        )
    except Exception as exc:
        write_json(request_path, {**request, "status": "interrupted", "interrupted_at": datetime.now(timezone.utc), "error_type": type(exc).__name__})
        raise
    case = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    write_json(request_path, {**request, "status": "completed", "finished_at": datetime.now(timezone.utc), "case_dir": str(case_dir), "case_id": case["case_id"], "qa_status": case["qa_status"]})
    print(json.dumps({"status": "completed", "case_dir": str(case_dir), **case}, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
