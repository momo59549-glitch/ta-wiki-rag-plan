"""Bootstrap the first reviewed-book KnowledgeCard drafts; never auto-publish."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from packages.knowledge.book_bootstrap import bootstrap_first_candlestick_cards


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True, help="EPUB import manifest JSON")
    parser.add_argument("--knowledge-root", type=Path, default=Path("data/knowledge"))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = bootstrap_first_candlestick_cards(
        args.manifest,
        args.knowledge_root,
        dry_run=args.dry_run,
    )
    print(json.dumps({**result, "publication": "blocked_until_content_review"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
