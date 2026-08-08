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
from packages.market_data import CompositeParquetMarketData, LocalParquetMarketData
from packages.research import PipelineConfig
from packages.rule_dsl import compile_rule
from packages.rules import get_rule


def _date(value: str) -> date:
    return date.fromisoformat(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="运行文件型多 Agent 股票研究团队")
    parser.add_argument("--model-data", type=Path, default=Path(r"H:\股票模型\Model\data"))
    parser.add_argument("--dataset", default="trend_cache", help="单一缓存；与 --composite 联用时为首选缓存")
    parser.add_argument("--composite", action="store_true", help="组合 trend_cache 与 tushare_daily_cache，不复制源数据")
    parser.add_argument("--symbols", nargs="*", default=[])
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--rule", default="hammer")
    parser.add_argument("--start", type=_date)
    parser.add_argument("--end", type=_date)
    parser.add_argument("--oos-start", type=_date, required=True)
    parser.add_argument("--lockbox-start", type=_date, help="最终锁箱起始日；研究 end 必须早于该日期")
    parser.add_argument("--horizons", type=int, nargs="+", default=[1, 3, 5, 10, 20])
    parser.add_argument("--commission-bps", type=float, default=3.0)
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    parser.add_argument("--min-oos-observations", type=int, default=300)
    parser.add_argument("--regime-window", type=int, default=60)
    parser.add_argument("--min-signal-amount", type=float)
    parser.add_argument("--include-untradeable", action="store_true")
    parser.add_argument("--universe-manifest", type=Path, help="JSONL 历史股票池；缺失时结果仅作探索性研究")
    parser.add_argument("--universe-as-of", type=_date, help="仅用于静态预筛；历史研究默认按每个 Observation 日期筛选")
    parser.add_argument("--output", type=Path, default=Path("data/research_cases"))
    args = parser.parse_args()
    source = CompositeParquetMarketData(args.model_data, (args.dataset, "tushare_daily_cache", "tushare_incremental_cache")) if args.composite else LocalParquetMarketData(args.model_data, args.dataset)
    if args.universe_manifest:
        # Do not select with the end-date membership: the pipeline enforces
        # membership for every observation date.  The source's full file list
        # is the candidate set only.
        requested = set(args.symbols) if args.symbols else set(source.symbols())
        symbols = sorted(requested)
        if args.limit is not None:
            symbols = symbols[:args.limit]
    else:
        symbols = args.symbols or source.symbols(args.limit)
    pipeline = PipelineConfig(tuple(args.horizons), args.start, args.end, "000001", "etf_cache", args.commission_bps, args.slippage_bps, args.oos_start, args.regime_window, args.min_signal_amount, not args.include_untradeable, str(args.universe_manifest) if args.universe_manifest else None, args.lockbox_start)
    case_dir = FileResearchTeam(source, args.output).run(symbols, compile_rule(get_rule(args.rule)), TeamConfig(pipeline, args.min_oos_observations))
    case = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    print(json.dumps({"case_dir": str(case_dir), **case}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
