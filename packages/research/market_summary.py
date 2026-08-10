"""Aggregate many immutable file-backed research cases into one review packet."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

from .statistics import summarize_outcomes
from .run_artifacts import iter_run_rows


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def aggregate_market_cases(cases_root: Path) -> dict[str, Any]:
    """Aggregate OOS outcomes only; never create a publication decision."""
    cases: list[dict[str, Any]] = []
    qa_statuses: Counter[str] = Counter()
    rules: set[tuple[str, str, str]] = set()
    data_snapshots: set[str] = set()
    for case_dir in sorted(path for path in cases_root.iterdir() if path.is_dir()):
        case_path = case_dir / "case.json"
        if not case_path.is_file():
            continue
        case = _read_json(case_path)
        qa = _read_json(case_dir / "qa_review.json") if (case_dir / "qa_review.json").is_file() else {}
        rule = case.get("rule", {})
        rules.add((str(rule.get("id")), str(rule.get("version")), str(rule.get("semantic_hash"))))
        data_snapshots.add(str(case.get("dataset_snapshot_id")))
        qa_statuses[str(qa.get("status", "unknown"))] += 1
        research_dir = case_dir / str(case["research_run"])
        outcomes_total = outcomes_oos = 0
        for item in iter_run_rows(research_dir, "outcomes"):
            outcomes_total += 1
            outcomes_oos += int(item.get("sample_split") == "out_of_sample")
        cases.append({
            "case_id": case.get("case_id", case_dir.name),
            "qa_status": qa.get("status", "unknown"),
            "state": case.get("state", "unknown"),
            "outcomes_total": outcomes_total,
            "outcomes_out_of_sample": outcomes_oos,
        })
    if not cases:
        raise ValueError(f"没有可汇总的案例: {cases_root}")
    if len(rules) != 1:
        raise ValueError("案例包含多个规则语义，禁止混合汇总")
    return {
        "schema_version": "market-research-summary/v1",
        "cases_root": str(cases_root),
        "cases": cases,
        "case_count": len(cases),
        "qa_status_counts": dict(sorted(qa_statuses.items())),
        "rule": {"id": next(iter(rules))[0], "version": next(iter(rules))[1], "semantic_hash": next(iter(rules))[2]},
        "dataset_snapshots": sorted(data_snapshots),
        "outcomes_out_of_sample": sum(item["outcomes_out_of_sample"] for item in cases),
        "statistics_out_of_sample": summarize_outcomes(
            item
            for case_dir in sorted(path for path in cases_root.iterdir() if path.is_dir() and (path / "case.json").is_file())
            for item in iter_run_rows(case_dir / str(_read_json(case_dir / "case.json")["research_run"]), "outcomes")
            if item.get("sample_split") == "out_of_sample"
        ),
        "evidence_stage": "exploratory_or_validation; final_lockbox_required",
        "publication": "blocked_until_human_approval",
        "limitations": [
            "首轮统一样本只覆盖 2026-02-03 至 2026-08-04；不应外推为长期历史证据。",
            "历史股票池在 Observation 日期逐条过滤；但 Tushare 补齐缓存的 is_st 字段尚未独立验证。",
            "所有统计均为样本外描述性汇总，并已经在所有分组上执行 FDR-BH 校正。",
        ],
    }


def render_market_summary(summary: dict[str, Any]) -> str:
    stats = summary["statistics_out_of_sample"]
    lines = [
        "# 首轮全市场点时研究总报告", "",
        "## 结论状态", "",
        f"- 规则：`{summary['rule']['id']}@{summary['rule']['version']}`",
        f"- 批次案例：{summary['case_count']}",
        f"- 样本外 Outcome：{summary['outcomes_out_of_sample']}",
        f"- 发布状态：`{summary['publication']}`", "",
        "## QA", "",
        *[f"- {name}: {count}" for name, count in summary["qa_status_counts"].items()], "",
        "## 样本外统计（FDR-BH）", "",
        "| 周期 | 市场状态 | 样本 | 均值净超额 | 95% CI | FDR p | 拒绝零假设 |",
        "|---:|---|---:|---:|---|---:|---|",
    ]
    for group in stats["groups"]:
        ci = group.get("confidence_interval")
        ci_text = "-" if ci is None else f"[{ci['lower']:.2%}, {ci['upper']:.2%}]"
        p_value = group.get("adjusted_p_value")
        p_text = "-" if p_value is None else f"{p_value:.4g}"
        lines.append(f"| {group['horizon_bars']} | {group['market_regime']} | {group['sample_size']} | {group['mean_return']:.2%} | {ci_text} | {p_text} | {group['multiple_testing_reject']} |")
    lines.extend(["", "## 限制", "", *[f"- {item}" for item in summary["limitations"]], ""])
    return "\n".join(lines)
