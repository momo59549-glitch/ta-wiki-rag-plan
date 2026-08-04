from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.research.case_report import render_case_report


def main() -> int:
    parser = argparse.ArgumentParser(description="生成研究案例人工审阅报告")
    parser.add_argument("case_dir", type=Path)
    print(render_case_report(parser.parse_args().case_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
