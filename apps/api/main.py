"""FastAPI 适配层；业务规则始终在 packages/ 内执行。"""
from dataclasses import asdict
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from datetime import datetime
from pathlib import Path

from packages.contracts import Candle, RuleDefinition
from packages.rule_dsl import RuleCompileError, compile_rule
from packages.rule_engine import evaluate
from packages.research.case_index import get_case, list_cases

CASE_ROOT = Path("data/research_cases")

app = FastAPI(title="TA Wiki RAG API", version="0.1.0")

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

@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ok"}

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

@app.post("/api/v1/rules/compile")
def compile_endpoint(rule: RuleInput) -> dict[str, int | str]:
    try:
        compiled = compile_rule(RuleDefinition(**rule.model_dump()))
    except RuleCompileError as exc:
        raise HTTPException(422, detail={"code": "RULE_COMPILE_ERROR", "message": str(exc)}) from exc
    return {"semantic_hash": compiled.semantic_hash, "max_lookback": compiled.max_lookback}

@app.post("/api/v1/rules/evaluate")
def evaluate_endpoint(request: EvaluateRequest) -> dict:
    try:
        compiled = compile_rule(RuleDefinition(**request.rule.model_dump()))
        result = evaluate([Candle(**item.model_dump()) for item in request.candles], request.as_of_index, compiled, request.parameters)
    except (RuleCompileError, ValueError) as exc:
        raise HTTPException(422, detail={"code": "RULE_EVALUATION_ERROR", "message": str(exc)}) from exc
    return {"matched": result.matched, "status": result.status, "semantic_hash": result.semantic_hash,
            "conditions": [asdict(condition) for condition in result.conditions], "warnings": result.warnings}
