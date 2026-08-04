from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.integrations.model_catalog import catalog_experiments


def main() -> int:
    parser = argparse.ArgumentParser(description="只读目录化历史 Model 实验 JSON")
    parser.add_argument("--experiments", type=Path, default=Path(r"H:\股票模型\Model\data\experiments"))
    parser.add_argument("--output", type=Path, default=Path("data/model_experiment_catalog.json"))
    args = parser.parse_args()
    result = catalog_experiments(args.experiments)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print({"entries": len(result["entries"]), "failures": len(result["failures"]), "output": str(args.output)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
