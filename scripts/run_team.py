"""运行无 SQL 的九 Agent 研究案例。"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.agents import FileResearchTeam, TeamConfig
from packages.market_data import LocalParquetMarketData
from packages.research import PipelineConfig
from packages.rule_dsl import compile_rule
from packages.rules import get_rule


def _date(value: str) -> date:
    return date.fromisoformat(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="运行文件型多 Agent 股票研究团队")
    parser.add_argument("--model-data", type=Path, default=Path(r"H:\股票模型\Model\data"))
    parser.add_argument("--dataset", default="trend_cache")
    parser.add_argument("--symbols", nargs="*", default=[])
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--rule", default="hammer")
    parser.add_argument("--start", type=_date)
    parser.add_argument("--end", type=_date)
    parser.add_argument("--oos-start", type=_date, required=True)
    parser.add_argument("--horizons", type=int, nargs="+", default=[1, 3, 5, 10, 20])
    parser.add_argument("--commission-bps", type=float, default=3.0)
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    parser.add_argument("--min-oos-observations", type=int, default=300)
    parser.add_argument("--regime-window", type=int, default=60)
    parser.add_argument("--min-signal-amount", type=float)
    parser.add_argument("--include-untradeable", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("data/research_cases"))
    args = parser.parse_args()
    source = LocalParquetMarketData(args.model_data, args.dataset)
    symbols = args.symbols or source.symbols(args.limit)
    pipeline = PipelineConfig(tuple(args.horizons), args.start, args.end, "000001", "etf_cache", args.commission_bps, args.slippage_bps, args.oos_start, args.regime_window, args.min_signal_amount, not args.include_untradeable)
    case_dir = FileResearchTeam(source, args.output).run(symbols, compile_rule(get_rule(args.rule)), TeamConfig(pipeline, args.min_oos_observations))
    case = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    print(json.dumps({"case_dir": str(case_dir), **case}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
