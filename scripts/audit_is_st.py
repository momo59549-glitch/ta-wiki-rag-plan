"""Audit cached ``is_st`` flags against an independent ST timeline."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from packages.market_data import CompositeParquetMarketData, LocalParquetMarketData, active_on, load_universe_memberships
from packages.market_data.st_status import audit_is_st
from packages.research.json_store import write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-data", type=Path, default=Path(r"H:\股票模型\Model\data"))
    parser.add_argument("--datasets", type=str, default="trend_cache,tushare_daily_cache,tushare_incremental_cache")
    parser.add_argument("--st-manifest", type=Path)
    parser.add_argument("--symbol-limit", type=int, default=200)
    parser.add_argument("--output", type=Path, default=Path("data/audit/is_st_audit.json"))
    args = parser.parse_args()
    source = CompositeParquetMarketData(args.model_data, tuple(item.strip() for item in args.datasets.split(",") if item.strip()))
    symbols = source.symbols()[: args.symbol_limit]
    result = audit_is_st(
        model_data=args.model_data,
        datasets=source.datasets,
        symbols=symbols,
        st_manifest=args.st_manifest,
    )
    write_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
