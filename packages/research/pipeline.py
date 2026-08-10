from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

from packages.contracts import Candle
from packages.market_data import LocalParquetMarketData, active_on, load_universe_memberships
from packages.research.json_store import write_json, write_jsonl
from packages.research.run_artifacts import (
    build_checkpoint,
    canonical_hash,
    load_commits,
    verify_checkpoint,
    write_batch,
)
from packages.research.execution import ExecutionConfig, assess_execution
from packages.research.indicators import candles_to_frame, compute_indicators
from packages.research.models import Observation, Outcome, ResearchRun
from packages.rule_dsl import CompiledRule, rule_definition_hash
from packages.rule_engine import evaluate


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    horizons: tuple[int, ...] = (1, 3, 5, 10, 20)
    start: date | None = None
    end: date | None = None
    benchmark_symbol: str | None = "000001"
    benchmark_dataset: str = "etf_cache"
    commission_bps_per_side: float = 3.0
    slippage_bps_per_side: float = 5.0
    out_of_sample_start: date | None = None
    market_regime_window: int = 60
    min_signal_amount: float | None = None
    skip_untradeable: bool = True
    universe_manifest: str | None = None
    lockbox_start: date | None = None

    def __post_init__(self) -> None:
        if not self.horizons or any(item < 1 for item in self.horizons):
            raise ValueError("horizons 必须是正整数")
        if self.commission_bps_per_side < 0 or self.slippage_bps_per_side < 0:
            raise ValueError("交易成本不能为负")
        if self.market_regime_window < 2:
            raise ValueError("市场状态窗口至少为 2")
        if self.min_signal_amount is not None and self.min_signal_amount <= 0:
            raise ValueError("最小成交额必须为正")
        if self.end and self.lockbox_start and self.end >= self.lockbox_start:
            raise ValueError("end 必须早于 lockbox_start，禁止读取最终锁箱")


class FileResearchPipeline:
    def __init__(self, source: LocalParquetMarketData, output_root: Path):
        self.source = source
        self.output_root = output_root

    @staticmethod
    def _observation_id(symbol: str, observed_at: datetime, semantic_hash: str, snapshot_id: str) -> str:
        payload = f"{symbol}|{observed_at.isoformat()}|{semantic_hash}|{snapshot_id}"
        return "obs_" + sha256(payload.encode()).hexdigest()[:24]

    @staticmethod
    def _outcome(
        observation: Observation,
        series: list[Candle],
        signal_index: int,
        horizon: int,
        benchmark_by_time: dict[datetime, Candle],
        regime_by_time: dict[datetime, str],
        config: PipelineConfig,
    ) -> Outcome | None:
        entry_index = signal_index + 1
        exit_index = entry_index + horizon - 1
        if exit_index >= len(series):
            return None
        window = series[entry_index:exit_index + 1]
        entry = window[0].open
        if entry <= 0:
            return None
        def execution_bar(candle: Candle) -> dict:
            bar = {"date": candle.timestamp, "open": candle.open, "close": candle.close, "pre_close": candle.prev_close, "is_st": candle.is_st}
            if candle.volume is not None:
                bar["volume"] = candle.volume
            if candle.amount is not None:
                bar["amount"] = candle.amount
            return bar
        # The opening order may only use information known at the open.  In
        # particular, do not use the entry day's close/volume/amount to filter
        # an opening fill: that would be a look-ahead selection bias.
        entry_check = assess_execution(
            execution_bar(window[0]), symbol=observation.symbol, side="buy",
            price_at="open", require_session_liquidity=False,
            config=ExecutionConfig(skip_untradeable=config.skip_untradeable),
        )
        exit_check = assess_execution(
            execution_bar(window[-1]), symbol=observation.symbol, side="sell",
            price_at="close", require_session_liquidity=True,
            config=ExecutionConfig(skip_untradeable=config.skip_untradeable),
        )
        if config.skip_untradeable and (entry_check.should_skip or exit_check.should_skip):
            return None
        exit_price = window[-1].close
        benchmark_return = None
        benchmark_entry = benchmark_by_time.get(window[0].timestamp)
        benchmark_exit = benchmark_by_time.get(window[-1].timestamp)
        if benchmark_entry and benchmark_exit and benchmark_entry.open > 0:
            benchmark_return = benchmark_exit.close / benchmark_entry.open - 1
        raw_return = exit_price / entry - 1
        total_cost = 2 * (config.commission_bps_per_side + config.slippage_bps_per_side) / 10_000
        net_return = raw_return - total_cost
        split = "out_of_sample" if config.out_of_sample_start and window[0].timestamp.date() >= config.out_of_sample_start else "in_sample"
        return Outcome(
            observation.id, horizon, window[0].timestamp, window[-1].timestamp,
            entry, exit_price, raw_return,
            max(item.high for item in window) / entry - 1,
            min(item.low for item in window) / entry - 1,
            benchmark_return,
            raw_return - benchmark_return if benchmark_return is not None else None,
            net_return,
            net_return - benchmark_return if benchmark_return is not None else None,
            split,
            regime_by_time.get(observation.observed_at, "unknown"),
            series[signal_index].amount,
            entry_check.executable,
            exit_check.executable,
            tuple(f"entry:{reason}" for reason in entry_check.reason_codes) + tuple(f"exit:{reason}" for reason in exit_check.reason_codes),
        )

    def run(
        self,
        symbols: list[str],
        rule: CompiledRule,
        config: PipelineConfig = PipelineConfig(),
        *,
        dataset_snapshot_id: str | None = None,
        dataset_snapshot_manifest: str | None = None,
        experiment_protocol_id: str | None = None,
        experiment_protocol_hash: str | None = None,
        code_snapshot_id: str | None = None,
        case_id: str | None = None,
        run_id: str | None = None,
        batch_size: int = 25,
        resume: bool = False,
        fault_injector=None,
    ) -> Path:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        symbols = sorted(set(symbols))
        memberships = load_universe_memberships(Path(config.universe_manifest)) if config.universe_manifest else None
        benchmark_by_time: dict[datetime, Candle] = {}
        regime_by_time: dict[datetime, str] = {}
        benchmark_id = None
        if config.benchmark_symbol:
            benchmark_source = LocalParquetMarketData(self.source.root, config.benchmark_dataset)
            benchmark = benchmark_source.load(config.benchmark_symbol, config.start, config.end)
            benchmark_by_time = {item.timestamp: item for item in benchmark}
            for index, item in enumerate(benchmark):
                if index + 1 < config.market_regime_window:
                    regime_by_time[item.timestamp] = "unknown"
                    continue
                moving_average = sum(row.close for row in benchmark[index + 1 - config.market_regime_window:index + 1]) / config.market_regime_window
                regime_by_time[item.timestamp] = "bullish" if item.close >= moving_average else "bearish"
            benchmark_id = benchmark_source.snapshot_id([config.benchmark_symbol])
        snapshot_id = dataset_snapshot_id or self.source.snapshot_id(symbols)
        if benchmark_id and dataset_snapshot_id is None:
            snapshot_id = "sha256:" + sha256(f"{snapshot_id}|{benchmark_id}".encode()).hexdigest()
        run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
        run_dir = self.output_root / run_id
        started_at = datetime.now(timezone.utc)
        progress_path = run_dir / "progress.json"
        identity = {
            "schema_version": "research-execution-identity/v1",
            "case_id": case_id,
            "run_id": run_id,
            "experiment_protocol_id": experiment_protocol_id,
            "experiment_protocol_hash": experiment_protocol_hash,
            "code_snapshot_id": code_snapshot_id,
            "dataset_snapshot_id": snapshot_id,
            "dataset_snapshot_manifest": dataset_snapshot_manifest,
            "rule_id": rule.definition.id,
            "rule_version": rule.definition.version,
            "rule_semantic_hash": rule.semantic_hash,
            "rule_definition_hash": rule_definition_hash(rule.definition),
            "symbols": symbols,
            "pipeline_config_hash": canonical_hash(asdict(config)),
            "batch_size": batch_size,
        }
        identity_hash = canonical_hash(identity)
        checkpoint_path = run_dir / "checkpoint.json"
        if resume:
            checkpoint, commits = verify_checkpoint(run_dir, identity)
            if checkpoint["status"] == "completed":
                raise ValueError("research run is already completed; resume is not allowed")
            started_at = datetime.fromisoformat(str(checkpoint["started_at"]))
        else:
            if run_dir.exists():
                raise FileExistsError(f"research run already exists: {run_dir}")
            commits = []
            write_json(checkpoint_path, build_checkpoint(identity, commits, status="running", started_at=started_at))
        write_json(progress_path, {
            "schema_version": "research-progress/v2",
            "run_id": run_id,
            "status": "running",
            "started_at": started_at,
            "updated_at": started_at,
            "symbols_total": len(symbols),
            "symbols_processed": sum(len(item["symbols"]) for item in commits),
            "symbols_loaded": sum(int(item["loaded_symbols"]) for item in commits),
            "observations": sum(int(item["observations"]["count"]) for item in commits),
            "outcomes": sum(int(item["outcomes"]["count"]) for item in commits),
            "percent": round(sum(len(item["symbols"]) for item in commits) / len(symbols) * 100, 2) if symbols else 100.0,
            "current_symbol": None,
            "committed_batches": len(commits),
            "resumed": resume,
        })
        try:
            for batch_index, start in enumerate(range(0, len(symbols), batch_size)):
                if batch_index < len(commits):
                    continue
                batch_symbols = symbols[start:start + batch_size]
                observations: list[Observation] = []
                outcomes: list[Outcome] = []
                skipped: list[str] = []
                loaded = 0
                for symbol in batch_symbols:
                    try:
                        series = self.source.load(symbol, config.start, config.end)
                    except (FileNotFoundError, ValueError):
                        skipped.append(symbol)
                        series = []
                    if series and len(series) <= rule.max_lookback + max(config.horizons):
                        skipped.append(symbol)
                        series = []
                    if not series:
                        continue
                    loaded += 1
                    indicators = None
                    if rule.required_indicators:
                        columns = compute_indicators(candles_to_frame(series), needs=rule.required_indicators)
                        indicators = {key: columns[key].tolist() for key in rule.required_indicators}
                    for index in range(rule.max_lookback, len(series) - 1):
                        result = evaluate(series, index, rule, indicators=indicators)
                        if result.status != "matched" or result.executable_from is None:
                            continue
                        if memberships is not None and not active_on(memberships, symbol, result.observed_at.date()):
                            continue
                        if config.min_signal_amount is not None and (series[index].amount is None or series[index].amount < config.min_signal_amount):
                            continue
                        observation = Observation(
                            self._observation_id(symbol, result.observed_at, result.semantic_hash, snapshot_id),
                            symbol, result.observed_at, result.executable_from,
                            rule.definition.id, rule.definition.version, result.semantic_hash,
                            snapshot_id, tuple(asdict(item) for item in result.conditions),
                        )
                        observations.append(observation)
                        for horizon in config.horizons:
                            outcome = self._outcome(observation, series, index, horizon, benchmark_by_time, regime_by_time, config)
                            if outcome:
                                outcomes.append(outcome)
                commit = write_batch(
                    run_dir, batch_index, batch_symbols, observations, outcomes, identity_hash,
                    loaded_symbols=loaded, skipped_symbols=skipped, fault_injector=fault_injector,
                )
                commits.append(commit)
                checkpoint = build_checkpoint(identity, commits, status="running", started_at=started_at)
                write_json(checkpoint_path, checkpoint)
                if fault_injector:
                    fault_injector("after_checkpoint", batch_index)
                processed = int(checkpoint["symbols_processed"])
                write_json(progress_path, {
                    "schema_version": "research-progress/v2", "run_id": run_id, "status": "running",
                    "started_at": started_at, "updated_at": datetime.now(timezone.utc),
                    "symbols_total": len(symbols), "symbols_processed": processed,
                    "symbols_loaded": checkpoint["symbols_loaded"], "observations": checkpoint["observations"],
                    "outcomes": checkpoint["outcomes"],
                    "percent": round(processed / len(symbols) * 100, 2) if symbols else 100.0,
                    "current_symbol": batch_symbols[-1] if batch_symbols else None,
                    "committed_batches": len(commits), "resumed": resume,
                })
        except BaseException:
            current = build_checkpoint(identity, load_commits(run_dir, identity_hash), status="interrupted", started_at=started_at)
            write_json(checkpoint_path, current)
            write_json(progress_path, {
                "schema_version": "research-progress/v2", "run_id": run_id, "status": "interrupted",
                "started_at": started_at, "updated_at": datetime.now(timezone.utc),
                "symbols_total": len(symbols), "symbols_processed": current["symbols_processed"],
                "symbols_loaded": current["symbols_loaded"], "observations": current["observations"],
                "outcomes": current["outcomes"],
                "percent": round(int(current["symbols_processed"]) / len(symbols) * 100, 2) if symbols else 100.0,
                "current_symbol": None, "committed_batches": current["committed_batches"], "resumed": resume,
            })
            raise
        checkpoint = build_checkpoint(identity, commits, status="running", started_at=started_at)
        summary = ResearchRun(
            run_id, datetime.now(timezone.utc), snapshot_id, rule.semantic_hash,
            len(symbols), int(checkpoint["symbols_loaded"]), int(checkpoint["observations"]), int(checkpoint["outcomes"]), tuple(checkpoint["skipped_symbols"]),
        )
        write_json(run_dir / "run.json", summary)
        write_json(run_dir / "config.json", {
            "horizons": config.horizons,
            "start": config.start,
            "end": config.end,
            "symbols": symbols,
            "source_root": str(self.source.root),
            "dataset": self.source.dataset,
            "benchmark_symbol": config.benchmark_symbol,
            "benchmark_dataset": config.benchmark_dataset if config.benchmark_symbol else None,
            "benchmark_snapshot_id": benchmark_id,
            "dataset_snapshot_manifest": dataset_snapshot_manifest,
            "experiment_protocol_id": experiment_protocol_id,
            "experiment_protocol_hash": experiment_protocol_hash,
            "code_snapshot_id": code_snapshot_id,
            "artifact_format": "research-sharded-run/v1",
            "batch_size": batch_size,
            "commission_bps_per_side": config.commission_bps_per_side,
            "slippage_bps_per_side": config.slippage_bps_per_side,
            "out_of_sample_start": config.out_of_sample_start,
            "lockbox_start": config.lockbox_start,
            "market_regime_window": config.market_regime_window,
            "min_signal_amount": config.min_signal_amount,
            "skip_untradeable": config.skip_untradeable,
            "universe": {
                "status": "point_in_time" if config.universe_manifest else "survivorship_unsafe",
                "manifest": config.universe_manifest,
                "enforcement": "per_observation_date" if config.universe_manifest else "none",
                "note": "未提供历史股票池清单时，当前 Parquet 文件列表只能用于探索性研究。",
            },
            "rule": asdict(rule.definition),
        })
        (run_dir / "report.md").write_text(
            self._report(summary, rule, config, iter_run_rows_after_commit(run_dir, commits, "outcomes")),
            encoding="utf-8",
        )
        # Compatibility views are streamed only after every batch is committed;
        # they are never resume authorities and do not grow process memory.
        write_jsonl(run_dir / "observations.jsonl", iter_run_rows_after_commit(run_dir, commits, "observations"))
        write_jsonl(run_dir / "outcomes.jsonl", iter_run_rows_after_commit(run_dir, commits, "outcomes"))
        write_json(run_dir / "artifact_manifest.json", {
            "schema_version": "research-artifact-manifest/v1",
            "execution_identity": identity,
            "execution_identity_hash": identity_hash,
            "committed_batches": len(commits),
            "observations": summary.observations,
            "outcomes": summary.outcomes,
            "commit_hashes": [item["commit_hash"] for item in commits],
        })
        write_json(checkpoint_path, build_checkpoint(identity, commits, status="completed", started_at=started_at))
        write_json(progress_path, {
            "schema_version": "research-progress/v2",
            "run_id": run_id,
            "status": "completed",
            "started_at": started_at,
            "updated_at": datetime.now(timezone.utc),
            "symbols_total": len(symbols),
            "symbols_processed": len(symbols),
            "symbols_loaded": summary.symbols_loaded,
            "observations": summary.observations,
            "outcomes": summary.outcomes,
            "percent": 100.0,
            "current_symbol": None,
            "committed_batches": len(commits),
            "resumed": resume,
        })
        return run_dir

    @staticmethod
    def _report(summary: ResearchRun, rule: CompiledRule, config: PipelineConfig, outcomes) -> str:
        aggregates: dict[tuple[str, int], dict[str, float]] = {}
        regime_aggregates: dict[str, list[float]] = {"bullish": [0.0, 0.0], "bearish": [0.0, 0.0], "unknown": [0.0, 0.0]}
        for item in outcomes:
            split = item["sample_split"] if isinstance(item, dict) else item.sample_split
            horizon = int(item["horizon_bars"] if isinstance(item, dict) else item.horizon_bars)
            values = item if isinstance(item, dict) else asdict(item)
            for section in ("all", split):
                bucket = aggregates.setdefault((section, horizon), {"count": 0.0, "wins": 0.0, "net": 0.0, "bench_sum": 0.0, "bench_n": 0.0, "excess_sum": 0.0, "excess_n": 0.0, "mfe": 0.0, "mae": 0.0})
                bucket["count"] += 1
                bucket["wins"] += float(values["net_return"] > 0)
                bucket["net"] += float(values["net_return"])
                bucket["mfe"] += float(values["mfe"])
                bucket["mae"] += float(values["mae"])
                if values.get("benchmark_return") is not None:
                    bucket["bench_sum"] += float(values["benchmark_return"]); bucket["bench_n"] += 1
                if values.get("net_excess_return") is not None:
                    bucket["excess_sum"] += float(values["net_excess_return"]); bucket["excess_n"] += 1
            if split == "out_of_sample" and horizon == 3 and values.get("net_excess_return") is not None:
                regime = str(values.get("market_regime", "unknown")); target = regime_aggregates.setdefault(regime, [0.0, 0.0])
                target[0] += 1; target[1] += float(values["net_excess_return"])
        lines = [
            f"# 研究运行 {summary.run_id}", "",
            "## 可复现身份", "",
            f"- 数据快照：`{summary.dataset_snapshot_id}`",
            f"- 规则：`{rule.definition.id}@{rule.definition.version}`",
            f"- 规则语义：`{summary.rule_semantic_hash}`",
            f"- 日期：`{config.start or '不限'} → {config.end or '不限'}`",
            f"- 请求/载入标的：{summary.symbols_requested}/{summary.symbols_loaded}",
            f"- Observation：{summary.observations}",
            f"- Outcome：{summary.outcomes}", "",
            f"- 单边佣金/滑点：{config.commission_bps_per_side:.1f}/{config.slippage_bps_per_side:.1f} bps", "",
        ]
        sections = [("全样本", "all"), ("样本内", "in_sample")]
        if config.out_of_sample_start:
            sections.append(("样本外", "out_of_sample"))
        for title, section in sections:
            lines.extend([f"## {title}分周期结果", "", "| 周期 | 样本 | 胜率 | 平均净收益 | 平均基准 | 平均净超额 | 平均 MFE | 平均 MAE |", "|---:|---:|---:|---:|---:|---:|---:|---:|"])
            for horizon in config.horizons:
                group = aggregates.get((section, horizon))
                if not group:
                    lines.append(f"| {horizon} | 0 | - | - | - | - | - | - |")
                    continue
                count = int(group["count"])
                benchmark_text = f"{group['bench_sum'] / group['bench_n']:.2%}" if group["bench_n"] else "-"
                net_excess_text = f"{group['excess_sum'] / group['excess_n']:.2%}" if group["excess_n"] else "-"
                lines.append(f"| {horizon} | {count} | {group['wins'] / count:.2%} | {group['net'] / count:.2%} | {benchmark_text} | {net_excess_text} | {group['mfe'] / count:.2%} | {group['mae'] / count:.2%} |")
            lines.append("")
        if config.out_of_sample_start:
            lines.extend(["## 样本外市场状态（平均净超额）", "", "| 市场状态 | 3 日样本 | 3 日平均净超额 |", "|---|---:|---:|"])
            for regime in ("bullish", "bearish", "unknown"):
                count, total = regime_aggregates[regime]
                lines.append(f"| {regime} | {int(count)} | {total / count:.2%} |" if count else f"| {regime} | 0 | - |")
            lines.append("")
        lines.extend([
            "", "## 限制", "",
            "- 本报告是描述性 Outcome 汇总，不代表因果或投资建议。",
            "- 超额收益以本地 000001 指数（如配置）同日开盘到收盘计算；缺失交易日不强行对齐。",
            "- 净收益已扣双边佣金与滑点；默认跳过停牌、无量/无额与买入涨停/卖出跌停的日线近似不可成交样本。",
            "- 样本外分段用于报告隔离；尚未实现参数训练/筛选自动化。",
            "- 市场状态只使用信号日及其历史指数收盘价计算；成交额过滤使用信号日数据。",
            "- JSONL 文件是逐条审计明细；失败和跳过标的保留在 `run.json`。",
            "",
        ])
        return "\n".join(lines)


def iter_run_rows_after_commit(run_dir: Path, commits: list[dict], kind: str):
    """Stream validated in-process commits before the final manifest exists."""
    for commit in commits:
        path = run_dir / commit[kind]["path"]
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line)
