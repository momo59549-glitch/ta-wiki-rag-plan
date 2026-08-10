"""将文件型案例工件汇总为人工审阅报告。"""
from __future__ import annotations

import json
from pathlib import Path

from .run_artifacts import iter_run_rows


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def render_case_report(case_dir: Path) -> Path:
    case = _read(case_dir / "case.json")
    qa = _read(case_dir / "qa_review.json")
    hypothesis = _read(case_dir / "hypothesis_draft.json")
    stats = _read(case_dir / "statistics_out_of_sample.json")
    agent_runs = [json.loads(line) for line in (case_dir / "agent_runs.jsonl").read_text(encoding="utf-8").splitlines() if line]
    research_dir = case_dir / case["research_run"]
    excluded = sum(1 for item in iter_run_rows(research_dir, "outcomes") if not item.get("entry_executable", True) or not item.get("exit_executable", True))
    lines = [
        f"# 研究案例 {case['case_id']}", "",
        "## 决策", "",
        f"- 当前状态：`{case['state']}`",
        f"- QA：`{qa['status']}`",
        f"- 规则发布：`{case['publication']}`",
        f"- 规则：`{case['rule']['id']}@{case['rule']['version']}`",
        f"- 数据快照：`{case['dataset_snapshot_id']}`", "",
        "## Agent 时间线", "",
        "| Agent | 状态 | 说明 |", "|---|---|---|",
    ]
    lines.extend(f"| {item['agent']} | {item['status']} | {item['summary']} |" for item in agent_runs)
    lines.extend(["", "## 证据门槛", "", f"- Research：{hypothesis['summary']}", f"- 最小样本门槛：{qa['minimum_oos_observations']}", f"- 满足候选数：{qa['research_candidates']}", f"- 统计输入：{stats['outcomes_received']} 个样本，排除 {stats['outcomes_excluded']} 个无效净超额值", f"- 可成交性标记样本：{excluded}", "", "## 样本外统计摘要", "", "| 周期 | 市场状态 | 样本 | 均值净超额 | 95% CI | t 值 |", "|---:|---|---:|---:|---|---:|"])
    for group in stats["groups"]:
        interval = group["confidence_interval"]
        ci = "-" if interval is None else f"[{interval['lower']:.2%}, {interval['upper']:.2%}]"
        t_value = "-" if group["t_statistic"] is None else f"{group['t_statistic']:.2f}"
        lines.append(f"| {group['horizon_bars']} | {group['market_regime']} | {group['sample_size']} | {group['mean_return']:.2%} | {ci} | {t_value} |")
    lines.extend(["", "## 限制与审批提示", "", "- 统计为描述性正态近似，未做多重检验；不构成投资建议。", "- 即使有正向统计结果，也必须通过显式人工审批；当前没有自动发布路径。", "- 详见研究明细目录：`" + case["research_run"] + "`。", ""])
    target = case_dir / "case_report.md"
    target.write_text("\n".join(lines), encoding="utf-8")
    return target
