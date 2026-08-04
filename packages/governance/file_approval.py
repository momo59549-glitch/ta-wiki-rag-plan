"""文件型人工审批。没有显式审批人输入时，系统绝不发布规则。"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from packages.research.json_store import write_json


class ApprovalError(ValueError):
    pass


def _read(path: Path) -> dict:
    if not path.is_file():
        raise ApprovalError(f"缺少审批工件: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def request_approval(case_dir: Path) -> Path:
    case = _read(case_dir / "case.json")
    qa = _read(case_dir / "qa_review.json")
    hypothesis = _read(case_dir / "hypothesis_draft.json")
    if qa.get("status") != "passed":
        raise ApprovalError("QA 未通过，不能请求规则审批")
    if not hypothesis.get("candidate_horizons"):
        raise ApprovalError("没有达到研究门槛的候选，不能请求规则审批")
    if case.get("publication") != "blocked_until_human_approval":
        raise ApprovalError("案例发布状态非法")
    request = {
        "status": "pending_human_review",
        "case_id": case["case_id"],
        "rule": case["rule"],
        "dataset_snapshot_id": case["dataset_snapshot_id"],
        "candidate_horizons": hypothesis["candidate_horizons"],
        "qa_status": qa["status"],
        "created_at": datetime.now(timezone.utc),
        "required_checklist": ["样本外结果", "成本假设", "失败样本", "适用范围", "发布理由"],
    }
    path = case_dir / "approval_request.json"
    write_json(path, request)
    return path


def approve_case(case_dir: Path, approver: str, decision: str, comment: str, registry_root: Path) -> Path:
    if decision not in {"approve", "reject", "request_changes"}:
        raise ApprovalError("decision 仅支持 approve/reject/request_changes")
    if not approver.strip() or not comment.strip():
        raise ApprovalError("审批人和审批说明不能为空")
    request = _read(case_dir / "approval_request.json")
    if request.get("status") != "pending_human_review":
        raise ApprovalError("审批请求不是待人工审核状态")
    approval = {
        "case_id": request["case_id"],
        "decision": decision,
        "approver": approver.strip(),
        "comment": comment.strip(),
        "decided_at": datetime.now(timezone.utc),
        "request_sha256": "sha256:" + sha256((case_dir / "approval_request.json").read_bytes()).hexdigest(),
    }
    approval_path = case_dir / "approval.json"
    write_json(approval_path, approval)
    if decision != "approve":
        return approval_path
    rule = request["rule"]
    version_key = f"{rule['id']}@{rule['version']}"
    publication = {
        "status": "approved",
        "rule": rule,
        "version_key": version_key,
        "source_case_id": request["case_id"],
        "dataset_snapshot_id": request["dataset_snapshot_id"],
        "approval_sha256": "sha256:" + sha256(approval_path.read_bytes()).hexdigest(),
        "published_at": datetime.now(timezone.utc),
        "scope": "research_only",
        "warning": "已人工批准的研究规则，不构成交易或投资建议。",
    }
    registry_root.mkdir(parents=True, exist_ok=True)
    target = registry_root / f"{rule['id']}-{rule['version']}.json"
    if target.exists():
        raise ApprovalError(f"规则版本已存在: {target.name}")
    write_json(target, publication)
    write_json(case_dir / "rule_publication.json", publication)
    return target
