"""LangGraph adapter over the authoritative file state machine."""
from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .state_machine import CaseState, FileCaseStateMachine


class CaseGraphState(TypedDict, total=False):
    case_dir: str
    case_id: str
    domain_state: str
    requires_action: str | None
    resume_ack: dict[str, Any] | None


def _load(state: CaseGraphState) -> CaseGraphState:
    machine = FileCaseStateMachine.open(Path(state["case_dir"]))
    action = "hypothesis_approval" if machine.state == CaseState.AWAITING_HYPOTHESIS_APPROVAL else "rule_approval" if machine.state == CaseState.AWAITING_RULE_APPROVAL else None
    return {"case_id": machine.case_id, "domain_state": machine.state.value, "requires_action": action}


def _wait_for_human(state: CaseGraphState) -> CaseGraphState:
    acknowledgment = interrupt({
        "case_id": state["case_id"], "domain_state": state["domain_state"],
        "requires_action": state["requires_action"],
        "instruction": "通过审批 API 写入决定，然后恢复此 thread。",
    })
    refreshed = _load(state)
    refreshed["resume_ack"] = acknowledgment
    return refreshed


def _route(state: CaseGraphState) -> str:
    return "wait_for_human" if state.get("requires_action") else "done"


def build_case_graph():
    graph = StateGraph(CaseGraphState)
    graph.add_node("load_domain_state", _load)
    graph.add_node("wait_for_human", _wait_for_human)
    graph.add_node("done", lambda state: state)
    graph.add_edge(START, "load_domain_state")
    graph.add_conditional_edges("load_domain_state", _route, {"wait_for_human": "wait_for_human", "done": "done"})
    graph.add_edge("wait_for_human", "done")
    graph.add_edge("done", END)
    return graph.compile(checkpointer=InMemorySaver())
