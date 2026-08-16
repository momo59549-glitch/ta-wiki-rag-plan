"""Explicit prepare/status/bounded-execute CLI for Gen3 quality runs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from packages.research.gen3_local_market import make_local_market_contract
from packages.research.gen3_quality_run import (
    execute_quality_run,
    prepare_from_contract,
    quality_run_status,
)


_CONTRACT_FIELDS = {"source_id", "root", "date_column", "open_column", "high_column", "low_column", "close_column", "volume_column"}


def _contract(path: str):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != _CONTRACT_FIELDS:
        raise ValueError("contract JSON must contain exactly the source, root, and six local market column fields")
    return make_local_market_contract(**value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gen3 resumable quality run")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("contract_json"); prepare.add_argument("--workspace-output-root", required=True); prepare.add_argument("--allowed-output-root", required=True)
    prepare.add_argument("--max-files", type=int, required=True); prepare.add_argument("--max-rows-per-file", type=int, required=True); prepare.add_argument("--max-issues-per-file", type=int, required=True)
    status = commands.add_parser("status")
    status.add_argument("contract_json"); status.add_argument("--run-dir", required=True); status.add_argument("--allowed-output-root", required=True)
    execute = commands.add_parser("execute")
    execute.add_argument("contract_json"); execute.add_argument("--run-dir", required=True); execute.add_argument("--allowed-output-root", required=True)
    execute.add_argument("--max-files-this-run", type=int, required=True); execute.add_argument("--confirm-read-source", action="store_true")
    try:
        args = parser.parse_args(argv); contract = _contract(args.contract_json)
        if args.command == "prepare":
            run_dir, snapshot, campaign = prepare_from_contract(contract, max_files=args.max_files, max_rows_per_file=args.max_rows_per_file, max_issues_per_file=args.max_issues_per_file, workspace_output_root=args.workspace_output_root, allowed_output_root=args.allowed_output_root)
            print(json.dumps({"status": "prepared", "run_dir": str(run_dir), "snapshot_hash": snapshot.snapshot_hash, "campaign_hash": campaign.campaign_hash}, ensure_ascii=False)); return 0
        if args.command == "status":
            print(json.dumps(quality_run_status(args.run_dir, contract, allowed_output_root=args.allowed_output_root).as_dict(), ensure_ascii=False)); return 0
        if not args.confirm_read_source:
            raise ValueError("execute requires --confirm-read-source")
        print(json.dumps(execute_quality_run(args.run_dir, contract, allowed_output_root=args.allowed_output_root, max_files_this_run=args.max_files_this_run).as_dict(), ensure_ascii=False)); return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False), file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())
