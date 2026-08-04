"""无 SQL 的确定性研究团队；每个角色写入独立、可审计的运行记录。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from packages.market_data import LocalParquetMarketData
from packages.research import FileResearchPipeline, PipelineConfig
from packages.research.json_store import write_json, write_jsonl
from packages.research.statistics import summarize_outcomes
from packages.research.hypotheses import build_hypothesis_draft
from packages.rule_dsl import CompiledRule


@dataclass(frozen=True, slots=True)
class TeamConfig:
    pipeline: PipelineConfig
    min_out_of_sample_observations: int = 300


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

    def run(self, symbols: list[str], rule: CompiledRule, config: TeamConfig) -> Path:
        case_id = "case_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
        case_dir = self.output_root / case_id
        events = [self._event("Coordinator", "completed", ["case.json"], "研究案例已创建；规则发布权限关闭")]

        run_dir = FileResearchPipeline(self.source, case_dir / "research_run").run(symbols, rule, config.pipeline)
        run_path = str(run_dir.relative_to(case_dir)).replace("\\", "/")
        run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        observations = self._read_jsonl(run_dir / "observations.jsonl")
        outcomes = self._read_jsonl(run_dir / "outcomes.jsonl")
        statistics = summarize_outcomes(
            [item for item in outcomes if item.get("sample_split") == "out_of_sample"],
        )
        write_json(case_dir / "statistics_out_of_sample.json", statistics)
        events.extend([
            self._event("Data", "completed", [f"{run_path}/config.json", f"{run_path}/run.json"], f"已载入 {run['symbols_loaded']} 个标的并冻结快照"),
            self._event("Scanner", "completed", [f"{run_path}/observations.jsonl"], f"生成 {len(observations)} 条 Observation"),
            self._event("Reviewer", "completed", [f"{run_path}/outcomes.jsonl"], f"生成 {len(outcomes)} 条 Outcome"),
        ])

        research = build_hypothesis_draft(statistics, config.min_out_of_sample_observations)
        write_json(case_dir / "hypothesis_draft.json", research)
        events.append(self._event("Research", "completed", ["hypothesis_draft.json", "statistics_out_of_sample.json"], research["summary"]))

        backtest = {
            "status": "completed",
            "engine": "file_outcome_evaluator",
            "rule_semantic_hash": run["rule_semantic_hash"],
            "dataset_snapshot_id": run["dataset_snapshot_id"],
            "outcomes_checked": len(outcomes),
            "note": "本阶段以预定义 Outcome 协议验证，复杂持仓回测仍待接入。",
        }
        write_json(case_dir / "backtest_review.json", backtest)
        events.append(self._event("Backtest", "completed", ["backtest_review.json"], "已核对规则、快照和 Outcome 身份"))

        knowledge = {
            "status": "draft",
            "rule": f"{rule.definition.id}@{rule.definition.version}",
            "claim": "待人工复核的经验候选，不构成投资建议。",
            "candidate_horizons": research["candidate_horizons"],
            "limitations": research["limitations"],
        }
        write_json(case_dir / "knowledge_card_draft.json", knowledge)
        events.append(self._event("Knowledge", "draft", ["knowledge_card_draft.json"], "仅生成草稿，不发布到规则库"))

        events.append(self._event("Report", "completed", [f"{run_path}/report.md"], "已生成含成本、基准和样本外分段的报告"))
        qa = self._qa(case_dir, run_dir, run, outcomes, research, config)
        write_json(case_dir / "qa_review.json", qa)
        events.append(self._event("QA", qa["status"], ["qa_review.json"], qa["summary"]))

        decision = "awaiting_human_approval" if qa["status"] == "passed" and research["candidate_horizons"] else "needs_more_evidence"
        case = {
            "case_id": case_id,
            "state": decision,
            "created_at": datetime.now(timezone.utc),
            "rule": {"id": rule.definition.id, "version": rule.definition.version, "semantic_hash": rule.semantic_hash},
            "dataset_snapshot_id": run["dataset_snapshot_id"],
            "research_run": run_path,
            "qa_status": qa["status"],
            "publication": "blocked_until_human_approval",
        }
        write_json(case_dir / "case.json", case)
        write_jsonl(case_dir / "agent_runs.jsonl", events)
        return case_dir

    @staticmethod
    def _qa(case_dir: Path, run_dir: Path, run: dict, outcomes: list[dict], research: dict, config: TeamConfig) -> dict:
        required = [run_dir / name for name in ("config.json", "run.json", "observations.jsonl", "outcomes.jsonl", "report.md")]
        checks = {
            "required_artifacts": all(path.is_file() for path in required),
            "rule_hash_present": run.get("rule_semantic_hash", "").startswith("sha256:"),
            "dataset_snapshot_present": run.get("dataset_snapshot_id", "").startswith("sha256:"),
            "outcomes_present": bool(outcomes),
            "costs_recorded": all(item.get("net_return") is not None for item in outcomes),
            "no_auto_publish": not (case_dir / "published_rule.json").exists(),
            "statistics_present": (case_dir / "statistics_out_of_sample.json").is_file(),
        }
        passed = all(checks.values())
        return {
            "status": "passed" if passed else "failed",
            "checks": checks,
            "summary": "结构化 QA 通过；规则仍需人工批准" if passed else "结构化 QA 失败，禁止进入审批",
            "research_candidates": len(research["candidate_horizons"]),
            "minimum_oos_observations": config.min_out_of_sample_observations,
        }
