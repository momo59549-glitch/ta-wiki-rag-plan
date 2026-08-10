"""Read-only catalog and adjudication references for logic-level deduplication."""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

from packages.contracts import RuleDefinition
from packages.research.auto_discovery import rule_logic_reference
from packages.research.promotion import verify_frozen_campaign_rule
from packages.rule_dsl import compile_rule
from packages.rules import catalog_rules


def catalog_logic_references() -> list[dict[str, Any]]:
    """Return every registered rule as a read-only duplicate reference."""
    return [
        rule_logic_reference(
            definition,
            source_kind="catalog",
            source_id=f"{definition.id}@{definition.version}",
            disposition="registered_catalog_rule",
        )
        for definition in catalog_rules()
    ]


def _frozen_definition_for_campaign(campaign: Path, protocol: dict[str, Any]) -> RuleDefinition:
    frozen_path = campaign / "frozen_rule_definition.json"
    receipt_path = campaign / "promotion_receipt.json"
    if frozen_path.exists() or receipt_path.exists():
        if not frozen_path.is_file() or not receipt_path.is_file():
            raise ValueError("frozen rule/receipt pair incomplete")
        frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        check = verify_frozen_campaign_rule(frozen, receipt)
        if check["status"] != "valid":
            raise ValueError("invalid frozen rule: " + ", ".join(check["failures"]))
        definition = RuleDefinition(**check["definition"])
    elif "definition" in protocol.get("rule", {}):
        definition = RuleDefinition(**protocol["rule"]["definition"])
    else:
        rule_id = str(protocol.get("rule", {}).get("id", ""))
        definition = next((item for item in catalog_rules() if item.id == rule_id), None)
        if definition is None:
            raise ValueError(f"catalog rule unavailable: {rule_id}")
    compiled = compile_rule(definition)
    rule_payload = protocol.get("rule", {})
    if rule_payload.get("semantic_hash") != compiled.semantic_hash:
        raise ValueError("protocol rule semantic hash mismatch")
    return definition


def scan_historical_trial_references(project_root: Path) -> dict[str, Any]:
    """Read completed frozen adjudications without opening market/lockbox data."""
    root = project_root.resolve()
    campaign_root = root / "data" / "strategy_test_campaigns"
    execution_root = root / "data" / "strategy_test_executions"
    by_protocol: dict[str, tuple[Path, RuleDefinition]] = {}
    errors: list[dict[str, str]] = []
    if campaign_root.is_dir():
        for protocol_path in sorted(campaign_root.glob("*/experiment_protocol.json")):
            try:
                protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
                protocol_id = str(protocol.get("protocol_id", ""))
                if not protocol_id:
                    raise ValueError("protocol_id missing")
                by_protocol[protocol_id] = (protocol_path.parent, _frozen_definition_for_campaign(protocol_path.parent, protocol))
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                errors.append({"path": str(protocol_path), "reason": str(exc)})

    references: list[dict[str, Any]] = []
    if execution_root.is_dir():
        for adjudication_path in sorted(execution_root.glob("*/adjudication.json")):
            try:
                adjudication = json.loads(adjudication_path.read_text(encoding="utf-8"))
                protocol_id = str(adjudication.get("protocol_id", ""))
                campaign, definition = by_protocol[protocol_id]
                verdict = str(adjudication.get("verdict", "completed_without_verdict"))
                references.append(
                    rule_logic_reference(
                        definition,
                        source_kind="frozen_campaign_adjudication",
                        source_id=protocol_id,
                        disposition=verdict,
                        metadata={
                            "campaign": campaign.name,
                            "protocol_id": protocol_id,
                            "case_id": adjudication.get("case_id"),
                            "qa_status": adjudication.get("qa_status"),
                            "adjudication_path": str(adjudication_path.relative_to(root)).replace("\\", "/"),
                        },
                    )
                )
            except (KeyError, OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                errors.append({"path": str(adjudication_path), "reason": str(exc)})
    return {
        "schema_version": "historical-trial-logic-index/v1",
        "catalog_references": catalog_logic_references(),
        "historical_trial_references": references,
        "errors": errors,
        "market_or_lockbox_data_read": False,
    }
