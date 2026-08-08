"""Fetch daily market increments into a separate overlay dataset."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path

from packages.market_data import sync_tushare_incremental


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path("data/universes/a_share_history.jsonl"))
    parser.add_argument("--model-data", type=Path, default=Path(r"H:\股票模型\Model\data"))
    parser.add_argument("--start", type=date.fromisoformat, default=date.today() - timedelta(days=7))
    parser.add_argument("--end", type=date.fromisoformat, default=date.today())
    parser.add_argument("--st-manifest", type=Path)
    args = parser.parse_args()
    result = sync_tushare_incremental(manifest_path=args.manifest, output_dataset_dir=args.model_data / "tushare_incremental_cache", checkpoint_path=Path("data/tushare_sync/incremental.checkpoint.json"), progress_path=Path("data/tushare_sync/incremental.progress.json"), start=args.start, end=args.end, st_manifest=args.st_manifest)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
