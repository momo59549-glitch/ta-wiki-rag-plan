"""Two-person, two-stage file approval for Hypothesis and Rule revisions."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from packages.orchestration import CaseState, FileCaseStateMachine
from packages.research.json_store import write_json

from .file_approval import ApprovalError


_DECISIONS = {"approve", "reject", "request_changes"}


def _read(path: Path) -> dict:
    if not path.is_file():
        raise ApprovalError(f"缺少审批工件: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _reviewer(value: str, comment: str, decision: str) -> tuple[str, str]:
    if decision not in _DECISIONS:
        raise ApprovalError("decision 仅支持 approve/reject/request_changes")
    if not value.strip() or not comment.strip():
        raise ApprovalError("审批人和审批说明不能为空")
    return value.strip(), comment.strip()


def _sync_case_state(case_dir: Path, state: CaseState) -> None:
    case = _read(case_dir / "case.json")
    case["state"] = state.value
    case["updated_at"] = datetime.now(timezone.utc)
    write_json(case_dir / "case.json", case)


def request_hypothesis_approval(case_dir: Path) -> Path:
    machine = FileCaseStateMachine.open(case_dir)
    if machine.state != CaseState.AWAITING_HYPOTHESIS_APPROVAL:
        raise ApprovalError(f"当前状态不能申请 Hypothesis 审批: {machine.state}")
    qa = _read(case_dir / "qa_review.json")
    draft = _read(case_dir / "hypothesis_draft.json")
    if qa.get("status") != "passed" or not draft.get("candidate_horizons"):
        raise ApprovalError("Hypothesis 未达到申请审批的结构化门槛")
    target = case_dir / "hypothesis_approval_request.json"
    if target.exists():
        return target
    write_json(target, {
        "schema_version": "hypothesis-approval-request/v1", "status": "pending_human_review",
        "case_id": machine.case_id, "candidate_horizons": draft["candidate_horizons"],
        "required_role": "research_lead", "created_at": datetime.now(timezone.utc),
    })
    return target


def review_hypothesis(case_dir: Path, approver: str, decision: str, comment: str) -> Path:
    approver, comment = _reviewer(approver, comment, decision)
    request = _read(case_dir / "hypothesis_approval_request.json")
    if request.get("status") != "pending_human_review":
        raise ApprovalError("Hypothesis 审批请求状态非法")
    target = case_dir / "hypothesis_approval.json"
    if target.exists():
        raise ApprovalError("Hypothesis 已经审批，不可覆盖")
    write_json(target, {
        "schema_version": "hypothesis-approval/v1", "case_id": request["case_id"],
        "decision": decision, "approver": approver, "role": "research_lead", "comment": comment,
        "request_sha256": "sha256:" + sha256((case_dir / "hypothesis_approval_request.json").read_bytes()).hexdigest(),
        "decided_at": datetime.now(timezone.utc),
    })
    machine = FileCaseStateMachine.open(case_dir)
    if decision == "approve":
        machine.transition(CaseState.HYPOTHESIS_APPROVED, "hypothesis.approved", {"approver": approver})
        machine.transition(CaseState.AWAITING_RULE_APPROVAL, "rule.approval.available")
    elif decision == "reject":
        machine.transition(CaseState.REJECTED, "hypothesis.rejected", {"approver": approver})
    else:
        machine.transition(CaseState.CHANGES_REQUESTED, "hypothesis.changes_requested", {"approver": approver})
    _sync_case_state(case_dir, machine.state)
    return target


def request_rule_approval(case_dir: Path) -> Path:
    machine = FileCaseStateMachine.open(case_dir)
    hypothesis = _read(case_dir / "hypothesis_approval.json")
    if machine.state != CaseState.AWAITING_RULE_APPROVAL or hypothesis.get("decision") != "approve":
        raise ApprovalError("Hypothesis 未批准，不能申请 Rule 审批")
    case = _read(case_dir / "case.json")
    target = case_dir / "rule_approval_request.json"
    if target.exists():
        return target
    write_json(target, {
        "schema_version": "rule-approval-request/v1", "status": "pending_human_review",
        "case_id": machine.case_id, "rule": case["rule"], "dataset_snapshot_id": case["dataset_snapshot_id"],
        "hypothesis_approval_sha256": "sha256:" + sha256((case_dir / "hypothesis_approval.json").read_bytes()).hexdigest(),
        "required_role": "rule_owner", "created_at": datetime.now(timezone.utc),
    })
    return target


def review_rule(case_dir: Path, approver: str, decision: str, comment: str, registry_root: Path) -> Path:
    approver, comment = _reviewer(approver, comment, decision)
    request = _read(case_dir / "rule_approval_request.json")
    hypothesis = _read(case_dir / "hypothesis_approval.json")
    if approver == hypothesis.get("approver"):
        raise ApprovalError("Hypothesis 与 Rule 必须由不同审批人负责")
    target = case_dir / "rule_approval.json"
    if target.exists():
        raise ApprovalError("Rule 已经审批，不可覆盖")
    write_json(target, {
        "schema_version": "rule-approval/v1", "case_id": request["case_id"], "decision": decision,
        "approver": approver, "role": "rule_owner", "comment": comment,
        "request_sha256": "sha256:" + sha256((case_dir / "rule_approval_request.json").read_bytes()).hexdigest(),
        "decided_at": datetime.now(timezone.utc),
    })
    machine = FileCaseStateMachine.open(case_dir)
    if decision == "approve":
        machine.transition(CaseState.RULE_APPROVED, "rule.approved", {"approver": approver})
        registry_root.mkdir(parents=True, exist_ok=True)
        rule = request["rule"]
        publication = registry_root / f"{rule['id']}-{rule['version']}.json"
        if publication.exists():
            raise ApprovalError(f"规则版本已存在: {publication.name}")
        write_json(publication, {
            "schema_version": "rule-registry-entry/v1", "status": "approved", "scope": "research_only",
            "source_case_id": request["case_id"], "rule": rule, "dataset_snapshot_id": request["dataset_snapshot_id"],
            "rule_approval_sha256": "sha256:" + sha256(target.read_bytes()).hexdigest(),
            "published_at": datetime.now(timezone.utc), "warning": "研究规则，不构成投资建议或自动交易指令。",
        })
    elif decision == "reject":
        machine.transition(CaseState.REJECTED, "rule.rejected", {"approver": approver})
    else:
        machine.transition(CaseState.CHANGES_REQUESTED, "rule.changes_requested", {"approver": approver})
    _sync_case_state(case_dir, machine.state)
    return target
