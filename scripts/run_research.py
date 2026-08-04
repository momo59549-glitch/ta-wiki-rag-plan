"""运行无 SQL 的文件型研究闭环。"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.market_data import LocalParquetMarketData
from packages.research import FileResearchPipeline, PipelineConfig
from packages.rule_dsl import compile_rule
from packages.rules import get_rule


def _date(value: str) -> date:
    return date.fromisoformat(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="扫描本地 Parquet 并产出 Observation/Outcome")
    parser.add_argument("--model-data", type=Path, default=Path(r"H:\股票模型\Model\data"))
    parser.add_argument("--dataset", default="trend_cache")
    parser.add_argument("--symbols", nargs="*", default=[])
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--rule", default="hammer")
    parser.add_argument("--start", type=_date)
    parser.add_argument("--end", type=_date)
    parser.add_argument("--horizons", type=int, nargs="+", default=[1, 3, 5, 10, 20])
    parser.add_argument("--benchmark-symbol", default="000001", help="etf_cache 中的指数代码；传空字符串关闭")
    parser.add_argument("--benchmark-dataset", default="etf_cache")
    parser.add_argument("--commission-bps", type=float, default=3.0, help="单边佣金（bps）")
    parser.add_argument("--slippage-bps", type=float, default=5.0, help="单边滑点（bps）")
    parser.add_argument("--oos-start", type=_date, help="样本外开始日期，例如 2024-01-01")
    parser.add_argument("--regime-window", type=int, default=60, help="市场状态均线窗口")
    parser.add_argument("--min-signal-amount", type=float, help="信号日最小成交额；不传则不筛选")
    parser.add_argument("--include-untradeable", action="store_true", help="保留不可成交样本并标记原因（默认跳过）")
    parser.add_argument("--output", type=Path, default=Path("data/research_runs"))
    args = parser.parse_args()

    source = LocalParquetMarketData(args.model_data, args.dataset)
    symbols = args.symbols or source.symbols(args.limit)
    pipeline = FileResearchPipeline(source, args.output)
    run_dir = pipeline.run(
        symbols,
        compile_rule(get_rule(args.rule)),
        PipelineConfig(tuple(args.horizons), args.start, args.end, args.benchmark_symbol or None, args.benchmark_dataset, args.commission_bps, args.slippage_bps, args.oos_start, args.regime_window, args.min_signal_amount, not args.include_untradeable),
    )
    summary = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    print(json.dumps({"run_dir": str(run_dir), **summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
