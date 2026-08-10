"""Run one immutable, outcome-blind automatic DSL-discovery generation.

The command only creates research-screening evidence.  It cannot approve,
publish, or execute a rule; any promotion still requires a separate frozen
Campaign, final lockbox review, and human approval.
"""
from __future__ import annotations

import argparse
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.market_data import LocalParquetMarketData, load_point_in_time_universe
from packages.research.auto_discovery import (
    DiscoveryConfig,
    available_candidate_capacity,
    archived_discovery_semantic_hashes,
    run_auto_discovery,
)
from packages.research.historical_trials import scan_historical_trial_references
from packages.research.rule_search import SearchConfig


def _date(value: str) -> date:
    return date.fromisoformat(value)


def _load_parent_registry(path: Path) -> tuple[dict, set[str], str]:
    if not path.is_file():
        raise FileNotFoundError(f"父代状态注册表不存在: {path}")
    raw = path.read_bytes()
    payload = json.loads(raw)
    return payload, archived_discovery_semantic_hashes(payload), "sha256:" + sha256(raw).hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-data", type=Path, default=Path(r"H:\股票模型\Model\data"))
    parser.add_argument("--output-root", type=Path, required=True)
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
    parser.add_argument("--stress-multipliers", type=str, default="2,3")
    parser.add_argument("--min-horizons", type=int, default=2)
    parser.add_argument("--dedup-jaccard", type=float, default=0.85)
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--candidate-budget", type=int, default=64)
    parser.add_argument("--seed", type=int, default=20260809)
    parser.add_argument("--max-ast-nodes", type=int, default=24)
    parser.add_argument("--max-conditions", type=int, default=3)
    parser.add_argument("--revalidation-days", type=int, default=90)
    parser.add_argument("--min-revalidation-observations", type=int, default=100)
    parser.add_argument("--min-mean-net-excess-return", type=float, default=0.0)
    parser.add_argument("--max-mean-return-drop", type=float, default=0.02)
    parser.add_argument("--parent-registry", type=Path, default=None)
    parser.add_argument("--parent-generation-id", type=str, default=None)
    parser.add_argument("--parent-archive-id", type=str, default=None)
    parser.add_argument("--prior-cumulative-budget", type=int, default=None)
    parser.add_argument("--known-logic-project-root", type=Path, default=REPOSITORY_ROOT, help="扫描 catalog 与已裁决 Campaign 的只读根目录")
    args = parser.parse_args(argv)

    if args.output_root.exists():
        parser.error(f"自动发现输出目录已存在，拒绝重跑或覆盖: {args.output_root}")
    if args.symbol_limit < 1:
        parser.error("--symbol-limit 必须为正整数")

    excluded: set[str] = set()
    parent_registry: dict | None = None
    parent_archive_id = args.parent_archive_id
    parent_generation_id = args.parent_generation_id
    prior_budget = args.prior_cumulative_budget
    if args.parent_registry:
        try:
            parent_registry, excluded, archive_hash = _load_parent_registry(args.parent_registry)
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
            parser.error(str(exc))
        parent_generation = str(parent_registry["generation"]["generation_id"])
        if parent_generation_id is not None and parent_generation_id != parent_generation:
            parser.error("--parent-generation-id 与 --parent-registry 的 generation_id 不一致")
        parent_generation_id = parent_generation
        parent_archive_id = parent_archive_id or archive_hash
        prior_budget = prior_budget if prior_budget is not None else int(parent_registry["generation"]["cumulative_candidate_budget"])
        parent_periods = parent_registry["periods"]
        prior_end = _date(str(parent_periods["research_end"]))
        prior_lockbox = _date(str(parent_periods["final_lockbox_start"]))
        if args.oos_start <= prior_end:
            parser.error("下一代必须使用父代研究结束日之后的新验证窗口")
        if args.lockbox_start == prior_lockbox:
            parser.error("下一代必须登记新的最终锁箱边界")
    if (parent_generation_id is None) != (parent_archive_id is None):
        parser.error("父代 generation_id 与 archive_id 必须同时提供，或同时省略")
    if prior_budget is None:
        prior_budget = 0

    try:
        historical_index = scan_historical_trial_references(args.known_logic_project_root)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        parser.error(f"无法建立 catalog/历史 trial 逻辑去重索引: {exc}")
    if historical_index["errors"]:
        parser.error("catalog/历史 trial 逻辑去重索引包含不可验证记录: " + json.dumps(historical_index["errors"], ensure_ascii=False))
    excluded_logic_hashes = {
        item["rule_logic_hash"]
        for item in historical_index["catalog_references"] + historical_index["historical_trial_references"]
    }

    try:
        search_config = SearchConfig(
            horizons=tuple(int(item) for item in args.horizons.split(",") if item.strip()),
            start=args.start,
            end=args.end,
            out_of_sample_start=args.oos_start,
            lockbox_start=args.lockbox_start,
            commission_bps_per_side=args.commission_bps,
            slippage_bps_per_side=args.slippage_bps,
            market_regime_window=args.regime_window,
            min_signal_amount=args.min_signal_amount,
            min_out_of_sample_observations=args.min_samples,
            cost_stress_multipliers=tuple(float(item) for item in args.stress_multipliers.split(",") if item.strip()),
            require_multiple_horizons=args.min_horizons,
            dedup_jaccard=args.dedup_jaccard,
        )
        discovery_config = DiscoveryConfig(
            generation_id=args.generation_id,
            candidate_budget=args.candidate_budget,
            seed=args.seed,
            max_ast_nodes=args.max_ast_nodes,
            max_conditions=args.max_conditions,
            parent_generation_id=parent_generation_id,
            parent_archive_id=parent_archive_id,
            prior_cumulative_candidate_budget=prior_budget,
            revalidation_days=args.revalidation_days,
            min_revalidation_observations=args.min_revalidation_observations,
            min_mean_net_excess_return=args.min_mean_net_excess_return,
            max_mean_return_drop=args.max_mean_return_drop,
        )
    except ValueError as exc:
        parser.error(str(exc))

    remaining_capacity = available_candidate_capacity(
        discovery_config,
        excluded_semantic_hashes=excluded,
        excluded_logic_hashes=excluded_logic_hashes,
    )
    if discovery_config.candidate_budget > remaining_capacity:
        parser.error(
            "--candidate-budget 超过当前代剩余 grammar 容量："
            f"请求 {discovery_config.candidate_budget}，可用 {remaining_capacity}。"
            "请降低预算或发布新的 grammar_version。"
        )

    try:
        active, universe_meta = load_point_in_time_universe(args.universe_manifest, args.end)
        symbols = active[: args.symbol_limit]
        result = run_auto_discovery(
            LocalParquetMarketData(args.model_data),
            symbols,
            search_config,
            discovery_config,
            args.output_root,
            universe_manifest=args.universe_manifest,
            excluded_semantic_hashes=excluded,
            excluded_logic_hashes=excluded_logic_hashes,
        )
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                **result,
                "universe": universe_meta,
                "parent_registry": str(args.parent_registry) if args.parent_registry else None,
                "remaining_grammar_capacity_before_run": remaining_capacity,
                "known_logic_deduplication": {
                    "catalog_references": len(historical_index["catalog_references"]),
                    "historical_trial_references": len(historical_index["historical_trial_references"]),
                    "excluded_logic_hashes": len(excluded_logic_hashes),
                },
                "automatic_execution": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
