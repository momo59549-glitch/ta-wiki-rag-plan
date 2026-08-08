"""Run one bounded, preregistered automatic rule-search round (screen only)."""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from packages.market_data import LocalParquetMarketData, load_point_in_time_universe
from packages.research.json_store import write_json
from packages.research.rule_search import SearchConfig, build_search_protocol, build_search_space, screen_candidates, search_space_summary


def _date(value: str) -> date:
    return date.fromisoformat(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-data", type=Path, default=Path(r"H:\股票模型\Model\data"))
    parser.add_argument("--output-root", type=Path, default=Path("data/rule_search"))
    parser.add_argument("--start", type=_date, required=True)
    parser.add_argument("--end", type=_date, required=True)
    parser.add_argument("--oos-start", type=_date, required=True)
    parser.add_argument("--lockbox-start", type=_date, required=True)
    parser.add_argument("--universe-manifest", type=Path, default=Path("data/universes/a_share_history.jsonl"))
    parser.add_argument("--symbol-limit", type=int, default=300)
    parser.add_argument("--horizons", type=str, default="1,3,5,10,20")
    parser.add_argument("--min-samples", type=int, default=300)
    parser.add_argument("--commission-bps", type=float, default=3.0)
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    parser.add_argument("--regime-window", type=int, default=60)
    parser.add_argument("--min-signal-amount", type=float, default=None)
    args = parser.parse_args()

    horizons = tuple(int(item) for item in args.horizons.split(",") if item.strip())
    config = SearchConfig(
        horizons=horizons,
        start=args.start,
        end=args.end,
        out_of_sample_start=args.oos_start,
        lockbox_start=args.lockbox_start,
        commission_bps_per_side=args.commission_bps,
        slippage_bps_per_side=args.slippage_bps,
        market_regime_window=args.regime_window,
        min_signal_amount=args.min_signal_amount,
        min_out_of_sample_observations=args.min_samples,
    )
    active, universe_meta = load_point_in_time_universe(args.universe_manifest, args.end)
    symbols = active[: args.symbol_limit]
    definitions = build_search_space()
    print(f"搜索空间：{search_space_summary(definitions)['candidates']} 个候选，覆盖 {len(search_space_summary(definitions)['families'])} 个规则族")
    print(f"开发股票池：{len(symbols)} 只（{universe_meta['as_of']} 点时有效）")
    protocol = build_search_protocol(definitions, symbols, config, args.output_root, universe_manifest=args.universe_manifest)
    round_path = args.output_root / "round.json"
    if round_path.exists():
        parser.error(f"该搜索轮次已有执行记录，拒绝重跑: {round_path}")
    print(f"搜索协议：{protocol['search_id']}")
    source = LocalParquetMarketData(args.model_data)
    summary = screen_candidates(
        source,
        symbols,
        definitions,
        config,
        args.output_root,
        universe_manifest=args.universe_manifest,
    )
    lines = [
        "# 自动规则搜索轮次报告", "",
        f"- 搜索协议：`{protocol['search_id']}`",
        f"- 候选规则：{summary['candidates_total']} 个，通过筛选：{summary['passed_screen']} 个",
        f"- 开发股票池：{summary['loaded_symbols']} 只载入，{summary['skipped_symbols']} 只跳过",
        f"- 研究期：`{args.start}` → `{args.end}`，验证期：`{args.oos_start}` 起",
        f"- 最终锁箱：`{args.lockbox_start}` 起（本轮未读取）",
        "", "## 通过筛选的候选（按样本外平均净超额排序）", "",
        "| 规则 | 参数 | 周期 | 市场状态 | 样本外均值 | 样本 | FDR p | 信号数 |", "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in summary["best"]:
        group = item["best_group"]
        parameters = "; ".join(f"{k}={v}" for k, v in item["definition"]["parameters"].items()) or "-"
        lines.append(f"| {item['rule_id']}@{item['version']} | {parameters} | {group['horizon_bars']} | {group['market_regime']} | {group['mean_net_excess_return']:.4%} | {group['sample_size']} | {group['adjusted_p_value']:.4g} | {item['signals']} |")
    lines.extend([
        "", "## 说明", "",
        "- 统计为描述性汇总；FDR-BH 对同一轮全部候选、全部分组统一校正。",
        "- 通过筛选不等于规则有效：晋升必须走冻结 Campaign（强快照、walk-forward、锁箱与人工审批）。",
        "- 本报告不构成投资建议。",
        "",
    ])
    write_json(args.output_root / "report.json", summary)
    (args.output_root / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"完成：{summary['candidates_total']} 个候选，通过 {summary['passed_screen']} 个；报告: {args.output_root / 'report.md'}")
    for item in summary["best"]:
        group = item["best_group"]
        print(f"  {item['rule_id']}@{item['version']} {group['horizon_bars']}日/{group['market_regime']} 均值 {group['mean_net_excess_return']:.4%} FDR {group['adjusted_p_value']:.4g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
