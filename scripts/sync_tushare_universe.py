"""Generate a local historical A-share universe from Tushare stock_basic."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.market_data import build_tushare_universe_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="通过 TUSHARE_TOKEN 建立历史 A 股股票池清单")
    parser.add_argument("--output", type=Path, default=Path("data/universes/a_share_history.jsonl"))
    parser.add_argument("--progress", type=Path, default=Path("data/universes/a_share_history.progress.json"))
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--retries", type=int, default=3)
    args = parser.parse_args()
    print(json.dumps(build_tushare_universe_manifest(args.output, progress_path=args.progress, timeout_seconds=args.timeout, max_retries=args.retries), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
