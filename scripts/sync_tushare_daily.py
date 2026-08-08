"""Synchronize historical daily bars missing from the local Model cache."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.market_data.tushare_daily import sync_missing_tushare_daily


def _date(value: str) -> date:
    return date.fromisoformat(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="断点续跑补齐 Tushare 缺失日线")
    parser.add_argument("--manifest", type=Path, default=Path("data/universes/a_share_history.jsonl"))
    parser.add_argument("--model-data", type=Path, default=Path(r"H:\股票模型\Model\data"))
    parser.add_argument("--existing-dataset", default="trend_cache")
    parser.add_argument("--output-dataset", default="tushare_daily_cache")
    parser.add_argument("--start", type=_date, default=date(1990, 1, 1))
    parser.add_argument("--end", type=_date, default=date.today())
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--st-manifest", type=Path)
    args = parser.parse_args()
    result = sync_missing_tushare_daily(
        manifest_path=args.manifest, existing_dataset_dir=args.model_data / args.existing_dataset,
        output_dataset_dir=args.model_data / args.output_dataset,
        checkpoint_path=Path("data/tushare_sync/a_share_daily.checkpoint.json"),
        progress_path=Path("data/tushare_sync/a_share_daily.progress.json"),
        start=args.start, end=args.end, limit=args.limit, timeout_seconds=args.timeout,
        max_retries=args.retries, delay_seconds=args.delay, st_manifest=args.st_manifest,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
