"""Immutable, human-selected bridge from auto discovery to frozen Campaigns.

The discovery registry is only a screening artifact.  This module accepts a
specific active candidate only through a hash-bound research-selection receipt;
it never approves a rule, adds it to the catalog, or authorizes publication.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any, Mapping

from packages.contracts import RuleDefinition
from packages.research.auto_discovery import PUBLICATION_BLOCK, REGISTRY_SCHEMA, select_current_regime_candidates
from packages.rule_dsl import compile_rule, rule_definition_hash, rule_logic_hash


PROMOTION_RECEIPT_SCHEMA = "auto-discovery-promotion-receipt/v1"
FROZEN_RULE_SCHEMA = "frozen-campaign-rule/v1"


def _canonical_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return "sha256:" + sha256(encoded).hexdigest()


def _definition_from_payload(payload: Any, *, label: str) -> RuleDefinition:
    if not isinstance(payload, Mapping):
        raise ValueError(f"{label} 必须是对象")
    allowed = {"id", "version", "name_zh", "expression", "parameters", "warmup_bars", "observed_at", "executable_from"}
    required = {"id", "version", "name_zh", "expression"}
    if set(payload) - allowed or not required <= set(payload):
        raise ValueError(f"{label} 字段不完整或不受支持")
    parameters = payload.get("parameters", {})
    if not isinstance(parameters, Mapping):
        raise ValueError(f"{label}.parameters 必须是对象")
    normalized_parameters: dict[str, float] = {}
    for key, value in parameters.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError(f"{label}.parameters[{key!r}] 必须为有限数值")
        normalized_parameters[str(key)] = float(value)
    warmup = payload.get("warmup_bars", 0)
    if isinstance(warmup, bool) or not isinstance(warmup, int) or warmup < 0:
        raise ValueError(f"{label}.warmup_bars 必须是非负整数")
    observed_at = payload.get("observed_at", "bar_close")
    executable_from = payload.get("executable_from", "next_bar_open")
    if observed_at != "bar_close" or executable_from != "next_bar_open":
        raise ValueError(f"{label} 必须固定为 bar_close -> next_bar_open")
    expression = payload["expression"]
    if not isinstance(expression, Mapping):
        raise ValueError(f"{label}.expression 必须是对象")
    definition = RuleDefinition(
        id=str(payload["id"]),
        version=str(payload["version"]),
        name_zh=str(payload["name_zh"]),
        expression=dict(expression),
        parameters=normalized_parameters,
        warmup_bars=warmup,
        observed_at=observed_at,
        executable_from=executable_from,
    )
    compile_rule(definition)
    return definition


def _receipt_identity(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: receipt.get(key)
        for key in (
            "schema_version",
            "status",
            "source",
            "selected_definition",
            "selected_definition_hash",
            "selected_rule_semantic_hash",
            "selected_rule_logic_hash",
            "selection",
            "approval",
            "publication",
        )
    }


def build_auto_discovery_promotion_receipt(
    registry: Mapping[str, Any],
    *,
    rule_semantic_hash: str,
    market_regime: str,
    as_of: date,
    selector: str,
    rationale: str,
) -> dict[str, Any]:
    """Create a receipt for one explicit research-only Campaign selection."""
    if not selector.strip() or not rationale.strip():
        raise ValueError("冻结 Campaign 选择必须显式提供 selector 与 rationale")
    if registry.get("schema_version") != REGISTRY_SCHEMA:
        raise ValueError("自动发现注册表 schema 不受支持")
    selected = {
        item["rule_semantic_hash"]: item
        for item in select_current_regime_candidates(registry, market_regime, as_of)
    }.get(rule_semantic_hash)
    if selected is None:
        raise ValueError("候选未在指定状态保持 active/unexpired，不能创建冻结 Campaign 收据")
    if selected.get("approval_status") != "not_approved":
        raise ValueError("自动发现候选不得带有自动或既有审批状态")
    definition = _definition_from_payload(selected["definition"], label="registry candidate definition")
    compiled = compile_rule(definition)
    if compiled.semantic_hash != rule_semantic_hash:
        raise ValueError("注册表候选 semantic_hash 与完整定义不一致")
    if selected.get("execution_authorization") != "blocked":
        raise ValueError("注册表不应授予执行授权")
    identity = {
        "schema_version": PROMOTION_RECEIPT_SCHEMA,
        "status": "selected_for_frozen_campaign_research_only",
        "source": {
            "kind": "auto_discovery",
            "registry_id": registry.get("registry_id"),
            "registry_origin_hash": registry.get("origin_registry_hash"),
            "registry_state_hash": registry.get("registry_hash"),
            "registry_lifecycle_revision": registry.get("lifecycle_revision"),
            "auto_discovery_protocol_id": registry.get("auto_discovery_protocol_id"),
            "auto_discovery_protocol_hash": registry.get("auto_discovery_protocol_hash"),
            "source_search_id": registry.get("source_search_id"),
            "source_rule_semantic_hash": compiled.semantic_hash,
            "source_discovery_semantic_hash": selected.get("discovery_semantic_hash"),
            "source_rule_logic_hash": rule_logic_hash(definition),
        },
        "selected_definition": asdict(definition),
        "selected_definition_hash": rule_definition_hash(definition),
        "selected_rule_semantic_hash": compiled.semantic_hash,
        "selected_rule_logic_hash": rule_logic_hash(definition),
        "selection": {
            "status": "explicit_human_research_selection",
            "selector": selector.strip(),
            "rationale": rationale.strip(),
            "market_regime": market_regime,
            "as_of": as_of.isoformat(),
        },
        "approval": {
            "status": "not_approved",
            "automatic_approval": False,
            "campaign_research_authorized_by_explicit_selection": True,
        },
        "publication": PUBLICATION_BLOCK,
    }
    receipt_hash = _canonical_hash(identity)
    return {
        **identity,
        "receipt_hash": receipt_hash,
        "receipt_id": "promotion_" + receipt_hash.removeprefix("sha256:")[:24],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def verify_auto_discovery_promotion_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Verify a receipt without consulting mutable discovery artifacts."""
    failures: list[str] = []
    identity = _receipt_identity(receipt)
    expected_hash = _canonical_hash(identity)
    expected_id = "promotion_" + expected_hash.removeprefix("sha256:")[:24]
    if receipt.get("schema_version") != PROMOTION_RECEIPT_SCHEMA:
        failures.append("schema_version")
    if receipt.get("receipt_hash") != expected_hash:
        failures.append("receipt_hash")
    if receipt.get("receipt_id") != expected_id:
        failures.append("receipt_id")
    if receipt.get("status") != "selected_for_frozen_campaign_research_only":
        failures.append("status")
    selection = receipt.get("selection") if isinstance(receipt.get("selection"), Mapping) else {}
    if selection.get("status") != "explicit_human_research_selection" or not str(selection.get("selector", "")).strip() or not str(selection.get("rationale", "")).strip():
        failures.append("selection")
    approval = receipt.get("approval") if isinstance(receipt.get("approval"), Mapping) else {}
    if approval.get("status") != "not_approved" or approval.get("automatic_approval") is not False:
        failures.append("approval")
    if receipt.get("publication") != PUBLICATION_BLOCK:
        failures.append("publication")
    try:
        definition = _definition_from_payload(receipt.get("selected_definition"), label="receipt selected_definition")
        compiled = compile_rule(definition)
        if receipt.get("selected_definition_hash") != rule_definition_hash(definition):
            failures.append("selected_definition_hash")
        if receipt.get("selected_rule_semantic_hash") != compiled.semantic_hash:
            failures.append("selected_rule_semantic_hash")
        if receipt.get("selected_rule_logic_hash") != rule_logic_hash(definition):
            failures.append("selected_rule_logic_hash")
        source = receipt.get("source") if isinstance(receipt.get("source"), Mapping) else {}
        if source.get("kind") != "auto_discovery" or source.get("source_rule_semantic_hash") != compiled.semantic_hash or source.get("source_rule_logic_hash") != rule_logic_hash(definition):
            failures.append("source")
    except (TypeError, ValueError) as exc:
        definition = None
        failures.append(f"selected_definition:{exc}")
    return {
        "status": "valid" if not failures else "invalid",
        "receipt_id": receipt.get("receipt_id"),
        "receipt_hash": receipt.get("receipt_hash"),
        "expected_receipt_hash": expected_hash,
        "failures": failures,
        "definition": asdict(definition) if definition is not None else None,
    }


def write_new_promotion_receipt(path: Path, receipt: Mapping[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(f"冻结 Campaign 选择收据已存在，拒绝覆盖: {path}")
    check = verify_auto_discovery_promotion_receipt(receipt)
    if check["status"] != "valid":
        raise ValueError("拒绝写入无效选择收据: " + ", ".join(check["failures"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(receipt), ensure_ascii=False, indent=2), encoding="utf-8")


def build_frozen_campaign_rule(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Create the Campaign-local full definition record from a valid receipt."""
    check = verify_auto_discovery_promotion_receipt(receipt)
    if check["status"] != "valid":
        raise ValueError("选择收据校验失败: " + ", ".join(check["failures"]))
    definition = _definition_from_payload(receipt["selected_definition"], label="receipt selected_definition")
    compiled = compile_rule(definition)
    return {
        "schema_version": FROZEN_RULE_SCHEMA,
        "source_receipt_id": receipt["receipt_id"],
        "source_receipt_hash": receipt["receipt_hash"],
        "definition": asdict(definition),
        "definition_hash": rule_definition_hash(definition),
        "rule_semantic_hash": compiled.semantic_hash,
        "rule_logic_hash": rule_logic_hash(definition),
        "publication": PUBLICATION_BLOCK,
    }


def verify_frozen_campaign_rule(payload: Mapping[str, Any], receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Verify Campaign-local rule bytes against its immutable selection receipt."""
    failures: list[str] = []
    receipt_check = verify_auto_discovery_promotion_receipt(receipt)
    if receipt_check["status"] != "valid":
        failures.append("promotion_receipt")
    if payload.get("schema_version") != FROZEN_RULE_SCHEMA:
        failures.append("schema_version")
    if payload.get("source_receipt_id") != receipt.get("receipt_id") or payload.get("source_receipt_hash") != receipt.get("receipt_hash"):
        failures.append("receipt_binding")
    try:
        definition = _definition_from_payload(payload.get("definition"), label="frozen campaign definition")
        compiled = compile_rule(definition)
        if payload.get("definition_hash") != rule_definition_hash(definition):
            failures.append("definition_hash")
        if payload.get("rule_semantic_hash") != compiled.semantic_hash:
            failures.append("rule_semantic_hash")
        if payload.get("rule_logic_hash") != rule_logic_hash(definition):
            failures.append("rule_logic_hash")
        if payload.get("definition") != receipt.get("selected_definition"):
            failures.append("definition_receipt_mismatch")
    except (TypeError, ValueError) as exc:
        definition = None
        failures.append(f"definition:{exc}")
    return {
        "status": "valid" if not failures else "invalid",
        "failures": failures,
        "definition": asdict(definition) if definition is not None else None,
    }
