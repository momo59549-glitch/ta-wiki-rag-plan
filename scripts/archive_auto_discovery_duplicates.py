"""Archive catalog/historical logic duplicates from an auto-discovery registry.

The source registry is read only.  A new lifecycle-state artifact is written
with the historic rejection/canonical-catalog matches and a new state hash.
No market data, final lockbox, Campaign, approval, or publication is touched.
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

from packages.research.auto_discovery import archive_catalog_or_historical_duplicates
from packages.research.historical_trials import scan_historical_trial_references


def _date(value: str) -> date:
    return date.fromisoformat(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--as-of", type=_date, required=True)
    parser.add_argument("--project-root", type=Path, default=REPOSITORY_ROOT)
    args = parser.parse_args()
    audit_path = args.output.with_suffix(args.output.suffix + ".dedup_audit.json")
    if args.output.exists() or audit_path.exists():
        parser.error(f"deduplicated registry or audit already exists; refusing overwrite: {args.output}")
    try:
        registry = json.loads(args.registry.read_text(encoding="utf-8"))
        history = scan_historical_trial_references(args.project_root)
        if history["errors"]:
            parser.error("historical adjudication index contains unverifiable records: " + json.dumps(history["errors"], ensure_ascii=False))
        archived = archive_catalog_or_historical_duplicates(
            registry,
            catalog_references=history["catalog_references"],
            historical_trial_references=history["historical_trial_references"],
            as_of=args.as_of,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(archived, ensure_ascii=False, indent=2), encoding="utf-8")
        audit_path.write_text(
            json.dumps(
                {
                    "schema_version": "auto-discovery-logic-dedup-audit/v1",
                    "source_registry": str(args.registry.resolve()),
                    "output_registry": str(args.output.resolve()),
                    "as_of": args.as_of.isoformat(),
                    "history_index": history,
                    "matches": archived["logic_deduplication_history"][-1]["matches"],
                    "automatic_campaign_execution": False,
                    "publication": archived["publication"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except (FileExistsError, FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "registry": str(args.output.resolve()),
                "registry_state_hash": archived["registry_hash"],
                "lifecycle_revision": archived["lifecycle_revision"],
                "matches": archived["logic_deduplication_history"][-1]["matches"],
                "audit": str(audit_path.resolve()),
                "automatic_campaign_execution": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
