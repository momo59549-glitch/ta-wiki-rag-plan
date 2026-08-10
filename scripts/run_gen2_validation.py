"""Narrow Gen2 Stage2 contract/fixture CLI; it never opens market data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from packages.research.gen2_discovery import load_global_trial_ledger, verify_gen2_protocol
from packages.research.gen2_file_provider import LocalParquetFutureSource, ManifestPitProvider
from packages.research.gen2_future_runner import run_future_incremental
from packages.research.gen2_validation import build_stage2_contract, run_synthetic_smoke, verify_stage2_contract


def _payload(path: str) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict): raise ValueError(f"JSON object required: {path}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gen2 future-validation contract and synthetic smoke only")
    commands = parser.add_subparsers(dest="command", required=True)
    contract = commands.add_parser("contract")
    contract.add_argument("--gen2-protocol", required=True)
    contract.add_argument("--parent-protocol", required=True)
    contract.add_argument("--parent-closure-result", required=True)
    contract.add_argument("--ledger-root", required=True)
    contract.add_argument("--dataset-contract", required=True)
    contract.add_argument("--pit-contract", required=True)
    contract.add_argument("--output")
    contract.add_argument("--dry-run", action="store_true")
    smoke = commands.add_parser("synthetic-smoke")
    smoke.add_argument("--dry-run", action="store_true", default=True)
    future = commands.add_parser("future-run", help="run only an explicit, manifest-bound local Parquet future provider")
    for option in ("--gen2-protocol", "--stage2-contract", "--parent-protocol", "--parent-closure-result", "--ledger-root", "--source-revision-manifest", "--allowed-data-root", "--run-root", "--as-of"):
        future.add_argument(option, required=True)
    args = parser.parse_args(argv)
    if args.command == "synthetic-smoke":
        print(json.dumps({"status": "synthetic_fixture_only", **run_synthetic_smoke(), "approval": "forbidden"}))
        return 0
    if args.command == "future-run":
        protocol, contract = _payload(args.gen2_protocol), _payload(args.stage2_contract)
        ledger = load_global_trial_ledger(Path(args.ledger_root))
        verify_gen2_protocol(protocol, ledger=ledger, parent_protocol_path=Path(args.parent_protocol), parent_closure_result_path=Path(args.parent_closure_result))
        verify_stage2_contract(contract, gen2_protocol=protocol, ledger=ledger, parent_protocol_path=Path(args.parent_protocol), parent_closure_result_path=Path(args.parent_closure_result), project_root=ROOT)
        as_of = __import__("datetime").date.fromisoformat(args.as_of)
        source = LocalParquetFutureSource(Path(args.source_revision_manifest), allowed_data_root=Path(args.allowed_data_root))
        pit = ManifestPitProvider(source)
        identity = source.identity()
        binding = {"schema_version": "gen2-actual-source-lineage-binding/v1", "asset_dataset_id": identity["asset_dataset_id"], "benchmark_dataset_id": identity["benchmark_dataset_id"], "calendar_id": identity["calendar_id"], "pit_lineage_id": identity["pit_lineage_id"], "adjustment": "adjusted_ohlc", "required_fields": ["open", "high", "low", "close", "prev_close", "volume", "amount", "is_st"]}
        if as_of < __import__("datetime").date.fromisoformat(str(protocol["periods"]["validation_start"])):
            result = run_future_incremental(source=source, pit=pit, gen2_protocol=protocol, stage2_contract=contract, ledger=ledger, parent_protocol_path=Path(args.parent_protocol), parent_closure_result_path=Path(args.parent_closure_result), actual_binding=binding, as_of=as_of, run_root=Path(args.run_root), project_root=ROOT)
            print(json.dumps(result, default=str))
            return 0
        result = run_future_incremental(source=source, pit=pit, gen2_protocol=protocol, stage2_contract=contract, ledger=ledger, parent_protocol_path=Path(args.parent_protocol), parent_closure_result_path=Path(args.parent_closure_result), actual_binding=binding, as_of=as_of, run_root=Path(args.run_root), project_root=ROOT)
        print(json.dumps(result, default=str))
        return 0
    if args.dry_run and args.output: raise ValueError("dry-run must not provide an output directory")
    if not args.dry_run and not args.output: raise ValueError("contract output directory is required unless --dry-run")
    protocol, ledger = _payload(args.gen2_protocol), load_global_trial_ledger(Path(args.ledger_root))
    verify_gen2_protocol(protocol, ledger=ledger, parent_protocol_path=Path(args.parent_protocol), parent_closure_result_path=Path(args.parent_closure_result))
    result = build_stage2_contract(protocol, dataset_contract=_payload(args.dataset_contract), pit_universe_contract=_payload(args.pit_contract), output=None if args.dry_run else Path(args.output) if args.output else None, project_root=ROOT)
    if not args.dry_run:
        verify_stage2_contract(result, gen2_protocol=protocol, ledger=ledger, parent_protocol_path=Path(args.parent_protocol), parent_closure_result_path=Path(args.parent_closure_result), project_root=ROOT)
    print(json.dumps({"dry_run": bool(args.dry_run), "contract_id": result["contract_id"], "output": args.output, "market_data_read": False, "approval": "forbidden"}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
