"""文件型研究案例的只读索引，供内部工作台/API 使用。"""
from __future__ import annotations

import json
from pathlib import Path


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_cases(root: Path) -> list[dict]:
    if not root.is_dir():
        return []
    entries: list[dict] = []
    for directory in root.iterdir():
        if not directory.is_dir():
            continue
        case = _read(directory / "case.json")
        if not case:
            continue
        qa = _read(directory / "qa_review.json") or {}
        hypothesis = _read(directory / "hypothesis_draft.json") or {}
        entries.append({
            "case_id": case.get("case_id", directory.name),
            "state": case.get("state", "unknown"),
            "created_at": case.get("created_at"),
            "rule": case.get("rule", {}),
            "qa_status": qa.get("status", "unknown"),
            "research_candidates": qa.get("research_candidates", 0),
            "hypothesis_summary": hypothesis.get("summary"),
            "publication": case.get("publication", "unknown"),
            "report_path": str(directory / "case_report.md") if (directory / "case_report.md").is_file() else None,
        })
    return sorted(entries, key=lambda item: item.get("created_at") or "", reverse=True)


def get_case(root: Path, case_id: str) -> dict:
    if "/" in case_id or "\\" in case_id or case_id in {".", ".."}:
        raise ValueError("非法 case_id")
    directory = root / case_id
    case = _read(directory / "case.json")
    if not case:
        raise FileNotFoundError(case_id)
    artifacts = {}
    for name in ("qa_review.json", "hypothesis_draft.json", "statistics_out_of_sample.json", "knowledge_card_draft.json", "approval_request.json", "approval.json", "rule_publication.json"):
        value = _read(directory / name)
        if value is not None:
            artifacts[name.removesuffix(".json")] = value
    return {"case": case, "artifacts": artifacts, "report_path": str(directory / "case_report.md") if (directory / "case_report.md").is_file() else None}
