"""Prepare or execute the fixed three-candidate preregistered comparison.

This command never reads a final lockbox and can only produce research ranking.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.market_data import CompositeParquetMarketData
from packages.research.candidate_comparison import FIXED_RULES, build_comparison_protocol, run_comparison, validate_completed_case
from packages.research.comparison_panel import build_comparison_panel


def _add_cases(parser: argparse.ArgumentParser) -> None:
    for name in ("rsi", "roc", "breakdown"):
        parser.add_argument(f"--{name}-case", type=Path, required=True)


def _fixed_case_inputs(args) -> tuple[dict[str, Path], dict[str, dict]]:
    cases = {name: getattr(args, f"{name}_case").resolve() for name in FIXED_RULES}
    identities = {}
    for name, path in cases.items():
        identity = validate_completed_case(path)
        actual_rule = {key: identity.get(key) for key in ("semantic_hash", "logic_hash", "receipt_id", "receipt_hash")}
        if actual_rule != FIXED_RULES[name]:
            raise ValueError(f"{name} does not match the fixed promoted rule identity")
        identities[name] = identity
    return cases, identities


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build-panel", help="build the trusted symbol-sharded OOS panel from the shared strong snapshot")
    _add_cases(build)
    build.add_argument("--model-data", type=Path, default=Path(r"H:\股票模型\Model\data"))
    build.add_argument("--panel-dir", type=Path, required=True)
    prepare = subparsers.add_parser("prepare", help="validate inputs and freeze the comparison before computation")
    _add_cases(prepare)
    prepare.add_argument("--market-panel", type=Path, required=True, help="panel_manifest.json produced by build-panel")
    prepare.add_argument("--protocol", type=Path, required=True)
    prepare.add_argument("--result", type=Path, required=True, help="the only result path this protocol may write")
    prepare.add_argument("--seed", type=int, default=20260809)
    execute = subparsers.add_parser("run", help="execute exactly one frozen comparison")
    execute.add_argument("--protocol", type=Path, required=True)
    execute.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    try:
        if args.command in {"build-panel", "prepare"}:
            cases, identities = _fixed_case_inputs(args)
        if args.command == "build-panel":
            unique = {(item["dataset_snapshot_id"], item["oos_start"], item["oos_end"], item["lockbox_start"]) for item in identities.values()}
            if len(unique) != 1:
                parser.error("fixed Cases do not share dataset/OOS/lockbox identity")
            _, start, end, lockbox = next(iter(unique))
            first = cases[sorted(cases)[0]]
            snapshot = json.loads((first / "dataset_snapshot_manifest.json").read_text(encoding="utf-8"))
            source = CompositeParquetMarketData(args.model_data.resolve(), ("trend_cache", "tushare_daily_cache", "tushare_incremental_cache"))
            manifest_path = build_comparison_panel(source, first / "dataset_snapshot_manifest.json", snapshot["symbols"],
                start=date.fromisoformat(start), end=date.fromisoformat(end), lockbox_start=date.fromisoformat(lockbox), output_dir=args.panel_dir)
            result = json.loads(manifest_path.read_text(encoding="utf-8"))
        elif args.command == "prepare":
            result = build_comparison_protocol(cases, args.market_panel.resolve(), args.protocol.resolve(), result_path=args.result.resolve(), seed=args.seed)
        else:
            result = run_comparison(args.protocol.resolve(), args.output.resolve())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
