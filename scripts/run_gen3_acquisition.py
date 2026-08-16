"""Dry-run-only Gen3 acquisition planner. It never reads credential values or downloads."""
from __future__ import annotations
import argparse
from datetime import date
import json
import sys
from pathlib import Path

from packages.research.gen3_acquisition import SourceAcquisitionSpec, build_dry_run_plan, readiness
from packages.research.gen3_policy import DataClass


def _spec(value: dict[str, object]) -> SourceAcquisitionSpec:
    parsed = dict(value)
    parsed["domain"] = DataClass(parsed["domain"])
    parsed["start"] = date.fromisoformat(parsed["start"])
    parsed["end"] = date.fromisoformat(parsed["end"])
    return SourceAcquisitionSpec.from_mapping(parsed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gen3 acquisition dry-run only")
    parser.add_argument("spec_json")
    parser.add_argument("--target-root", required=True)
    parser.add_argument("--allowed-root", required=True)
    parser.add_argument("--max-records", required=True, type=int)
    parser.add_argument("--max-bytes", required=True, type=int)
    try:
        args = parser.parse_args(argv)
        payload = json.loads(Path(args.spec_json).read_text(encoding="utf-8"))
        if set(payload) != {"specs"} or not isinstance(payload["specs"], list) or not payload["specs"]: raise ValueError("input must be an object containing a non-empty specs list")
        specs = tuple(_spec(item) for item in payload["specs"])
        blocked = [reason for spec in specs for ready, reason in (readiness(spec),) if not ready]
        if blocked:
            print(json.dumps({"status": "blocked", "reasons": blocked}, ensure_ascii=False)); return 2
        plan = build_dry_run_plan(specs, target_root=args.target_root, allowed_root=args.allowed_root, max_records=args.max_records, max_bytes=args.max_bytes)
        print(json.dumps({"status": plan.status, "plan_hash": plan.plan_hash, "spec_hashes": [item.spec_hash for item in plan.specs]}, ensure_ascii=False)); return 0
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False), file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())
