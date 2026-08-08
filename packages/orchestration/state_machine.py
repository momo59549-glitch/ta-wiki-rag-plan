"""Durable file-backed Research Case state machine.

The domain transition table is authoritative.  LangGraph may orchestrate these
transitions later, but it must never bypass them.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from packages.research.json_store import write_json, write_jsonl


class InvalidTransition(ValueError):
    pass


class CaseState(StrEnum):
    CREATED = "created"
    DATA_READY = "data_ready"
    OBSERVATIONS_READY = "observations_ready"
    OUTCOMES_READY = "outcomes_ready"
    HYPOTHESIS_DRAFTED = "hypothesis_drafted"
    BACKTEST_REVIEWED = "backtest_reviewed"
    KNOWLEDGE_DRAFTED = "knowledge_drafted"
    REPORT_READY = "report_ready"
    QA_PASSED = "qa_passed"
    QA_LIMITED = "qa_limited"
    QA_FAILED = "qa_failed"
    AWAITING_HYPOTHESIS_APPROVAL = "awaiting_hypothesis_approval"
    HYPOTHESIS_APPROVED = "hypothesis_approved"
    AWAITING_RULE_APPROVAL = "awaiting_rule_approval"
    RULE_APPROVED = "rule_approved"
    NEEDS_MORE_EVIDENCE = "needs_more_evidence"
    CHANGES_REQUESTED = "changes_requested"
    REJECTED = "rejected"
    FAILED = "failed"


_LINEAR = (
    CaseState.CREATED, CaseState.DATA_READY, CaseState.OBSERVATIONS_READY,
    CaseState.OUTCOMES_READY, CaseState.HYPOTHESIS_DRAFTED,
    CaseState.BACKTEST_REVIEWED, CaseState.KNOWLEDGE_DRAFTED,
    CaseState.REPORT_READY,
)
ALLOWED_TRANSITIONS: dict[CaseState, frozenset[CaseState]] = {
    source: frozenset({target, CaseState.FAILED})
    for source, target in zip(_LINEAR, _LINEAR[1:])
}
ALLOWED_TRANSITIONS.update({
    CaseState.REPORT_READY: frozenset({CaseState.QA_PASSED, CaseState.QA_LIMITED, CaseState.QA_FAILED, CaseState.FAILED}),
    CaseState.QA_PASSED: frozenset({CaseState.AWAITING_HYPOTHESIS_APPROVAL, CaseState.NEEDS_MORE_EVIDENCE}),
    CaseState.QA_LIMITED: frozenset({CaseState.NEEDS_MORE_EVIDENCE}),
    CaseState.QA_FAILED: frozenset({CaseState.NEEDS_MORE_EVIDENCE}),
    CaseState.AWAITING_HYPOTHESIS_APPROVAL: frozenset({CaseState.HYPOTHESIS_APPROVED, CaseState.CHANGES_REQUESTED, CaseState.REJECTED}),
    CaseState.HYPOTHESIS_APPROVED: frozenset({CaseState.AWAITING_RULE_APPROVAL}),
    CaseState.AWAITING_RULE_APPROVAL: frozenset({CaseState.RULE_APPROVED, CaseState.CHANGES_REQUESTED, CaseState.REJECTED}),
    CaseState.CHANGES_REQUESTED: frozenset({CaseState.HYPOTHESIS_DRAFTED, CaseState.AWAITING_HYPOTHESIS_APPROVAL}),
})


@dataclass(slots=True)
class FileCaseStateMachine:
    case_dir: Path
    case_id: str
    state: CaseState
    sequence: int = 0

    @classmethod
    def create(cls, case_dir: Path, case_id: str) -> "FileCaseStateMachine":
        state_path = case_dir / "case_state.json"
        if state_path.exists():
            raise FileExistsError(f"案例状态已存在: {case_id}")
        machine = cls(case_dir, case_id, CaseState.CREATED)
        machine._persist_state()
        machine._persist_events([machine._event(None, CaseState.CREATED, "case.created", {})])
        return machine

    @classmethod
    def open(cls, case_dir: Path) -> "FileCaseStateMachine":
        payload = json.loads((case_dir / "case_state.json").read_text(encoding="utf-8"))
        return cls(case_dir, str(payload["case_id"]), CaseState(payload["state"]), int(payload["sequence"]))

    def transition(self, target: CaseState, event_type: str, payload: dict[str, Any] | None = None, *, idempotency_key: str | None = None) -> bool:
        if target == self.state:
            return False
        if target not in ALLOWED_TRANSITIONS.get(self.state, frozenset()):
            raise InvalidTransition(f"非法状态转移: {self.state} -> {target}")
        events = self._load_events()
        if idempotency_key and any(item.get("idempotency_key") == idempotency_key for item in events):
            return False
        previous = self.state
        self.state = target
        self.sequence += 1
        self._persist_state()
        events.append(self._event(previous, target, event_type, payload or {}, idempotency_key))
        self._persist_events(events)
        return True

    def _event(self, previous: CaseState | None, target: CaseState, event_type: str, payload: dict[str, Any], idempotency_key: str | None = None) -> dict[str, Any]:
        at = datetime.now(timezone.utc).isoformat()
        identity = f"{self.case_id}|{self.sequence}|{previous}|{target}|{event_type}|{idempotency_key or ''}"
        return {
            "schema_version": "research-case-event/v1",
            "event_id": "evt_" + sha256(identity.encode()).hexdigest()[:24],
            "case_id": self.case_id,
            "sequence": self.sequence,
            "event_type": event_type,
            "from_state": previous.value if previous else None,
            "to_state": target.value,
            "occurred_at": at,
            "idempotency_key": idempotency_key,
            "payload": payload,
        }

    def _persist_state(self) -> None:
        write_json(self.case_dir / "case_state.json", {"schema_version": "research-case-state/v1", "case_id": self.case_id, "state": self.state.value, "sequence": self.sequence, "updated_at": datetime.now(timezone.utc)})

    def _load_events(self) -> list[dict[str, Any]]:
        path = self.case_dir / "case_events.jsonl"
        return [] if not path.is_file() else [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _persist_events(self, events: list[dict[str, Any]]) -> None:
        write_jsonl(self.case_dir / "case_events.jsonl", events)
