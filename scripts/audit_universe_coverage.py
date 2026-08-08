"""Report local price-file coverage for a historical universe manifest."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.market_data import audit_universe_price_coverage


def _date(value: str) -> date:
    return date.fromisoformat(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="审计股票池与本地行情覆盖率")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--model-data", type=Path, default=Path(r"H:\股票模型\Model\data"))
    parser.add_argument("--dataset", action="append", default=[], help="可重复指定；不提供时审计 trend_cache")
    parser.add_argument("--as-of", type=_date, help="只审计该日实际在市的股票，排除未来上市标的")
    parser.add_argument("--output", type=Path, default=Path("data/universes/a_share_history_coverage.json"))
    args = parser.parse_args()
    datasets = args.dataset or ["trend_cache"]
    result = audit_universe_price_coverage(args.manifest, tuple(args.model_data / item for item in datasets), args.as_of)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
