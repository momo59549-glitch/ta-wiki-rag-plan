"""Build an independent ST-status timeline from Tushare namechange records."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from packages.market_data import build_st_timeline


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe-manifest", type=Path, default=Path("data/universes/a_share_history.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("data/manifests/st_timeline.jsonl"))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--delay", type=float, default=0.2)
    args = parser.parse_args()
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        parser.error("未设置 TUSHARE_TOKEN；请通过环境变量提供")
    result = build_st_timeline(
        token=token,
        universe_manifest=args.universe_manifest,
        output_path=args.output,
        timeout_seconds=args.timeout,
        max_retries=args.retries,
        delay_seconds=args.delay,
        limit=args.limit,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
