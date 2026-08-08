"""FastAPI control plane; domain rules remain in packages/."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json
import os
from pathlib import Path
from secrets import compare_digest
from time import perf_counter
from typing import Any, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, SecretStr

from packages.contracts import Candle, RuleDefinition
from packages.governance import ApprovalError, FileAuditLog, request_hypothesis_approval, request_rule_approval, review_hypothesis, review_rule
from packages.knowledge import AnthropicWikiAnswerer, EvidenceReference, FileKnowledgeRepository, KnowledgeError, WikiAnswerError, answer_wiki_question, search_published_cards, wiki_model_status
from packages.orchestration import FileControlPlane, JobError
from packages.orchestration.file_runtime import validate_job_payload
from packages.research.case_index import get_case, list_cases
from packages.rule_dsl import RuleCompileError, compile_rule
from packages.rule_engine import evaluate


DATA_ROOT = Path(os.environ.get("TA_RESEARCH_DATA_ROOT", "data"))
CASE_ROOT = Path(os.environ.get("TA_RESEARCH_CASE_ROOT", str(DATA_ROOT / "research_cases")))
CONTROL_ROOT = Path(os.environ.get("TA_CONTROL_ROOT", str(DATA_ROOT / "control")))
REGISTRY_ROOT = Path(os.environ.get("TA_RULE_REGISTRY_ROOT", str(DATA_ROOT / "rule_registry")))
KNOWLEDGE_ROOT = Path(os.environ.get("TA_KNOWLEDGE_ROOT", str(DATA_ROOT / "knowledge")))
EVIDENCE_ROOT = Path(os.environ.get("TA_EVIDENCE_ROOT", str(DATA_ROOT / "manifests")))
AUDIT_PATH = Path(os.environ.get("TA_AUDIT_PATH", str(DATA_ROOT / "audit" / "api_requests.jsonl")))


class Principal(BaseModel):
    actor: str
    role: str


def authenticate(request: Request, x_ta_api_key: str | None = Header(default=None), x_ta_actor: str | None = Header(default=None), x_ta_role: str | None = Header(default=None)) -> Principal:
    expected = os.environ.get("TA_API_KEY")
    if expected and (not x_ta_api_key or not compare_digest(expected, x_ta_api_key)):
        raise HTTPException(401, detail={"code": "UNAUTHORIZED", "message": "API key 无效"})
    principal = Principal(actor=(x_ta_actor or "local-development").strip(), role=(x_ta_role or "admin").strip())
    if not principal.actor or not principal.role:
        raise HTTPException(401, detail={"code": "IDENTITY_REQUIRED", "message": "缺少 actor/role"})
    request.state.actor, request.state.role = principal.actor, principal.role
    return principal


def require_roles(*allowed: str):
    def dependency(principal: Principal = Depends(authenticate)) -> Principal:
        if principal.role != "admin" and principal.role not in allowed:
            raise HTTPException(403, detail={"code": "FORBIDDEN", "message": f"需要角色: {', '.join(allowed)}"})
        return principal
    return dependency


app = FastAPI(title="TA Research Team API", version="0.3.0", dependencies=[Depends(authenticate)])


@app.middleware("http")
async def audit_request(request: Request, call_next):
    started = perf_counter()
    request_id = request.headers.get("x-request-id") or "req_" + uuid4().hex
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    except Exception:
        status = 500
        raise
    finally:
        FileAuditLog(AUDIT_PATH).append("api.request", getattr(request.state, "actor", "unauthenticated"), getattr(request.state, "role", "unknown"), {"request_id": request_id, "method": request.method, "path": request.url.path, "status": status, "duration_ms": round((perf_counter() - started) * 1000, 3), "client": request.client.host if request.client else None})


def _control() -> FileControlPlane:
    return FileControlPlane(CONTROL_ROOT)


def _case_dir(case_id: str) -> Path:
    if not case_id.startswith("case_") or not case_id.removeprefix("case_").replace("_", "").isalnum():
        raise HTTPException(422, detail={"code": "INVALID_CASE_ID", "message": "案例 ID 非法"})
    path = CASE_ROOT / case_id
    if not (path / "case.json").is_file():
        raise HTTPException(404, detail={"code": "CASE_NOT_FOUND", "message": case_id})
    return path


class CandleInput(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    amount: float | None = None


class RuleInput(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    version: str
    name_zh: str
    expression: dict
    parameters: dict[str, float] = Field(default_factory=dict)
    warmup_bars: int = Field(default=0, ge=0)


class EvaluateRequest(BaseModel):
    rule: RuleInput
    candles: list[CandleInput]
    as_of_index: int = Field(ge=0)
    parameters: dict[str, float] = Field(default_factory=dict)


class JobCreateRequest(BaseModel):
    kind: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=200)
    correlation_id: str | None = Field(default=None, max_length=200)


class ReviewRequest(BaseModel):
    approver: str = Field(min_length=1, max_length=120)
    decision: str
    comment: str = Field(min_length=1, max_length=4000)


class EvidenceRefInput(BaseModel):
    kind: Literal["evidence_span", "epub_chapter"]
    manifest_path: str
    locator: str


class KnowledgeCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    claim: str = Field(min_length=1, max_length=8000)
    source_case_id: str | None = None
    evidence_refs: list[EvidenceRefInput] = Field(default_factory=list)
    research_artifacts: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class ContentReviewRequest(BaseModel):
    reviewer: str = Field(min_length=1, max_length=120)
    decision: str
    comment: str = Field(min_length=1, max_length=4000)


class WikiAnswerRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=3, ge=1, le=8)
    use_model: bool = True
    provider_api_key: SecretStr | None = None


@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ok", "storage": "file", "version": app.version}


@app.get("/api/v1/research-cases")
def research_cases() -> list[dict]:
    return list_cases(CASE_ROOT)


@app.get("/api/v1/research-cases/{case_id}")
def research_case(case_id: str) -> dict:
    try:
        return get_case(CASE_ROOT, case_id)
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "INVALID_CASE_ID", "message": str(exc)}) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, detail={"code": "CASE_NOT_FOUND", "message": str(exc)}) from exc


@app.get("/api/v1/research-cases/{case_id}/timeline")
def case_timeline(case_id: str) -> dict[str, Any]:
    directory = _case_dir(case_id)
    def read_jsonl(name: str) -> list[dict[str, Any]]:
        path = directory / name
        return [] if not path.is_file() else [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    state = json.loads((directory / "case_state.json").read_text(encoding="utf-8")) if (directory / "case_state.json").is_file() else None
    return {"case_id": case_id, "state": state, "state_events": read_jsonl("case_events.jsonl"), "agent_runs": read_jsonl("agent_runs.jsonl")}


@app.get("/api/v1/research-cases/{case_id}/report")
def case_report(case_id: str):
    directory = _case_dir(case_id)
    case = json.loads((directory / "case.json").read_text(encoding="utf-8"))
    candidates = [directory / "case_report.md", directory / str(case.get("research_run", "")) / "report.md"]
    report = next((path for path in candidates if path.is_file()), None)
    if not report:
        raise HTTPException(404, detail={"code": "REPORT_NOT_FOUND", "message": case_id})
    return FileResponse(report, media_type="text/markdown; charset=utf-8", filename=f"{case_id}.md")


@app.post("/api/v1/jobs", status_code=201)
def create_job(request: JobCreateRequest, principal: Principal = Depends(require_roles("operator"))) -> dict[str, Any]:
    try:
        validate_job_payload(request.kind, request.payload)
        return _control().create_job(request.kind, request.payload, idempotency_key=request.idempotency_key, correlation_id=request.correlation_id)
    except JobError as exc:
        raise HTTPException(422, detail={"code": "JOB_ERROR", "message": str(exc)}) from exc


@app.get("/api/v1/jobs")
def jobs(limit: int = Query(default=100, ge=1, le=1000)) -> list[dict[str, Any]]:
    return _control().list_jobs(limit)


@app.get("/api/v1/jobs/{job_id}")
def job(job_id: str) -> dict[str, Any]:
    try:
        return _control().get_job(job_id)
    except (JobError, FileNotFoundError) as exc:
        raise HTTPException(404, detail={"code": "JOB_NOT_FOUND", "message": str(exc)}) from exc


@app.post("/api/v1/jobs/{job_id}/cancel")
def cancel_job(job_id: str, principal: Principal = Depends(require_roles("operator"))) -> dict[str, Any]:
    try:
        return _control().request_cancel(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, detail={"code": "JOB_NOT_FOUND", "message": str(exc)}) from exc
    except JobError as exc:
        raise HTTPException(409, detail={"code": "JOB_STATE_CONFLICT", "message": str(exc)}) from exc


@app.get("/api/v1/jobs/{job_id}/events")
def job_events(job_id: str, after: str | None = None) -> list[dict[str, Any]]:
    try:
        return _control().job_events(job_id, after)
    except JobError as exc:
        raise HTTPException(422, detail={"code": "INVALID_JOB_ID", "message": str(exc)}) from exc


@app.get("/api/v1/jobs/{job_id}/events/stream")
def job_event_stream(job_id: str, after: str | None = None):
    try:
        events = _control().job_events(job_id, after)
    except JobError as exc:
        raise HTTPException(422, detail={"code": "INVALID_JOB_ID", "message": str(exc)}) from exc
    def stream():
        for event in events:
            yield f"id: {event['event_id']}\nevent: {event['event_type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def _approval_call(action):
    try:
        return {"artifact": str(action())}
    except ApprovalError as exc:
        raise HTTPException(409, detail={"code": "APPROVAL_CONFLICT", "message": str(exc)}) from exc


@app.post("/api/v1/research-cases/{case_id}/approvals/hypothesis/request")
def hypothesis_request(case_id: str, principal: Principal = Depends(require_roles("research_lead"))) -> dict[str, str]:
    directory = _case_dir(case_id)
    return _approval_call(lambda: request_hypothesis_approval(directory))


@app.post("/api/v1/research-cases/{case_id}/approvals/hypothesis/review")
def hypothesis_review(case_id: str, request: ReviewRequest, principal: Principal = Depends(require_roles("research_lead"))) -> dict[str, str]:
    directory = _case_dir(case_id)
    if request.approver != principal.actor:
        raise HTTPException(422, detail={"code": "APPROVER_IDENTITY_MISMATCH", "message": "审批人必须等于认证 actor"})
    return _approval_call(lambda: review_hypothesis(directory, request.approver, request.decision, request.comment))


@app.post("/api/v1/research-cases/{case_id}/approvals/rule/request")
def rule_request(case_id: str, principal: Principal = Depends(require_roles("rule_owner"))) -> dict[str, str]:
    directory = _case_dir(case_id)
    return _approval_call(lambda: request_rule_approval(directory))


@app.post("/api/v1/research-cases/{case_id}/approvals/rule/review")
def rule_review(case_id: str, request: ReviewRequest, principal: Principal = Depends(require_roles("rule_owner"))) -> dict[str, str]:
    directory = _case_dir(case_id)
    if request.approver != principal.actor:
        raise HTTPException(422, detail={"code": "APPROVER_IDENTITY_MISMATCH", "message": "审批人必须等于认证 actor"})
    return _approval_call(lambda: review_rule(directory, request.approver, request.decision, request.comment, REGISTRY_ROOT))


@app.get("/api/v1/knowledge-cards")
def knowledge_cards() -> list[dict[str, Any]]:
    return FileKnowledgeRepository(KNOWLEDGE_ROOT).list_cards()


@app.get("/api/v1/knowledge-search")
def knowledge_search(q: str = Query(min_length=1, max_length=500), top_k: int = Query(default=5, ge=1, le=20)) -> list[dict[str, Any]]:
    return search_published_cards(FileKnowledgeRepository(KNOWLEDGE_ROOT).list_cards(), q, top_k)


@app.post("/api/v1/wiki/answer")
def wiki_answer(request: WikiAnswerRequest) -> dict[str, Any]:
    try:
        answerer = None
        if request.use_model:
            answerer = (
                AnthropicWikiAnswerer.from_credentials(request.provider_api_key.get_secret_value())
                if request.provider_api_key
                else AnthropicWikiAnswerer.from_env()
            )
        return answer_wiki_question(
            FileKnowledgeRepository(KNOWLEDGE_ROOT).list_cards(),
            request.question,
            top_k=request.top_k,
            answerer=answerer,
        )
    except (WikiAnswerError, ValueError) as exc:
        raise HTTPException(503, detail={"code": "WIKI_ANSWER_UNAVAILABLE", "message": str(exc)}) from exc


@app.get("/api/v1/wiki/status")
def wiki_status() -> dict[str, Any]:
    return wiki_model_status()


@app.post("/api/v1/knowledge-cards", status_code=201)
def create_knowledge_card(request: KnowledgeCreateRequest, principal: Principal = Depends(require_roles("knowledge_editor"))) -> dict[str, str]:
    if request.source_case_id:
        _case_dir(request.source_case_id)
    references = []
    evidence_root = EVIDENCE_ROOT.resolve()
    for item in request.evidence_refs:
        manifest = Path(item.manifest_path).resolve()
        if not manifest.is_relative_to(evidence_root):
            raise HTTPException(422, detail={"code": "EVIDENCE_PATH_OUT_OF_SCOPE", "message": str(manifest)})
        references.append(EvidenceReference(item.kind, str(manifest), item.locator))
    try:
        path = FileKnowledgeRepository(KNOWLEDGE_ROOT).create_draft(title=request.title, claim=request.claim, source_case_id=request.source_case_id, evidence_refs=references, research_artifacts=request.research_artifacts, limitations=request.limitations)
        return {"artifact": str(path)}
    except KnowledgeError as exc:
        raise HTTPException(422, detail={"code": "KNOWLEDGE_ERROR", "message": str(exc)}) from exc


@app.post("/api/v1/knowledge-cards/{card_id}/review")
def review_knowledge_card(card_id: str, request: ContentReviewRequest, principal: Principal = Depends(require_roles("content_reviewer"))) -> dict[str, str]:
    if request.reviewer != principal.actor:
        raise HTTPException(422, detail={"code": "REVIEWER_IDENTITY_MISMATCH", "message": "审校人必须等于认证 actor"})
    try:
        repository = FileKnowledgeRepository(KNOWLEDGE_ROOT)
        card = repository.get(card_id)
        if request.decision == "publish" and card.get("source_case_id"):
            directory = _case_dir(card["source_case_id"])
            state = json.loads((directory / "case_state.json").read_text(encoding="utf-8"))["state"]
            if state != "rule_approved":
                raise KnowledgeError("来源 Research Case 尚未完成 Rule 审批，不能发布知识卡")
            prior_approvers = set()
            for name in ("hypothesis_approval.json", "rule_approval.json"):
                path = directory / name
                if path.is_file():
                    prior_approvers.add(json.loads(path.read_text(encoding="utf-8")).get("approver"))
            if request.reviewer in prior_approvers:
                raise KnowledgeError("Knowledge 审校人必须独立于 Hypothesis/Rule 审批人")
        path = repository.review(card_id, request.reviewer, request.decision, request.comment)
        return {"artifact": str(path)}
    except (KnowledgeError, FileNotFoundError) as exc:
        raise HTTPException(409, detail={"code": "KNOWLEDGE_REVIEW_CONFLICT", "message": str(exc)}) from exc


@app.post("/api/v1/rules/compile")
def compile_endpoint(rule: RuleInput) -> dict[str, int | str]:
    try:
        compiled = compile_rule(RuleDefinition(**rule.model_dump()))
    except RuleCompileError as exc:
        raise HTTPException(422, detail={"code": "RULE_COMPILE_ERROR", "message": str(exc)}) from exc
    return {"semantic_hash": compiled.semantic_hash, "max_lookback": compiled.max_lookback}


@app.post("/api/v1/rules/evaluate")
def evaluate_endpoint(request: EvaluateRequest) -> dict[str, Any]:
    try:
        compiled = compile_rule(RuleDefinition(**request.rule.model_dump()))
        result = evaluate([Candle(**item.model_dump()) for item in request.candles], request.as_of_index, compiled, request.parameters)
    except (RuleCompileError, ValueError) as exc:
        raise HTTPException(422, detail={"code": "RULE_EVALUATION_ERROR", "message": str(exc)}) from exc
    return {"matched": result.matched, "status": result.status, "semantic_hash": result.semantic_hash, "conditions": [asdict(condition) for condition in result.conditions], "warnings": result.warnings}
