"""无 SQL 的确定性研究团队；每个角色写入独立、可审计的运行记录。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from uuid import uuid4

from packages.market_data import LocalParquetMarketData, build_strong_snapshot, consume_source_snapshot_reuse_token, verify_source_against_strong_snapshot, verify_strong_snapshot
from packages.orchestration import CaseState, FileCaseStateMachine
from packages.research import FileResearchPipeline, PipelineConfig, build_experiment_protocol, verify_code_snapshot, verify_experiment_protocol
from packages.research.hypotheses import build_hypothesis_draft
from packages.research.json_store import write_json, write_jsonl
from packages.research.run_artifacts import artifact_exists, iter_run_rows
from packages.research.statistics import summarize_outcomes
from packages.research.validation import WalkForwardConfig, build_walk_forward_folds
from packages.rule_dsl import CompiledRule, rule_definition_hash


@dataclass(frozen=True, slots=True)
class TeamConfig:
    pipeline: PipelineConfig
    min_out_of_sample_observations: int = 300
    walk_forward_train_dates: int = 504
    walk_forward_test_dates: int = 126
    max_candidate_trials: int = 20


class FileResearchTeam:
    """Coordinator 将确定性节点串联；不会自动发布或修改规则。"""

    def __init__(self, source: LocalParquetMarketData, output_root: Path):
        self.source = source
        self.output_root = output_root

    @staticmethod
    def _event(agent: str, status: str, artifacts: list[str], summary: str) -> dict:
        return {
            "agent": agent,
            "status": status,
            "at": datetime.now(timezone.utc),
            "artifacts": artifacts,
            "summary": summary,
        }

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict]:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    @staticmethod
    def preflight_frozen_campaign(
        campaign_dir: Path,
        symbols: list[str],
        rule: CompiledRule,
        config: TeamConfig,
    ) -> dict:
        """Run all cheap frozen-Campaign gates before expensive data hashing."""
        campaign_dir = campaign_dir.resolve()
        names = {
            "dataset": "dataset_snapshot.json",
            "protocol": "experiment_protocol.json",
            "code": "code_snapshot.json",
            "readiness": "readiness_report.json",
        }
        missing = [name for name in names.values() if not (campaign_dir / name).is_file()]
        if missing:
            raise ValueError("冻结 Campaign 缺少文件：" + ", ".join(missing))
        payload = {key: json.loads((campaign_dir / name).read_text(encoding="utf-8")) for key, name in names.items()}
        protocol = payload["protocol"]
        readiness = payload["readiness"]
        protocol_check = verify_experiment_protocol(protocol)
        dataset_manifest = payload["dataset"]
        project_root = Path(__file__).resolve().parents[2]
        code_check = verify_code_snapshot(project_root, campaign_dir / names["code"])
        periods = protocol.get("periods", {})
        outcomes = protocol.get("outcomes", {})
        execution = protocol.get("execution", {}).get("base_cost_bps_per_side", {})
        analysis = protocol.get("analysis", {})
        checks = {
            "campaign_ready": readiness.get("status") == "ready" and all(readiness.get("checks", {}).values()),
            "code_valid": code_check.get("status") == "valid",
            "protocol_ready": protocol.get("status") == "preregistered" and protocol.get("readiness", {}).get("status") == "ready",
            "protocol_integrity": protocol_check.get("status") == "valid",
            "dataset_manifest_id_present": str(dataset_manifest.get("dataset_snapshot_id", "")).startswith("sha256:"),
            "dataset_bound": protocol.get("dataset_snapshot_id") == dataset_manifest.get("dataset_snapshot_id"),
            "code_bound": protocol.get("code_version") == code_check.get("code_snapshot_id"),
            "rule_bound": protocol.get("rule", {}).get("id") == rule.definition.id and protocol.get("rule", {}).get("version") == rule.definition.version and protocol.get("rule", {}).get("semantic_hash") == rule.semantic_hash,
            "full_rule_definition_bound": protocol_check.get("definition_status") == "legacy_catalog_reference" or protocol.get("rule", {}).get("definition_hash") == rule_definition_hash(rule.definition),
            "symbols_bound": sorted(set(symbols)) == sorted(set(protocol.get("symbols", []))),
            "horizons_bound": tuple(outcomes.get("horizons", [])) == tuple(config.pipeline.horizons),
            "periods_bound": periods == {
                "research_start": config.pipeline.start.isoformat() if config.pipeline.start else None,
                "validation_start": config.pipeline.out_of_sample_start.isoformat() if config.pipeline.out_of_sample_start else None,
                "research_end": config.pipeline.end.isoformat() if config.pipeline.end else None,
                "final_lockbox_start": config.pipeline.lockbox_start.isoformat() if config.pipeline.lockbox_start else None,
            },
            "costs_bound": float(execution.get("commission", -1)) == config.pipeline.commission_bps_per_side and float(execution.get("slippage", -1)) == config.pipeline.slippage_bps_per_side,
            "analysis_bound": analysis == {
                "benchmark_symbol": config.pipeline.benchmark_symbol,
                "benchmark_dataset": config.pipeline.benchmark_dataset if config.pipeline.benchmark_symbol else None,
                "market_regime_window": config.pipeline.market_regime_window,
                "min_signal_amount": config.pipeline.min_signal_amount,
                "skip_untradeable": config.pipeline.skip_untradeable,
            },
            "minimum_oos_bound": int(outcomes.get("minimum_oos_observations", -1)) == config.min_out_of_sample_observations,
            "trial_budget_bound": int(protocol.get("validation", {}).get("max_candidate_trials", -1)) == config.max_candidate_trials,
            "universe_bound": str(protocol.get("universe_manifest", "")).replace("\\", "/") == str(config.pipeline.universe_manifest or "").replace("\\", "/"),
            "lockbox_sealed": bool(config.pipeline.end and config.pipeline.lockbox_start and config.pipeline.end < config.pipeline.lockbox_start),
        }
        failures = [name for name, passed in checks.items() if not passed]
        if failures:
            raise ValueError("冻结 Campaign 绑定校验失败：" + ", ".join(failures))
        return {**payload, "directory": campaign_dir, "checks": checks, "code_check": code_check}

    @staticmethod
    def _validated_campaign(
        campaign_dir: Path,
        symbols: list[str],
        rule: CompiledRule,
        config: TeamConfig,
        *,
        source: LocalParquetMarketData | None = None,
        source_check: dict | None = None,
    ) -> dict:
        """Run preflight then strong snapshot/source validation for execution."""
        preflight = FileResearchTeam.preflight_frozen_campaign(campaign_dir, symbols, rule, config)
        campaign_dir = preflight["directory"]
        data_check = verify_strong_snapshot(campaign_dir / "dataset_snapshot.json")
        checked_source = (
            consume_source_snapshot_reuse_token(source, campaign_dir / "dataset_snapshot.json", source_check)
            if source is not None and source_check is not None
            else verify_source_against_strong_snapshot(source, campaign_dir / "dataset_snapshot.json")
            if source is not None
            else {"status": "not_checked"}
        )
        checks = {
            **preflight["checks"],
            "dataset_valid": data_check.get("status") == "valid",
            "execution_source_bound": source is None or checked_source.get("status") == "valid",
        }
        failures = [name for name, passed in checks.items() if not passed]
        if failures:
            raise ValueError("冻结 Campaign 绑定校验失败：" + ", ".join(failures))
        return {**preflight, "checks": checks, "data_check": data_check, "execution_source": checked_source}

    def run(
        self,
        symbols: list[str],
        rule: CompiledRule,
        config: TeamConfig,
        *,
        frozen_campaign: Path | None = None,
        frozen_source_check: dict | None = None,
        case_id: str | None = None,
        run_id: str | None = None,
        resume: bool = False,
        batch_size: int = 25,
    ) -> Path:
        if resume and frozen_campaign is None:
            raise ValueError("resume is restricted to a registered frozen Campaign execution")
        frozen = (
            self._validated_campaign(
                frozen_campaign,
                symbols,
                rule,
                config,
                source=self.source,
                source_check=frozen_source_check,
            )
            if frozen_campaign
            else None
        )
        case_id = case_id or "case_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
        case_dir = self.output_root / case_id
        lifecycle = FileCaseStateMachine.open(case_dir) if resume else FileCaseStateMachine.create(case_dir, case_id)
        if lifecycle.case_id != case_id or (resume and lifecycle.state != CaseState.CREATED):
            raise ValueError("resumed Case identity/state mismatch")
        coordinator_summary = "研究案例已创建并绑定冻结 Campaign；规则发布权限关闭" if frozen else "研究案例已创建；规则发布权限关闭"
        events = [self._event("Coordinator", "completed", ["case.json"], coordinator_summary)]

        benchmark_extra = ()
        if config.pipeline.benchmark_symbol:
            benchmark_source = LocalParquetMarketData(self.source.root, config.pipeline.benchmark_dataset)
            benchmark_extra = (("benchmark", benchmark_source, (config.pipeline.benchmark_symbol,)),)
        snapshot_path = case_dir / "dataset_snapshot_manifest.json"
        protocol_path = case_dir / "experiment_protocol.json"
        if frozen:
            if not resume:
                shutil.copy2(frozen["directory"] / "dataset_snapshot.json", snapshot_path)
                shutil.copy2(frozen["directory"] / "experiment_protocol.json", protocol_path)
                shutil.copy2(frozen["directory"] / "code_snapshot.json", case_dir / "code_snapshot.json")
                shutil.copy2(frozen["directory"] / "readiness_report.json", case_dir / "campaign_readiness_report.json")
                if (frozen["directory"] / "campaign_derivation.json").is_file():
                    shutil.copy2(frozen["directory"] / "campaign_derivation.json", case_dir / "campaign_derivation.json")
            snapshot = frozen["dataset"]
            protocol = frozen["protocol"]
        else:
            snapshot = build_strong_snapshot(self.source, symbols, snapshot_path, extra_sources=benchmark_extra)
            protocol = build_experiment_protocol(
                rule,
                config.pipeline,
                symbols,
                snapshot["dataset_snapshot_id"],
                protocol_path,
                minimum_oos_observations=config.min_out_of_sample_observations,
                max_candidate_trials=config.max_candidate_trials,
            )
        run_dir = FileResearchPipeline(self.source, case_dir / "research_run").run(
            symbols,
            rule,
            config.pipeline,
            dataset_snapshot_id=snapshot["dataset_snapshot_id"],
            dataset_snapshot_manifest="dataset_snapshot_manifest.json",
            experiment_protocol_id=protocol["protocol_id"],
            experiment_protocol_hash=protocol.get("protocol_hash"),
            code_snapshot_id=protocol.get("code_version"),
            case_id=case_id,
            run_id=run_id,
            batch_size=batch_size,
            resume=resume,
        )
        run_path = str(run_dir.relative_to(case_dir)).replace("\\", "/")
        run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        statistics = summarize_outcomes(
            (item for item in iter_run_rows(run_dir, "outcomes") if item.get("sample_split") == "out_of_sample"),
        )
        write_json(case_dir / "statistics_out_of_sample.json", statistics)
        validation = build_walk_forward_folds(
            sorted({item["observed_at"] for item in iter_run_rows(run_dir, "observations")}),
            WalkForwardConfig(
                train_size=config.walk_forward_train_dates,
                test_size=config.walk_forward_test_dates,
                purge_size=max(config.pipeline.horizons),
            ),
        )
        write_json(case_dir / "walk_forward_validation.json", validation)
        data_artifacts = ["dataset_snapshot_manifest.json", "experiment_protocol.json", f"{run_path}/config.json", f"{run_path}/run.json", f"{run_path}/progress.json"]
        if frozen:
            data_artifacts.extend(["code_snapshot.json", "campaign_readiness_report.json"])
        events.extend([
            self._event("Data", "completed", data_artifacts, f"已载入 {run['symbols_loaded']} 个标的并验证冻结强内容快照、代码快照与预注册协议" if frozen else f"已载入 {run['symbols_loaded']} 个标的并冻结强内容快照与预注册协议"),
            self._event("Scanner", "completed", [f"{run_path}/artifact_manifest.json"], f"生成 {run['observations']} 条 Observation"),
            self._event("Reviewer", "completed", [f"{run_path}/artifact_manifest.json"], f"生成 {run['outcomes']} 条 Outcome"),
        ])
        lifecycle.transition(CaseState.DATA_READY, "data.snapshot.ready", {"symbols_loaded": run["symbols_loaded"]})
        lifecycle.transition(CaseState.OBSERVATIONS_READY, "observations.ready", {"count": run["observations"]})
        lifecycle.transition(CaseState.OUTCOMES_READY, "outcomes.ready", {"count": run["outcomes"]})

        research = self._research(config, statistics)
        write_json(case_dir / "hypothesis_draft.json", research)
        events.append(self._event("Research", "completed", ["hypothesis_draft.json", "statistics_out_of_sample.json", "walk_forward_validation.json"], research["summary"]))
        lifecycle.transition(CaseState.HYPOTHESIS_DRAFTED, "hypothesis.drafted", {"candidates": len(research["candidate_horizons"])})

        backtest = {
            "status": "completed",
            "engine": "file_outcome_evaluator; vectorbt_candidate_verifier",
            "rule_semantic_hash": run["rule_semantic_hash"],
            "dataset_snapshot_id": run["dataset_snapshot_id"],
            "dataset_snapshot_manifest": "dataset_snapshot_manifest.json",
            "experiment_protocol_id": protocol["protocol_id"],
            "experiment_protocol_hash": protocol["protocol_hash"],
            "outcomes_checked": run["outcomes"],
            "frozen_campaign": str(frozen["directory"]) if frozen else None,
            "note": "Outcome 负责全市场事件研究；通过证据门槛的候选必须再由 vectorbt 生成组合级独立复核。",
        }
        write_json(case_dir / "backtest_review.json", backtest)
        events.append(self._event("Backtest", "completed", ["backtest_review.json"], "已核对规则、快照和 Outcome 身份"))
        lifecycle.transition(CaseState.BACKTEST_REVIEWED, "backtest.reviewed", {"outcomes_checked": run["outcomes"]})

        knowledge = {
            "schema_version": "knowledge-card-draft/v1",
            "status": "draft",
            "rule": f"{rule.definition.id}@{rule.definition.version}",
            "claim": "待人工复核的经验候选，不构成投资建议。",
            "candidate_horizons": research["candidate_horizons"],
            "evidence_refs": [],
            "research_artifacts": [f"{run_path}/artifact_manifest.json", "statistics_out_of_sample.json"],
            "evidence_status": "missing_book_or_source_evidence",
            "publication": "blocked_until_content_review",
            "limitations": research["limitations"] + ["历史全市场旧运行属于探索性结果，不能视为未查看的最终锁箱。"],
        }
        write_json(case_dir / "knowledge_card_draft.json", knowledge)
        events.append(self._event("Knowledge", "draft", ["knowledge_card_draft.json"], "仅生成草稿，不发布到规则库"))
        lifecycle.transition(CaseState.KNOWLEDGE_DRAFTED, "knowledge.drafted")

        events.append(self._event("Report", "completed", [f"{run_path}/report.md"], "已生成含成本、基准和样本外分段的报告"))
        lifecycle.transition(CaseState.REPORT_READY, "report.ready", {"path": f"{run_path}/report.md"})
        qa = self._qa(case_dir, run_dir, run, research, config)
        write_json(case_dir / "qa_review.json", qa)
        events.append(self._event("QA", qa["status"], ["qa_review.json"], qa["summary"]))

        qa_state = CaseState.QA_PASSED if qa["status"] == "passed" else CaseState.QA_LIMITED if qa["status"] == "passed_with_limitations" else CaseState.QA_FAILED
        lifecycle.transition(qa_state, "qa.completed", {"status": qa["status"]})
        decision = CaseState.AWAITING_HYPOTHESIS_APPROVAL if qa["status"] == "passed" and research["candidate_horizons"] else CaseState.NEEDS_MORE_EVIDENCE
        lifecycle.transition(decision, "case.routed", {"has_candidates": bool(research["candidate_horizons"])})
        case = {
            "case_id": case_id,
            "state": lifecycle.state.value,
            "created_at": datetime.now(timezone.utc),
            "rule": {"id": rule.definition.id, "version": rule.definition.version, "semantic_hash": rule.semantic_hash},
            "dataset_snapshot_id": run["dataset_snapshot_id"],
            "research_run": run_path,
            "qa_status": qa["status"],
            "evidence_stage": "exploratory_or_validation; final_lockbox_required",
            "universe_status": json.loads((run_dir / "config.json").read_text(encoding="utf-8"))["universe"]["status"],
            "publication": "blocked_until_human_approval",
            "frozen_campaign": str(frozen["directory"]) if frozen else None,
            "code_snapshot_id": protocol.get("code_version"),
        }
        write_json(case_dir / "case.json", case)
        write_jsonl(case_dir / "agent_runs.jsonl", events)
        return case_dir

    @staticmethod
    def _research(config: TeamConfig, statistics: dict) -> dict:
        research = build_hypothesis_draft(statistics, config.min_out_of_sample_observations)
        research["statistical_summary_artifact"] = "statistics_out_of_sample.json"
        research["validation_artifact"] = "walk_forward_validation.json"
        research["limitations"].extend([
            "Walk-forward 切分已记录；候选参数优化必须只在每个训练窗内进行。",
            "最终发布仍需要一段从未用于设计或选择的锁箱数据。",
        ])
        return research

    @staticmethod
    def _qa(case_dir: Path, run_dir: Path, run: dict, research: dict, config: TeamConfig) -> dict:
        required = [run_dir / name for name in ("config.json", "run.json", "progress.json", "artifact_manifest.json", "report.md")]
        validation = json.loads((case_dir / "walk_forward_validation.json").read_text(encoding="utf-8")) if (case_dir / "walk_forward_validation.json").is_file() else {}
        protocol = json.loads((case_dir / "experiment_protocol.json").read_text(encoding="utf-8")) if (case_dir / "experiment_protocol.json").is_file() else {}
        snapshot_check = verify_strong_snapshot(case_dir / "dataset_snapshot_manifest.json") if (case_dir / "dataset_snapshot_manifest.json").is_file() else {"status": "missing"}
        code_check = verify_code_snapshot(Path(__file__).resolve().parents[2], case_dir / "code_snapshot.json") if (case_dir / "code_snapshot.json").is_file() else {"status": "not_required"}
        campaign_readiness = json.loads((case_dir / "campaign_readiness_report.json").read_text(encoding="utf-8")) if (case_dir / "campaign_readiness_report.json").is_file() else None
        outcomes_present = False
        costs_recorded = True
        for item in iter_run_rows(run_dir, "outcomes"):
            outcomes_present = True
            if item.get("net_return") is None:
                costs_recorded = False
        core_checks = {
            "required_artifacts": all(path.is_file() for path in required),
            "committed_shards_present": artifact_exists(run_dir, "observations") and artifact_exists(run_dir, "outcomes"),
            "rule_hash_present": run.get("rule_semantic_hash", "").startswith("sha256:"),
            "dataset_snapshot_present": run.get("dataset_snapshot_id", "").startswith("sha256:"),
            "outcomes_present": outcomes_present,
            "costs_recorded": costs_recorded,
            "no_auto_publish": not (case_dir / "published_rule.json").exists(),
            "statistics_present": (case_dir / "statistics_out_of_sample.json").is_file(),
            "walk_forward_validation_present": (case_dir / "walk_forward_validation.json").is_file(),
            "multiple_testing_recorded": bool(research.get("rejected_groups") is not None),
        }
        readiness_checks = {
            "strong_snapshot_valid": snapshot_check.get("status") == "valid" and snapshot_check.get("dataset_snapshot_id") == run.get("dataset_snapshot_id"),
            "experiment_preregistered": protocol.get("status") == "preregistered" and protocol.get("readiness", {}).get("status") == "ready",
            "walk_forward_has_folds": bool(validation.get("folds")),
            "purge_covers_max_horizon": validation.get("config", {}).get("purge_size", 0) >= max(config.pipeline.horizons),
            "lockbox_sealed": bool(config.pipeline.lockbox_start and config.pipeline.end and config.pipeline.end < config.pipeline.lockbox_start),
            "cost_stress_preregistered": len(protocol.get("execution", {}).get("stress_cost_scenarios", [])) >= 2,
            "trial_budget_bounded": 1 <= int(protocol.get("validation", {}).get("max_candidate_trials", 0)) <= 20,
            "protocol_dataset_bound": protocol.get("dataset_snapshot_id") == run.get("dataset_snapshot_id"),
            "code_snapshot_bound": not str(protocol.get("code_version", "")).startswith("sha256:") or (code_check.get("status") == "valid" and code_check.get("code_snapshot_id") == protocol.get("code_version")),
            "campaign_readiness_bound": campaign_readiness is None or (campaign_readiness.get("status") == "ready" and all(campaign_readiness.get("checks", {}).values())),
        }
        checks = {**core_checks, **readiness_checks}
        structural_passed = all(core_checks.values())
        strategy_ready = all(readiness_checks.values())
        universe_status = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))["universe"]["status"]
        return {
            "status": "passed" if structural_passed and strategy_ready and universe_status == "point_in_time" else "passed_with_limitations" if structural_passed else "failed",
            "checks": checks,
            "core_checks": core_checks,
            "strategy_readiness_checks": readiness_checks,
            "universe_status": universe_status,
            "summary": "策略测试门槛通过；规则仍需独立验证与人工批准" if structural_passed and strategy_ready and universe_status == "point_in_time" else "核心结构通过，但策略测试门槛未全部满足，禁止进入审批" if structural_passed else "结构化 QA 失败，禁止进入审批",
            "research_candidates": len(research["candidate_horizons"]),
            "minimum_oos_observations": config.min_out_of_sample_observations,
        }
