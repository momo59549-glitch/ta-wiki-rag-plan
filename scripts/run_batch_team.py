"""对本地股票池分批创建研究案例，支持 checkpoint 续跑。"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.agents import FileResearchTeam, TeamConfig
from packages.market_data import CompositeParquetMarketData, LocalParquetMarketData
from packages.research import PipelineConfig
from packages.research.batch import run_in_batches
from packages.rule_dsl import compile_rule
from packages.rules import get_rule


def _date(value: str) -> date:
    return date.fromisoformat(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="分批运行文件型研究团队")
    parser.add_argument("--model-data", type=Path, default=Path(r"H:\股票模型\Model\data"))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--composite", action="store_true", help="组合主缓存和 Tushare 补齐缓存，主缓存优先")
    parser.add_argument("--dataset", default="trend_cache")
    parser.add_argument("--fallback-dataset", default="tushare_daily_cache")
    parser.add_argument("--universe-manifest", type=Path, help="按每个 Observation 日期执行历史在市筛选")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--checkpoint", type=Path, default=Path("data/batch_checkpoints/hammer.json"))
    parser.add_argument("--output", type=Path, default=Path("data/research_cases"))
    parser.add_argument("--start", type=_date, required=True)
    parser.add_argument("--end", type=_date, required=True)
    parser.add_argument("--oos-start", type=_date, required=True)
    args = parser.parse_args()
    source = CompositeParquetMarketData(args.model_data, (args.dataset, args.fallback_dataset)) if args.composite else LocalParquetMarketData(args.model_data, args.dataset)
    symbols = source.symbols(args.limit)
    pipeline = PipelineConfig(start=args.start, end=args.end, out_of_sample_start=args.oos_start, universe_manifest=str(args.universe_manifest) if args.universe_manifest else None)
    team = FileResearchTeam(source, args.output)
    rule = compile_rule(get_rule("hammer"))
    def runner(chunk: list[str]) -> dict:
        case = team.run(chunk, rule, TeamConfig(pipeline))
        return {"case_dir": str(case)}
    results = run_in_batches(symbols, args.batch_size, args.checkpoint, runner)
    print({"completed": sum(item.status == "completed" for item in results), "failed": sum(item.status == "failed" for item in results), "checkpoint": str(args.checkpoint)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
