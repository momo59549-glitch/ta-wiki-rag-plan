"""Safe CLI for Gen3 market-corpus quality metadata plans and one-file audits."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from packages.research.gen3_local_market import make_local_market_contract
from packages.research.gen3_quality_campaign import (
    audit_snapshot_file,
    build_corpus_snapshot,
    make_campaign_contract,
)


_CONTRACT_FIELDS = {
    "source_id", "root", "date_column", "open_column", "high_column", "low_column", "close_column", "volume_column",
}


def _contract(contract_json: str) -> object:
    values = json.loads(Path(contract_json).read_text(encoding="utf-8"))
    if not isinstance(values, dict) or set(values) != _CONTRACT_FIELDS:
        raise ValueError("contract JSON must contain exactly the local market source and six column fields")
    return make_local_market_contract(**values)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gen3 market quality metadata plan or explicit one-file audit")
    parser.add_argument("contract_json")
    parser.add_argument("--max-files", type=int, required=True)
    parser.add_argument("--max-rows-per-file", type=int, required=True)
    parser.add_argument("--max-issues-per-file", type=int, required=True)
    parser.add_argument("--audit-file", help="one explicit file already present in the metadata snapshot")
    try:
        args = parser.parse_args(argv)
        contract = _contract(args.contract_json)
        snapshot = build_corpus_snapshot(contract, max_files=args.max_files)  # type: ignore[arg-type]
        campaign = make_campaign_contract(snapshot, max_rows_per_file=args.max_rows_per_file, max_issues_per_file=args.max_issues_per_file)
        if args.audit_file is None:
            print(json.dumps({"status": "metadata_plan", "snapshot_hash": snapshot.snapshot_hash, "campaign_hash": campaign.campaign_hash, "file_count": len(snapshot.files), "write_policy": campaign.write_policy}, ensure_ascii=False))
            return 0
        report = audit_snapshot_file(snapshot, campaign, contract, args.audit_file)  # type: ignore[arg-type]
        print(json.dumps({"status": "single_file_audit", "snapshot_hash": snapshot.snapshot_hash, "campaign_hash": campaign.campaign_hash, "report_hash": report.report_hash, "file_path": report.file_path, "report_status": report.status, "rows_scanned": report.rows_scanned, "issues_encountered": report.issues_encountered, "truncated": report.truncated, "truncated_issues": report.truncated_issues}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
