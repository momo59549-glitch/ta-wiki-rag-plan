"""Preregistered, research-only comparison of completed frozen Campaign cases."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from math import erf, isfinite, sqrt
from hashlib import sha256
from itertools import groupby
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from packages.research.json_store import write_json
from packages.research.comparison_panel import PANEL_SCHEMA, ShardedPanel
from packages.research.comparison_staging import build_compact_staging, iter_candidate_events, verify_staging
from packages.research.protocol import verify_experiment_protocol
from packages.research.promotion import verify_auto_discovery_promotion_receipt, verify_frozen_campaign_rule
from packages.research.readiness import build_code_snapshot, verify_code_snapshot
from packages.research.run_artifacts import canonical_hash, file_hash, iter_run_rows, load_commits, verify_checkpoint


PROTOCOL_SCHEMA = "candidate-comparison-protocol/v1"
RESULT_SCHEMA = "candidate-comparison-result/v1"
PUBLICATION = "research_ranking_only; approval_and_publication_forbidden"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXED_RULES = {
    "rsi": {"semantic_hash": "sha256:019d9e350829a346fd3c53100e8873c8e47e3de2ca3bdf8b6c61da907554d2b1", "logic_hash": "sha256:8d862e3a9b1e7230129da22b3ccc30bd40e9fafe633b3f3a22a8261fe3604270", "receipt_id": "promotion_ba4692a65261ef9e0ea7dd50", "receipt_hash": "sha256:ba4692a65261ef9e0ea7dd50307f740ebb25335eb84dd59733fc7c0fcd7c7f2e"},
    "roc": {"semantic_hash": "sha256:3788dcf0ac8d10c07d3ddafbdeeb21980efe86ccba456c7738cba0156a2c797c", "logic_hash": "sha256:137283851b2433537ffa8a95c998f9ac92367a7d9e32c577977e2144338401a0", "receipt_id": "promotion_efc1968214b1c291e6f26be0", "receipt_hash": "sha256:efc1968214b1c291e6f26be0a9457c5f766010926fb67c58d1e4ae7dee463f14"},
    "breakdown": {"semantic_hash": "sha256:954e32a4f9df85828679fe3db83fae1158379fb6bb8a4be7ca37bab883dbda3e", "logic_hash": "sha256:e56451dedccc8f4506a423ddb2faec0feb05aefe6ed4647d7d00b2444578567a", "receipt_id": "promotion_3220634d58c3e3fdfd97cc37", "receipt_hash": "sha256:3220634d58c3e3fdfd97cc37e00ac5df2325952f1ad3eba0cfa5e9f146fbc2e3"},
}
FIXED_FAMILIES = {"rsi": "momentum_oscillator", "roc": "momentum_oscillator", "breakdown": "price_breakdown"}


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _protocol_identity(payload: Mapping[str, Any]) -> dict[str, Any]:
    fields = ("schema_version", "status", "candidates", "shared_dataset_snapshot_id", "oos", "analysis", "execution", "multiple_testing", "elimination", "market_panel", "comparison_code_snapshot", "result", "publication")
    return {field: payload[field] for field in fields}


def verify_comparison_protocol(payload: Mapping[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    try:
        identity = _protocol_identity(payload)
    except KeyError as exc:
        return {"status": "invalid", "failures": [f"missing:{exc.args[0]}"]}
    expected_hash = canonical_hash(identity)
    expected_id = "comparison_" + expected_hash.removeprefix("sha256:")[:24]
    if payload.get("schema_version") != PROTOCOL_SCHEMA:
        failures.append("schema_version")
    if payload.get("status") != "preregistered":
        failures.append("status")
    if payload.get("comparison_hash") != expected_hash:
        failures.append("comparison_hash")
    if payload.get("comparison_id") != expected_id:
        failures.append("comparison_id")
    analysis = payload.get("analysis", {})
    if analysis.get("primary_regime") != "bearish" or analysis.get("horizons") != [5, 10, 20] or int(analysis.get("cooldown_trading_bars", 0)) != 20:
        failures.append("fixed_analysis_scope")
    lags = analysis.get("hac_lags", {})
    if any(int(lags.get(str(horizon), -1)) < horizon for horizon in (5, 10, 20)):
        failures.append("hac_lags")
    execution = payload.get("execution", {})
    if (int(execution.get("max_exit_delay_bars", -1)) != 5 or execution.get("overlapping_same_symbol_positions") != "forbidden"
            or int(execution.get("audit_sample_limit", -1)) != 100):
        failures.append("portfolio_execution_policy")
    if not payload.get("result", {}).get("staging_path"):
        failures.append("staging_path")
    code_record = payload.get("comparison_code_snapshot", {})
    if not str(code_record.get("code_snapshot_id", "")).startswith("sha256:") or not str(code_record.get("manifest_sha256", "")).startswith("sha256:") or not code_record.get("manifest_path"):
        failures.append("comparison_code_snapshot")
    if payload.get("market_panel", {}).get("schema") != PANEL_SCHEMA:
        failures.append("market_panel_schema")
    if payload.get("publication") != PUBLICATION:
        failures.append("publication")
    if len(payload.get("candidates", [])) != 3:
        failures.append("candidate_count")
    actual_rules = {item.get("candidate"): {key: item.get(key) for key in ("semantic_hash", "logic_hash", "receipt_id", "receipt_hash")} for item in payload.get("candidates", [])}
    if actual_rules != FIXED_RULES:
        failures.append("fixed_rule_identities")
    if payload.get("multiple_testing", {}).get("portfolio_family") != "all_3_candidates_x_3_horizons_base_daily_portfolio_returns":
        failures.append("portfolio_fdr_family")
    confirmation = payload.get("elimination", {}).get("portfolio_confirmation", {})
    if confirmation != {"minimum_completed_positive_fdr_ci_horizons": 2, "minimum_2x_positive_net_return_horizons": 2, "base_hac_lag": "horizon", "2x_hac_required": False}:
        failures.append("portfolio_confirmation")
    if 2.0 not in execution.get("stress_multipliers", []):
        failures.append("portfolio_2x_cost_stress")
    return {"status": "valid" if not failures else "invalid", "failures": failures, "expected_hash": expected_hash, "expected_id": expected_id}


def validate_completed_case(case_dir: Path, expected: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Strongly validate one completed sharded Case without reading market data."""
    case_dir = case_dir.resolve()
    required = ("case.json", "qa_review.json", "experiment_protocol.json", "dataset_snapshot_manifest.json")
    missing = [name for name in required if not (case_dir / name).is_file()]
    if missing:
        raise ValueError("case missing artifacts: " + ", ".join(missing))
    case, qa, protocol, dataset = (_read(case_dir / name) for name in required)
    protocol_check = verify_experiment_protocol(protocol)
    if protocol_check["status"] != "valid":
        raise ValueError("case protocol integrity invalid: " + ", ".join(protocol_check["failures"]))
    dataset_identity = {key: dataset.get(key) for key in ("schema_version", "mode", "dataset", "symbols", "files")}
    if dataset.get("dataset_snapshot_id") != canonical_hash(dataset_identity):
        raise ValueError("dataset snapshot manifest identity mismatch")
    code_path = case_dir / "code_snapshot.json"
    if not code_path.is_file():
        raise ValueError("case code snapshot artifact missing")
    code = _read(code_path)
    code_identity = {"schema_version": code.get("schema_version"), "files": code.get("files")}
    code_identity_hash = "sha256:" + sha256(json.dumps(code_identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if code.get("code_snapshot_id") != code_identity_hash or protocol.get("code_version") != code.get("code_snapshot_id"):
        raise ValueError("case code snapshot identity mismatch")
    run_dir = case_dir / str(case.get("research_run", ""))
    if not (run_dir / "artifact_manifest.json").is_file() or not (run_dir / "checkpoint.json").is_file():
        raise ValueError("case lacks integrity-checked sharded artifacts")
    manifest = _read(run_dir / "artifact_manifest.json")
    if manifest.get("execution_identity_hash") != canonical_hash(manifest.get("execution_identity", {})):
        raise ValueError("artifact manifest identity hash mismatch")
    checkpoint, commits = verify_checkpoint(run_dir, manifest["execution_identity"])
    run = _read(run_dir / "run.json")
    progress = _read(run_dir / "progress.json")
    if checkpoint.get("status") != "completed" or progress.get("status") != "completed":
        raise ValueError("case research run is not completed")
    if manifest.get("commit_hashes") != [item["commit_hash"] for item in commits]:
        raise ValueError("artifact manifest commit list mismatch")
    for field in ("observations", "outcomes"):
        if int(manifest.get(field, -1)) != int(run.get(field, -2)) or int(run[field]) != sum(int(item[field]["count"]) for item in commits):
            raise ValueError(f"artifact {field} count mismatch")
    request_path = case_dir.parent / "execution_request.json"
    request = _read(request_path) if request_path.is_file() else {}
    campaign_dir = Path(str(request.get("campaign", "")))
    receipt_path = campaign_dir / "promotion_receipt.json"; frozen_rule_path = campaign_dir / "frozen_rule_definition.json"
    campaign_protocol_path = campaign_dir / "experiment_protocol.json"; campaign_dataset_path = campaign_dir / "dataset_snapshot.json"; campaign_code_path = campaign_dir / "code_snapshot.json"
    if any(not item.is_file() for item in (receipt_path, frozen_rule_path, campaign_protocol_path, campaign_dataset_path, campaign_code_path)):
        raise ValueError("case lacks complete promotion/protocol/rule/data campaign binding")
    receipt, frozen_rule = _read(receipt_path), _read(frozen_rule_path)
    receipt_check = verify_auto_discovery_promotion_receipt(receipt)
    frozen_check = verify_frozen_campaign_rule(frozen_rule, receipt)
    if receipt_check["status"] != "valid" or frozen_check["status"] != "valid":
        raise ValueError("case promotion receipt/frozen rule integrity invalid")
    campaign_protocol, campaign_dataset, campaign_code = _read(campaign_protocol_path), _read(campaign_dataset_path), _read(campaign_code_path)
    campaign_protocol_check = verify_experiment_protocol(campaign_protocol)
    if campaign_protocol_check["status"] != "valid" or campaign_protocol.get("protocol_id") != protocol.get("protocol_id") or campaign_protocol.get("protocol_hash") != protocol.get("protocol_hash"):
        raise ValueError("case protocol differs from receipt-bound campaign protocol")
    if campaign_dataset.get("dataset_snapshot_id") != protocol.get("dataset_snapshot_id") or campaign_code.get("code_snapshot_id") != protocol.get("code_version"):
        raise ValueError("case data/code identity differs from receipt-bound campaign")
    if (canonical_hash(campaign_protocol) != canonical_hash(protocol) or canonical_hash(campaign_dataset) != canonical_hash(dataset)
            or canonical_hash(campaign_code) != canonical_hash(code)):
        raise ValueError("case protocol/data/code artifacts are not content-bound to campaign")
    if any(request.get(key) != expected_value for key, expected_value in (("case_id", case.get("case_id")), ("protocol_id", protocol.get("protocol_id")), ("dataset_snapshot_id", protocol.get("dataset_snapshot_id")))):
        raise ValueError("execution request identity differs from completed case")
    checks = {
        "case_id": case.get("case_id"), "case_path": str(case_dir),
        "qa_status": qa.get("status"), "protocol_id": protocol.get("protocol_id"),
        "protocol_hash": protocol.get("protocol_hash"), "dataset_snapshot_id": protocol.get("dataset_snapshot_id"),
        "code_snapshot_id": protocol.get("code_version"), "rule": protocol.get("rule"),
        "research_run": str(run_dir), "artifact_identity_hash": manifest.get("execution_identity_hash"),
        "artifact_commit_hashes": manifest.get("commit_hashes"),
        "oos_start": protocol.get("periods", {}).get("validation_start"),
        "oos_end": protocol.get("periods", {}).get("research_end"),
        "lockbox_start": protocol.get("periods", {}).get("final_lockbox_start"),
        "final_lockbox_consumed": request.get("final_lockbox_consumed", False),
        "base_cost_bps_per_side": protocol.get("execution", {}).get("base_cost_bps_per_side"),
        "semantic_hash": receipt.get("selected_rule_semantic_hash"), "logic_hash": receipt.get("selected_rule_logic_hash"),
        "receipt_id": receipt.get("receipt_id"), "receipt_hash": receipt.get("receipt_hash"),
    }
    artifact_identity = manifest.get("execution_identity", {})
    if artifact_identity.get("case_id") != checks["case_id"] or artifact_identity.get("dataset_snapshot_id") != checks["dataset_snapshot_id"]:
        raise ValueError("artifact execution identity is not bound to Case/dataset")
    if artifact_identity.get("experiment_protocol_id") not in {None, checks["protocol_id"]}:
        raise ValueError("artifact execution identity is not bound to protocol")
    if qa.get("status") not in {"passed", "passed_with_limitations"} or case.get("qa_status") != qa.get("status"):
        raise ValueError("case QA is not completed or identity-bound")
    if checks["final_lockbox_consumed"]:
        raise ValueError("final lockbox was already consumed")
    if not checks["oos_end"] or not checks["lockbox_start"] or checks["oos_end"] >= checks["lockbox_start"]:
        raise ValueError("case OOS period is not sealed before final lockbox")
    for outcome in iter_run_rows(run_dir, "outcomes"):
        for field in ("entry_at", "exit_at"):
            value = outcome.get(field)
            if value is not None and str(value)[:10] >= str(checks["lockbox_start"]):
                raise ValueError(f"case outcome crosses final lockbox: {field}")
    if case.get("dataset_snapshot_id") != checks["dataset_snapshot_id"] or run.get("dataset_snapshot_id") != checks["dataset_snapshot_id"]:
        raise ValueError("case dataset identity mismatch")
    case_rule = case.get("rule", {})
    protocol_rule = protocol.get("rule", {})
    if any(case_rule.get(key) != protocol_rule.get(key) for key in ("id", "version", "semantic_hash")):
        raise ValueError("case rule identity mismatch")
    if (checks["semantic_hash"] != case_rule.get("semantic_hash") or frozen_rule.get("rule_semantic_hash") != checks["semantic_hash"]
            or frozen_rule.get("rule_logic_hash") != checks["logic_hash"]):
        raise ValueError("case rule differs from promotion receipt")
    if not set((5, 10, 20)).issubset({int(item) for item in protocol.get("outcomes", {}).get("horizons", [])}):
        raise ValueError("case does not contain preregistered comparison horizons")
    if expected:
        for key in ("case_id", "protocol_id", "protocol_hash", "dataset_snapshot_id", "code_snapshot_id", "artifact_identity_hash", "artifact_commit_hashes", "semantic_hash", "logic_hash", "receipt_id", "receipt_hash"):
            if expected.get(key) != checks.get(key):
                raise ValueError(f"case frozen identity mismatch: {key}")
    return checks


def build_comparison_protocol(case_dirs: Mapping[str, Path], market_panel: Path, output: Path, *, result_path: Path | None = None, seed: int = 20260809, project_root: Path = REPOSITORY_ROOT) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(f"comparison protocol already exists: {output}")
    if sorted(case_dirs) != ["breakdown", "roc", "rsi"]:
        raise ValueError("candidate set is fixed to breakdown, roc, rsi")
    candidates = [{"candidate": name, "rule_family": FIXED_FAMILIES[name], **validate_completed_case(path)} for name, path in sorted(case_dirs.items())]
    actual_rules = {item["candidate"]: {key: item[key] for key in ("semantic_hash", "logic_hash", "receipt_id", "receipt_hash")} for item in candidates}
    if actual_rules != FIXED_RULES:
        raise ValueError("candidate cases do not match the three fixed promoted rule identities")
    if len({item["semantic_hash"] for item in candidates}) != 3:
        raise ValueError("candidate cases contain duplicate promoted rules")
    snapshots = {item["dataset_snapshot_id"] for item in candidates}
    periods = {(item["oos_start"], item["oos_end"], item["lockbox_start"]) for item in candidates}
    costs = {json.dumps(item["base_cost_bps_per_side"], sort_keys=True) for item in candidates}
    if len(snapshots) != 1:
        raise ValueError("candidate cases use different dataset snapshots")
    if len(periods) != 1:
        raise ValueError("candidate cases use different OOS ranges")
    if len(costs) != 1:
        raise ValueError("candidate cases use different cost conventions")
    if not market_panel.is_file():
        raise FileNotFoundError("frozen market panel is required before preregistration")
    oos_start, oos_end, lockbox_start = next(iter(periods))
    panel = ShardedPanel(market_panel, expected_snapshot_id=next(iter(snapshots)), expected_oos={"start": oos_start, "end": oos_end, "lockbox_start": lockbox_start})
    builder_snapshot_path = panel.root / panel.manifest["builder_code_snapshot"]["path"]
    builder_code_check = verify_code_snapshot(project_root, builder_snapshot_path)
    if builder_code_check["status"] != "valid":
        raise ValueError("panel builder code snapshot no longer matches project; rebuild panel")
    panel.validate_all()
    comparison_snapshot_path = output.resolve().with_name("comparison_code_snapshot.json")
    if comparison_snapshot_path.exists():
        raise FileExistsError(f"comparison code snapshot already exists: {comparison_snapshot_path}")
    comparison_snapshot = build_code_snapshot(project_root, comparison_snapshot_path)
    if comparison_snapshot["code_snapshot_id"] != panel.manifest["builder_code_snapshot_id"]:
        raise ValueError("panel builder and comparison code snapshots differ; rebuild panel")
    identity = {
        "schema_version": PROTOCOL_SCHEMA, "status": "preregistered", "candidates": candidates,
        "shared_dataset_snapshot_id": next(iter(snapshots)),
        "oos": {"start": oos_start, "end": oos_end, "lockbox_start": lockbox_start, "final_lockbox_must_remain_unread": True},
        "analysis": {"primary_regime": "bearish", "secondary_regimes": ["bullish", "unknown"], "horizons": [5, 10, 20],
                     "cooldown_trading_bars": 20, "overlap_exact_key": "symbol+observed_date", "proximity_trading_bars": 5,
                     "cross_sectional_unit": "signal_date_mean", "hac_lags": {"5": 5, "10": 10, "20": 20}, "confidence_level": 0.95, "seed": int(seed),
                     "evaluation_cutoff": "per_symbol_last_signal_index = n_bars - horizon - max_exit_delay_bars - 1", "tail_policy": "purged_before_evaluation_without_inspecting_future_tradeability"},
        "execution": {"entry": "next_session_open", "exit": "fixed_horizon_close", "max_positions": 20,
                      "same_day_selection": "sha256(seed|candidate|symbol|signal_date)", "weighting": "equal_weight_slots",
                      "base_cost_bps_per_side": json.loads(next(iter(costs))), "stress_multipliers": [2.0, 3.0],
                      "requires_open_tradeable": True, "requires_exit_close_tradeable": True,
                      "same_bar_close_information_for_entry": False, "max_exit_delay_bars": 5, "overlapping_same_symbol_positions": "forbidden", "audit_sample_limit": 100,
                      "untradeable_exit_policy": "delay_to_first_tradeable_close_up_to_max_exit_delay_else_ledger_blocked",
                      "engine": "audited_daily_cash_equity_ledger; vectorbt_single_asset_adapter_insufficient"},
        "multiple_testing": {"family": "all_3_candidates_x_3_horizons_bearish_primary", "portfolio_family": "all_3_candidates_x_3_horizons_base_daily_portfolio_returns", "method": "fdr_bh", "alpha": 0.05},
        "elimination": {"minimum_positive_hac_ci_horizons": 2, "stress_multiplier_required": 2.0,
                        "minimum_dates_per_horizon": 20, "minimum_positive_year_fraction": 0.5,
                        "high_overlap_threshold": 0.6, "other_regime_material_negative_ci": "upper<0",
                        "portfolio_confirmation": {"minimum_completed_positive_fdr_ci_horizons": 2, "minimum_2x_positive_net_return_horizons": 2, "base_hac_lag": "horizon", "2x_hac_required": False},
                        "tie_break": ["portfolio_positive_horizons", "portfolio_minimum_hac_ci_lower", "portfolio_2x_positive_horizons", "event_positive_horizons", "event_minimum_hac_ci_lower", "unique_event_fraction", "candidate_name"]},
        "market_panel": {"manifest_path": str(market_panel.resolve()), "manifest_sha256": file_hash(market_panel), "panel_id": panel.manifest["panel_id"],
                         "schema": PANEL_SCHEMA, "source_root": panel.manifest["source_root"], "source_dataset": panel.manifest["source_dataset"]},
        "comparison_code_snapshot": {"code_snapshot_id": comparison_snapshot["code_snapshot_id"], "manifest_path": str(comparison_snapshot_path),
                                     "manifest_sha256": file_hash(comparison_snapshot_path)},
        "result": {"path": str((result_path or output.with_name("comparison_result.json")).resolve()),
                   "staging_path": str((result_path or output.with_name("comparison_result.json")).resolve().with_suffix(".staging")), "write_once": True},
        "publication": PUBLICATION,
    }
    digest = canonical_hash(identity)
    payload = {**identity, "comparison_id": "comparison_" + digest.removeprefix("sha256:")[:24], "comparison_hash": digest, "created_at": datetime.now(timezone.utc)}
    write_json(output, payload)
    return payload


def apply_trading_bar_cooldown(events: Iterable[Mapping[str, Any]], calendar: Mapping[str, Mapping[str, int]], bars: int = 20) -> list[dict[str, Any]]:
    ordered = sorted((dict(item) for item in events), key=lambda item: (str(item["symbol"]), str(item["date"]), str(item.get("event_id", ""))))
    selected: list[dict[str, Any]] = []
    last: dict[str, int] = {}
    seen: set[tuple[str, str]] = set()
    for item in ordered:
        symbol, day = str(item["symbol"]), str(item["date"])
        key = (symbol, day)
        if key in seen:
            continue
        seen.add(key)
        try:
            ordinal = int(calendar[symbol][day])
        except KeyError as exc:
            raise ValueError(f"event is absent from frozen trading calendar: {symbol} {day}") from exc
        if symbol not in last or ordinal - last[symbol] >= bars:
            selected.append(item); last[symbol] = ordinal
    return selected


def pairwise_overlap(left: Iterable[Mapping[str, Any]], right: Iterable[Mapping[str, Any]], calendar: Mapping[str, Mapping[str, int]], proximity_bars: int = 5) -> dict[str, Any]:
    a = {(str(item["symbol"]), str(item["date"])) for item in left}; b = {(str(item["symbol"]), str(item["date"])) for item in right}
    intersection = a & b; union = a | b
    by_symbol_b: dict[str, list[int]] = defaultdict(list)
    by_symbol_a: dict[str, list[int]] = defaultdict(list)
    for symbol, day in b: by_symbol_b[symbol].append(int(calendar[symbol][day]))
    for symbol, day in a: by_symbol_a[symbol].append(int(calendar[symbol][day]))
    near_a = sum(any(abs(int(calendar[s][d]) - other) <= proximity_bars for other in by_symbol_b[s]) for s, d in a)
    near_b = sum(any(abs(int(calendar[s][d]) - other) <= proximity_bars for other in by_symbol_a[s]) for s, d in b)
    return {"left_events": len(a), "right_events": len(b), "exact_intersection": len(intersection),
            "exact_jaccard": len(intersection) / len(union) if union else 1.0,
            "left_contained_by_right": len(intersection) / len(a) if a else 0.0, "right_contained_by_left": len(intersection) / len(b) if b else 0.0,
            "left_unique_fraction": len(a - b) / len(a) if a else 0.0, "right_unique_fraction": len(b - a) / len(b) if b else 0.0,
            "proximity_left_covered": near_a / len(a) if a else 0.0, "proximity_right_covered": near_b / len(b) if b else 0.0}


def _calendar_for_events(panel: ShardedPanel, event_groups: Iterable[Iterable[Mapping[str, Any]]]) -> dict[str, dict[str, int]]:
    needed: dict[str, set[str]] = defaultdict(set)
    for events in event_groups:
        for item in events: needed[str(item["symbol"])].add(str(item["date"]))
    calendar: dict[str, dict[str, int]] = {}
    for symbol, days in sorted(needed.items()):
        _, full_index = panel.load_symbol(symbol)
        missing = days - set(full_index)
        if missing: raise ValueError(f"events absent from frozen panel calendar: {symbol}")
        calendar[symbol] = {day: full_index[day] for day in days}
    return calendar


def _cooldown_from_panel(events: Iterable[Mapping[str, Any]], panel: ShardedPanel, bars: int) -> list[dict[str, Any]]:
    values = list(events)
    return apply_trading_bar_cooldown(values, _calendar_for_events(panel, [values]), bars)


def hac_mean(values: Iterable[float], lag: int, confidence_level: float = 0.95) -> dict[str, Any]:
    series = [float(value) for value in values if isfinite(float(value))]
    n = len(series)
    if not series:
        return {"effect": None, "standard_error": None, "confidence_interval": None, "n_dates": 0, "lag": lag,
                "raw_p_value": None, "evidence_status": "no_valid_dates"}
    mean = sum(series) / n; centered = [value - mean for value in series]
    long_run = sum(value * value for value in centered) / n
    for offset in range(1, min(lag, n - 1) + 1):
        covariance = sum(centered[index] * centered[index - offset] for index in range(offset, n)) / n
        long_run += 2 * (1 - offset / (lag + 1)) * covariance
    se = sqrt(max(0.0, long_run) / n)
    z = 1.959963984540054 if confidence_level == 0.95 else 1.959963984540054
    return {"effect": mean, "standard_error": se, "confidence_interval": {"lower": mean - z * se, "upper": mean + z * se}, "n_dates": n, "lag": lag,
            "evidence_status": "insufficient_dates" if n <= lag else "descriptive_hac",
            "raw_p_value": 2 * (1 - 0.5 * (1 + erf(abs(mean / se) / sqrt(2)))) if se else (0.0 if mean else 1.0)}


def apply_fdr_bh(records: list[dict[str, Any]], alpha: float = 0.05) -> None:
    ordered = sorted(enumerate(records), key=lambda pair: float(pair[1].get("raw_p_value") if pair[1].get("raw_p_value") is not None else 1.0))
    m = len(ordered); adjusted = [1.0] * m; running = 1.0
    for rank_index in range(m - 1, -1, -1):
        original_index, record = ordered[rank_index]; rank = rank_index + 1
        raw = float(record.get("raw_p_value") if record.get("raw_p_value") is not None else 1.0)
        running = min(running, raw * m / rank); adjusted[original_index] = running
    for record, value in zip(records, adjusted):
        record["adjusted_p_value"] = value; record["fdr_reject"] = record.get("raw_p_value") is not None and value <= alpha


def _group_rows(rows: Iterable[Mapping[str, Any]], field: str) -> dict[str, list[Mapping[str, Any]]]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows: groups[str(row[field])].append(row)
    return groups


def _symbol_groups(events: Iterable[Mapping[str, Any]]):
    for symbol, rows in groupby(events, key=lambda item: str(item["symbol"])):
        yield symbol, list(rows)


def _staged_overlap(staging_dir: Path, staging: Mapping[str, Any], panel: ShardedPanel, left: str, right: str, proximity: int) -> dict[str, Any]:
    left_groups = iter(_symbol_groups(iter_candidate_events(staging_dir, staging, left))); right_groups = iter(_symbol_groups(iter_candidate_events(staging_dir, staging, right)))
    current_left = next(left_groups, None); current_right = next(right_groups, None)
    left_n = right_n = intersection = near_left = near_right = 0
    while current_left is not None or current_right is not None:
        if current_right is None or (current_left is not None and current_left[0] < current_right[0]):
            left_n += len(current_left[1]); current_left = next(left_groups, None); continue
        if current_left is None or current_right[0] < current_left[0]:
            right_n += len(current_right[1]); current_right = next(right_groups, None); continue
        symbol = current_left[0]; left_days = [row["date"] for row in current_left[1]]; right_days = [row["date"] for row in current_right[1]]
        left_n += len(left_days); right_n += len(right_days); intersection += len(set(left_days) & set(right_days))
        _, index = panel.load_symbol(symbol); right_ord = [index[day] for day in right_days]; left_ord = [index[day] for day in left_days]
        near_left += sum(any(abs(index[day] - value) <= proximity for value in right_ord) for day in left_days)
        near_right += sum(any(abs(index[day] - value) <= proximity for value in left_ord) for day in right_days)
        current_left = next(left_groups, None); current_right = next(right_groups, None)
    union = left_n + right_n - intersection
    return {"left_events": left_n, "right_events": right_n, "exact_intersection": intersection,
            "exact_jaccard": intersection / union if union else 1.0, "left_contained_by_right": intersection / left_n if left_n else 0.0,
            "right_contained_by_left": intersection / right_n if right_n else 0.0, "left_unique_fraction": (left_n - intersection) / left_n if left_n else 0.0,
            "right_unique_fraction": (right_n - intersection) / right_n if right_n else 0.0,
            "proximity_left_covered": near_left / left_n if left_n else 0.0, "proximity_right_covered": near_right / right_n if right_n else 0.0}


def _staged_statistics(staging_dir: Path, staging: Mapping[str, Any], candidates: list[str], horizons: list[int], lags: Mapping[str, int]):
    accumulators: dict[tuple[str, int, str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for candidate in candidates:
        for event in iter_candidate_events(staging_dir, staging, candidate):
            for horizon_text, outcome in event["horizons"].items():
                key = (candidate, int(horizon_text), str(outcome["regime"]), str(event["date"])); bucket = accumulators[key]
                bucket[0] += float(outcome["net_excess_return"]); bucket[1] += 1
    grouped: dict[tuple[str, int, str], list[tuple[str, float]]] = defaultdict(list)
    for (candidate, horizon, regime, day), (total, count) in accumulators.items(): grouped[(candidate, horizon, regime)].append((day, total / count))
    primary = []; secondary = []
    for candidate in candidates:
        for horizon in horizons:
            for regime in ("bearish", "bullish", "unknown"):
                values = sorted(grouped.get((candidate, horizon, regime), [])); summary = {"candidate": candidate, "horizon": horizon, "regime": regime, **hac_mean([value for _, value in values], int(lags[str(horizon)]))}
                summary["_daily_values"] = [value for _, value in values]
                years: dict[str, list[float]] = defaultdict(list)
                for day, value in values: years[day[:4]].append(value)
                summary["year_effects"] = {year: sum(items) / len(items) for year, items in sorted(years.items())}
                summary["positive_year_fraction"] = sum(value > 0 for value in summary["year_effects"].values()) / len(summary["year_effects"]) if summary["year_effects"] else 0.0
                (primary if regime == "bearish" else secondary).append(summary)
    return primary, secondary


def _bounded_sample(samples: list[dict[str, Any]], item: Mapping[str, Any], limit: int) -> None:
    value = dict(item); samples.append(value); samples.sort(key=lambda row: canonical_hash(row))
    if len(samples) > limit: samples.pop()


def _staged_portfolio(staging_dir: Path, staging: Mapping[str, Any], panel: ShardedPanel, protocol: Mapping[str, Any], candidate: str, horizon: int, global_dates: list[str], *, cost_multiplier: float = 1.0) -> dict[str, Any]:
    shards = {item["entry_date"]: item for item in staging["plan_shards"] if item["candidate"] == candidate and int(item["horizon"]) == horizon}
    sample_limit = int(protocol["execution"]["audit_sample_limit"]); max_positions = int(protocol["execution"]["max_positions"])
    cost = cost_multiplier * sum(float(value) for value in protocol["execution"]["base_cost_bps_per_side"].values()) / 10_000
    cash = 1.0; active = {}; curve = []; trades_count = 0; rejected_counts = defaultdict(int); trade_samples = []; rejected_samples = []; unresolved_samples = []
    trade_digest = sha256(); rejected_digest = sha256(); turnover = 0.0; peak_plans = peak_active = 0; unresolved = 0
    for day in global_dates:
        equity_open = cash + sum(position["shares"] * (float(position["rows"][position["index"].get(day)]["open"]) if day in position["index"] else position["last_close"]) for position in active.values())
        available = max_positions - len(active); best = []
        shard = shards.get(day)
        if shard:
            with (staging_dir / shard["path"]).open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip(): continue
                    plan = json.loads(line); reason = None
                    if plan["symbol"] in active: reason = "same_symbol_overlap_forbidden"
                    elif not plan["entry_tradeable"]: reason = "entry_untradeable"
                    if reason:
                        rejected_counts[reason] += 1; record = {"symbol": plan["symbol"], "signal_date": plan["signal_date"], "reason": reason}; rejected_digest.update((json.dumps(record, sort_keys=True) + "\n").encode()); _bounded_sample(rejected_samples, record, sample_limit); continue
                    best.append(plan); best.sort(key=lambda item: (item["selection_rank"], item["symbol"]))
                    if len(best) > max(0, available):
                        removed = best.pop(); rejected_counts["max_positions"] += 1; record = {"symbol": removed["symbol"], "signal_date": removed["signal_date"], "reason": "max_positions"}; rejected_digest.update((json.dumps(record, sort_keys=True) + "\n").encode()); _bounded_sample(rejected_samples, record, sample_limit)
            peak_plans = max(peak_plans, len(best))
        for plan in best:
            rows, index = panel.load_symbol(plan["symbol"]); row = rows[index[day]]
            if float(row["open"]) != float(plan["entry_open"]) or bool(row["tradeable_open"]) != bool(plan["entry_tradeable"]): raise ValueError("staged entry no longer matches frozen panel")
            effective = float(row["open"]) * (1 + cost); target = equity_open / max_positions; notional = min(target, cash)
            if notional <= 0: continue
            cash -= notional; turnover += notional
            active[plan["symbol"]] = {**plan, "rows": rows, "index": index, "shares": notional / effective, "entry_notional": notional, "last_close": float(row["open"])}
        peak_active = max(peak_active, len(active))
        for symbol, position in list(active.items()):
            row = position["rows"][position["index"][day]] if day in position["index"] else None
            if row is None: continue
            position["last_close"] = float(row["close"])
            if day >= position["scheduled_exit_date"]:
                if bool(row["tradeable_close"]):
                    proceeds = position["shares"] * float(row["close"]) * (1 - cost); cash += proceeds; turnover += proceeds; trades_count += 1
                    record = {"symbol": symbol, "signal_date": position["signal_date"], "entry_date": position["entry_date"], "exit_date": day,
                              "scheduled_exit_date": position["scheduled_exit_date"], "entry_notional": position["entry_notional"], "exit_proceeds": proceeds}
                    trade_digest.update((json.dumps(record, sort_keys=True) + "\n").encode()); _bounded_sample(trade_samples, record, sample_limit); del active[symbol]
                elif day == position["deadline_date"]:
                    unresolved += 1; record = {"symbol": symbol, "signal_date": position["signal_date"], "deadline_date": day, "reason": "exit_untradeable_beyond_max_delay"}; _bounded_sample(unresolved_samples, record, sample_limit); del active[symbol]
        equity = cash + sum(item["shares"] * item["last_close"] for item in active.values()); curve.append({"date": day, "equity": equity, "cash": cash, "positions": len(active)})
    blocked = unresolved > 0 or bool(active); ending = curve[-1]["equity"] if curve else 1.0; peak = 0.0; drawdowns = []
    for item in curve: peak = max(peak, item["equity"]); drawdowns.append(item["equity"] / peak - 1 if peak else 0.0)
    previous_equity = 1.0; daily_returns = []
    for item in curve:
        daily_returns.append(item["equity"] / previous_equity - 1 if previous_equity else 0.0); previous_equity = item["equity"]
    portfolio_hac = hac_mean(daily_returns, horizon) if not blocked else {**hac_mean([], horizon), "evidence_status": "blocked_ledger"}
    elapsed = (date.fromisoformat(curve[-1]["date"]) - date.fromisoformat(curve[0]["date"])).days if len(curve) > 1 else 0
    candidate_record = next(item for item in staging["candidates"] if item["candidate"] == candidate)
    return {"status": "blocked_unresolved_exit" if blocked else "completed", "accounting": "daily_cash_positions_equity", "horizon": horizon, "cost_multiplier": cost_multiplier,
            "trades_count": trades_count, "trade_hash": "sha256:" + trade_digest.hexdigest(), "trade_audit_sample": trade_samples,
            "rejected_counts": dict(rejected_counts), "rejected_hash": "sha256:" + rejected_digest.hexdigest(), "rejected_audit_sample": rejected_samples,
            "tail_purged_count": candidate_record["tail_purged_counts"][str(horizon)], "tail_purged_audit_sample": candidate_record["tail_purged_samples"][str(horizon)],
            "unresolved_count": unresolved + len(active), "unresolved_audit_sample": unresolved_samples, "equity_curve": curve,
            "daily_returns": daily_returns, "n_days": len(daily_returns), "hac": portfolio_hac,
            "portfolio_net_return": None if blocked else ending - 1, "annualized_return": None if blocked or elapsed <= 0 or ending <= 0 else ending ** (365.25 / elapsed) - 1,
            "max_drawdown": None if blocked else min(drawdowns, default=0.0), "gross_turnover": None if blocked else turnover,
            "diagnostics": {"peak_plans": peak_plans, "peak_active_positions": peak_active}, "event_mean_used_as_portfolio_pnl": False}


def _apply_portfolio_fdr(portfolio: Mapping[str, Mapping[str, dict[str, Any]]], candidates: list[str], horizons: list[int], alpha: float) -> list[dict[str, Any]]:
    family = [{"candidate": candidate, "horizon": horizon, **portfolio[candidate][str(horizon)]["hac"]} for candidate in candidates for horizon in horizons]
    apply_fdr_bh(family, alpha)
    for item in family:
        portfolio[item["candidate"]][str(item["horizon"])]["hac"] = item
    return family


def _finalize_ranking(primary: list[dict[str, Any]], secondary: list[dict[str, Any]], overlaps: list[dict[str, Any]],
                      portfolio: Mapping[str, Mapping[str, dict[str, Any]]], protocol: Mapping[str, Any], candidates: list[str], horizons: list[int]) -> list[dict[str, Any]]:
    unique_fraction: dict[str, list[float]] = defaultdict(list)
    for item in overlaps:
        unique_fraction[item["left"]].append(float(item["left_unique_fraction"])); unique_fraction[item["right"]].append(float(item["right_unique_fraction"]))
    confirmation = protocol["elimination"]["portfolio_confirmation"]; ranking = []
    for candidate in candidates:
        records = [item for item in primary if item["candidate"] == candidate]
        positive = [item for item in records if item.get("evidence_status") == "descriptive_hac" and item["n_dates"] >= protocol["elimination"]["minimum_dates_per_horizon"] and item["confidence_interval"] and item["confidence_interval"]["lower"] > 0 and item["fdr_reject"]]
        negative_other = [item for item in secondary if item["candidate"] == candidate and item["confidence_interval"] and item["confidence_interval"]["upper"] < 0]
        stress_pass = [item for item in positive if item["stress"][str(protocol["elimination"]["stress_multiplier_required"])]["confidence_interval"] and item["stress"][str(protocol["elimination"]["stress_multiplier_required"])]["confidence_interval"]["lower"] > 0]
        years_pass = [item for item in positive if item["positive_year_fraction"] >= protocol["elimination"]["minimum_positive_year_fraction"]]
        event_passes = len(positive) >= protocol["elimination"]["minimum_positive_hac_ci_horizons"] and len(stress_pass) >= 2 and len(years_pass) >= 2
        base_positive = []; stress_positive = []
        for horizon in horizons:
            ledger = portfolio[candidate][str(horizon)]; summary = ledger["hac"]
            if (ledger["status"] == "completed" and ledger["portfolio_net_return"] is not None and ledger["portfolio_net_return"] > 0
                    and summary.get("evidence_status") == "descriptive_hac" and summary.get("confidence_interval")
                    and summary["confidence_interval"]["lower"] > 0 and summary.get("fdr_reject")):
                base_positive.append(summary)
            stressed = ledger["cost_stress"]["2.0"]
            if stressed["status"] == "completed" and stressed["portfolio_net_return"] is not None and stressed["portfolio_net_return"] > 0:
                stress_positive.append(horizon)
        portfolio_passes = len(base_positive) >= int(confirmation["minimum_completed_positive_fdr_ci_horizons"]) and len(stress_positive) >= int(confirmation["minimum_2x_positive_net_return_horizons"])
        status = "research_eliminated_event" if not event_passes else "research_survivor" if portfolio_passes else "research_eliminated_portfolio"
        ranking.append({"candidate": candidate, "positive_horizons": len(positive), "stress_positive_horizons": len(stress_pass), "year_stable_horizons": len(years_pass),
                        "minimum_positive_ci_lower": min((item["confidence_interval"]["lower"] for item in positive), default=None), "passes_primary_gate": event_passes,
                        "material_negative_other_regimes": negative_other, "portfolio_positive_horizons": len(base_positive), "portfolio_2x_positive_horizons": len(stress_positive),
                        "portfolio_minimum_positive_ci_lower": min((item["confidence_interval"]["lower"] for item in base_positive), default=None), "passes_portfolio_gate": portfolio_passes,
                        "pre_portfolio_status": "event_study_survivor_pending_portfolio" if event_passes else "research_eliminated_event",
                        "mean_unique_event_fraction": sum(unique_fraction[candidate]) / len(unique_fraction[candidate]) if unique_fraction[candidate] else 1.0,
                        "status": status, "approval": "forbidden", "publication": "forbidden"})
    ranking.sort(key=lambda item: (-item["portfolio_positive_horizons"], -(item["portfolio_minimum_positive_ci_lower"] if item["portfolio_minimum_positive_ci_lower"] is not None else -1e99),
                                   -item["portfolio_2x_positive_horizons"], -item["positive_horizons"], -(item["minimum_positive_ci_lower"] if item["minimum_positive_ci_lower"] is not None else -1e99),
                                   -item["mean_unique_event_fraction"], item["candidate"]))
    by_name = {item["candidate"]: item for item in ranking}
    for overlap in overlaps:
        if overlap["same_rule_family"] or max(overlap["proximity_left_covered"], overlap["proximity_right_covered"], overlap["exact_jaccard"]) >= protocol["elimination"]["high_overlap_threshold"]:
            left, right = by_name[overlap["left"]], by_name[overlap["right"]]
            if left["status"] == "research_survivor" and right["status"] == "research_survivor":
                weaker = right if ranking.index(left) < ranking.index(right) else left
                weaker["status"] = "research_eliminated_redundant_high_overlap"
    return ranking


def run_comparison(protocol_path: Path, output: Path) -> dict[str, Any]:
    output = output.resolve()
    if output.exists():
        raise FileExistsError(f"comparison result already exists: {output}")
    protocol = _read(protocol_path); check = verify_comparison_protocol(protocol)
    if check["status"] != "valid": raise ValueError("comparison protocol invalid: " + ", ".join(check["failures"]))
    if output != Path(protocol["result"]["path"]).resolve():
        raise ValueError("comparison output path differs from the preregistered write-once result path")
    validated = {item["candidate"]: validate_completed_case(Path(item["case_path"]), item) for item in protocol["candidates"]}
    if len({item["dataset_snapshot_id"] for item in validated.values()}) != 1: raise ValueError("candidate cases use different dataset snapshots")
    panel_path = Path(protocol["market_panel"]["manifest_path"])
    if file_hash(panel_path) != protocol["market_panel"]["manifest_sha256"]:
        raise ValueError("frozen comparison panel manifest hash mismatch")
    panel = ShardedPanel(panel_path, expected_snapshot_id=protocol["shared_dataset_snapshot_id"], expected_oos=protocol["oos"])
    if panel.manifest["panel_id"] != protocol["market_panel"]["panel_id"]:
        raise ValueError("frozen comparison panel identity mismatch")
    code_record = protocol["comparison_code_snapshot"]; comparison_snapshot_path = Path(code_record["manifest_path"])
    expected_comparison_snapshot_path = protocol_path.resolve().with_name("comparison_code_snapshot.json")
    if comparison_snapshot_path.resolve() != expected_comparison_snapshot_path:
        raise ValueError("frozen comparison code snapshot path is not the protocol sibling")
    if not comparison_snapshot_path.is_file() or file_hash(comparison_snapshot_path) != code_record["manifest_sha256"]:
        raise ValueError("frozen comparison code snapshot artifact missing or tampered")
    code_check = verify_code_snapshot(REPOSITORY_ROOT, comparison_snapshot_path)
    if code_check["status"] != "valid" or code_check["code_snapshot_id"] != code_record["code_snapshot_id"]:
        raise ValueError("current project code differs from frozen comparison snapshot")
    if code_record["code_snapshot_id"] != panel.manifest["builder_code_snapshot_id"]:
        raise ValueError("panel builder and comparison code snapshots differ")
    horizons = [int(item) for item in protocol["analysis"]["horizons"]]; names = sorted(validated)
    staging_dir = Path(protocol["result"]["staging_path"])
    staging = build_compact_staging(protocol, validated, panel, staging_dir)
    staging = verify_staging(protocol, panel, staging_dir)
    overlaps = []; families = {item["candidate"]: item["rule_family"] for item in protocol["candidates"]}
    for index, left in enumerate(names):
        for right in names[index + 1:]:
            overlaps.append({"left": left, "right": right, "same_rule_family": families[left] == families[right],
                             **_staged_overlap(staging_dir, staging, panel, left, right, int(protocol["analysis"]["proximity_trading_bars"]))})
    primary, secondary = _staged_statistics(staging_dir, staging, names, horizons, protocol["analysis"]["hac_lags"])
    base_round_trip = 2 * sum(float(value) for value in protocol["execution"]["base_cost_bps_per_side"].values()) / 10_000
    for item in primary:
        daily = item.pop("_daily_values")
        item["stress"] = {str(multiplier): hac_mean([value - (float(multiplier) - 1) * base_round_trip for value in daily], int(protocol["analysis"]["hac_lags"][str(item["horizon"])])) for multiplier in protocol["execution"]["stress_multipliers"]}
    for item in secondary: item.pop("_daily_values", None)
    apply_fdr_bh(primary, float(protocol["multiple_testing"]["alpha"]))
    portfolio_gate = {"status": "completed_bounded_daily_ledger", "engine_assessment": "symbol-sharded panel; active cache bounded by max_positions", "event_mean_used_as_portfolio_pnl": False}
    global_dates = panel.trading_dates()
    portfolio = {}
    for candidate in names:
        portfolio[candidate] = {}
        for horizon in horizons:
            base = _staged_portfolio(staging_dir, staging, panel, protocol, candidate, horizon, global_dates)
            stress = _staged_portfolio(staging_dir, staging, panel, protocol, candidate, horizon, global_dates, cost_multiplier=2.0)
            portfolio[candidate][str(horizon)] = {**base, "cost_stress": {"2.0": stress}}
    portfolio_hac = _apply_portfolio_fdr(portfolio, names, horizons, float(protocol["multiple_testing"]["alpha"]))
    ranking = _finalize_ranking(primary, secondary, overlaps, portfolio, protocol, names, horizons)
    cooldown_events = {item["candidate"]: {"count": item["events"], "selection_hash": item["selection_hash"]} for item in staging["candidates"]}
    result = {"schema_version": RESULT_SCHEMA, "comparison_id": protocol["comparison_id"], "comparison_hash": protocol["comparison_hash"],
              "completed_at": datetime.now(timezone.utc), "event_overlap": overlaps,
              "cooldown_events": cooldown_events, "staging": {"path": str(staging_dir), "staging_hash": staging["staging_hash"], "diagnostics": staging["diagnostics"]},
              "primary_hac": primary, "other_regime_evidence": secondary, "portfolio_validation": {**portfolio_gate, "hac_family": portfolio_hac, "ledgers": portfolio},
              "ranking": ranking, "scope": "research_ranking_only", "approval": "forbidden", "publication": "forbidden", "final_lockbox_read": False}
    result["result_hash"] = canonical_hash({key: value for key, value in result.items() if key not in {"completed_at", "result_hash"}})
    write_json(output, result); return result
