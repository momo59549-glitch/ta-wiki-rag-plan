from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from packages.knowledge.stock_book_bootstrap import bootstrap_reviewed_stock_book_cards


def main() -> None:
    parser = argparse.ArgumentParser(description="生成并审核股票技术分析 PDF 规则卡")
    parser.add_argument("--schwager", type=Path, required=True)
    parser.add_argument("--bulkowski", type=Path, required=True)
    parser.add_argument("--knowledge-root", type=Path, default=Path("data/knowledge"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = bootstrap_reviewed_stock_book_cards(
        {"schwager": args.schwager, "bulkowski": args.bulkowski},
        args.knowledge_root,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
