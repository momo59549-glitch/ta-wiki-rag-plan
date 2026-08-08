"""Create an auditable all-market summary from completed file research cases."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.research.market_summary import aggregate_market_cases, render_market_summary


def main() -> int:
    parser = argparse.ArgumentParser(description="汇总全市场研究案例")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = aggregate_market_cases(args.cases)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output / "REPORT.md").write_text(render_market_summary(summary), encoding="utf-8")
    print(json.dumps({"case_count": summary["case_count"], "outcomes_out_of_sample": summary["outcomes_out_of_sample"], "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
