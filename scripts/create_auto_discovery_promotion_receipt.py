"""Create one explicit, research-only frozen-Campaign selection receipt.

This command never approves/publishes a rule and never executes a Campaign.
It binds a caller-selected active registry candidate to its exact definition
before the separate campaign-derivation command may consume it.
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

from packages.research.promotion import build_auto_discovery_promotion_receipt, write_new_promotion_receipt


def _date(value: str) -> date:
    return date.fromisoformat(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rule-semantic-hash", required=True)
    parser.add_argument("--market-regime", choices=("bullish", "bearish", "unknown"), required=True)
    parser.add_argument("--as-of", type=_date, required=True)
    parser.add_argument("--selector", required=True, help="明确选择该研究试验的人工/委托主体")
    parser.add_argument("--rationale", required=True, help="保留该代表候选的可审计理由")
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f"receipt already exists; refusing overwrite: {args.output}")
    try:
        registry = json.loads(args.registry.read_text(encoding="utf-8"))
        receipt = build_auto_discovery_promotion_receipt(
            registry,
            rule_semantic_hash=args.rule_semantic_hash,
            market_regime=args.market_regime,
            as_of=args.as_of,
            selector=args.selector,
            rationale=args.rationale,
        )
        write_new_promotion_receipt(args.output, receipt)
    except (FileExistsError, FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "receipt": str(args.output.resolve()),
                "receipt_id": receipt["receipt_id"],
                "rule_semantic_hash": receipt["selected_rule_semantic_hash"],
                "rule_logic_hash": receipt["selected_rule_logic_hash"],
                "approval": receipt["approval"],
                "publication": receipt["publication"],
                "automatic_campaign_execution": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
