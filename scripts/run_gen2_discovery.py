"""Preregister a Gen2 context-wrapper generation without screening any outcomes.

This command intentionally has no market-data argument and no backtest mode.
It only freezes candidates, a future validation boundary, and a global trial
ledger entry.  Use --dry-run for the safe default preview.
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import json
import sys
from typing import Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.research.gen2_discovery import (
    GLOBAL_LEDGER_POLICY_SCHEMA,
    Gen2Config,
    Gen2Periods,
    build_gen2_protocol,
    canonical_hash,
    historical_trial_inventory,
    initialize_global_trial_ledger,
    load_gen1_candidate_references,
    load_global_trial_ledger,
    preregister_gen2_generation,
    verify_gen1_protocol,
)


def _date(value: str) -> date:
    return date.fromisoformat(value)


def _parent_protocol(path: Path) -> tuple[dict, date, str, int]:
    payload = verify_gen1_protocol(path)
    return (
        payload,
        _date(str(payload["periods"]["research_end"])),
        str(payload["protocol_hash"]),
        int(payload["generation"]["candidate_budget"]),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--parent-protocol", type=Path, required=True)
    parser.add_argument("--parent-closure-result", type=Path, required=True)
    parser.add_argument("--research-start", type=_date, required=True)
    parser.add_argument("--validation-start", type=_date, required=True)
    parser.add_argument("--research-end", type=_date, required=True)
    parser.add_argument("--lockbox-start", type=_date, required=True)
    parser.add_argument("--candidate-budget", type=int, default=8)
    parser.add_argument("--global-trial-budget", type=int, required=True)
    parser.add_argument("--benchmark-symbol", default="000300")
    parser.add_argument("--seed", type=int, default=20260810)
    parser.add_argument("--ledger-root", type=Path, default=Path("data/research_trial_ledger"))
    parser.add_argument("--history-root", type=Path, default=REPOSITORY_ROOT, help="只读扫描既有 outcome-touched trial artifacts")
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="只打印预注册协议，绝不写文件（默认建议）")
    args = parser.parse_args(argv)
    if not args.dry_run and args.output_root is None:
        parser.error("写入预注册产物时必须提供 --output-root；或改用 --dry-run")
    try:
        parent, parent_end, parent_hash, _ = _parent_protocol(args.parent_protocol)
        inventory = historical_trial_inventory(args.history_root)
        legacy_count = int(inventory["unique_rule_logic_count"])
        config = Gen2Config(
            generation_id=args.generation_id,
            parent_generation_id=str(parent["generation"]["generation_id"]),
            parent_protocol_hash=parent_hash,
            candidate_budget=args.candidate_budget,
            benchmark_symbol=args.benchmark_symbol,
            seed=args.seed,
        )
        periods = Gen2Periods(args.research_start, args.validation_start, args.research_end, args.lockbox_start)
        if args.dry_run and not (args.ledger_root / "policy.json").exists():
            policy = {
                "schema_version": GLOBAL_LEDGER_POLICY_SCHEMA,
                "global_trial_budget": args.global_trial_budget,
                "legacy_trial_count": legacy_count,
                "legacy_inventory_hash": inventory["inventory_hash"],
                "append_only_entries": "entries/<generation_id>.json",
                "final_lockbox_consumption": "forbidden",
            }
            policy["policy_hash"] = canonical_hash(policy)
            ledger = {"policy": policy, "entries": [], "used_trial_count": legacy_count, "remaining_trial_count": args.global_trial_budget - legacy_count}
            if ledger["remaining_trial_count"] < 0:
                raise ValueError("global trial budget 小于 Gen1 已占用试验数")
        else:
            initialize_global_trial_ledger(args.ledger_root, global_trial_budget=args.global_trial_budget, legacy_trial_count=legacy_count, legacy_inventory_hash=inventory["inventory_hash"])
            ledger = load_global_trial_ledger(args.ledger_root)
        protocol = build_gen2_protocol(
            config, periods, parent_research_end=parent_end, global_ledger=ledger,
            gen1_references=load_gen1_candidate_references(args.parent_protocol),
            parent_closure=__import__("packages.research.gen2_discovery", fromlist=["verify_parent_generation_closure"]).verify_parent_generation_closure(args.parent_closure_result),
        )
        if args.dry_run:
            print(json.dumps({"dry_run": True, "writes": False, "protocol": protocol}, ensure_ascii=False, default=str, sort_keys=True))
        else:
            result = preregister_gen2_generation(protocol, output_root=args.output_root, ledger_root=args.ledger_root, parent_protocol_path=args.parent_protocol, parent_closure_result_path=args.parent_closure_result)
            print(json.dumps({"dry_run": False, "screen_or_backtest": "forbidden", **result}, ensure_ascii=False, default=str, sort_keys=True))
    except (FileExistsError, FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
