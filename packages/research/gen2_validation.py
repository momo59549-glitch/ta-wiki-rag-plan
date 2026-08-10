"""Future-only Gen2 execution/evidence contract and synthetic evaluator.

No function in this module discovers candidates or loads a market root.  A
caller supplies a small in-memory frame only after the preregistered future
window exists.  Final-lockbox rows are rejected before any signal evaluation.
"""
from __future__ import annotations
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from packages.contracts import RuleDefinition
from packages.research.candidate_comparison import apply_fdr_bh, hac_mean
from packages.research.execution import assess_execution
from packages.research.gen2_discovery import apply_context_filters, canonical_hash, gen2_candidate_semantic_id
from packages.research.gen2_discovery import verify_gen2_protocol
from packages.research.indicators import compute_indicators
from packages.research.json_store import write_json
from packages.research.readiness import build_code_snapshot, verify_code_snapshot
from packages.research.rule_search import _base_columns, vectorized_evaluate
from packages.rule_dsl import compile_rule, rule_logic_hash

STAGE2_SCHEMA = "gen2-future-validation-contract/v1"
OBS_SCHEMA = "gen2-future-observation-shard/v1"
_EXECUTION_PLAN = {
    "signal": "T_close", "entry": "T_plus_1_open",
    "exit": "entry_plus_horizon_minus_1_close", "horizons": [5, 10, 20],
    "commission_bps_per_side": 3.0, "slippage_bps_per_side": 5.0,
    "reuse": "packages.research.execution.assess_execution",
    "max_positions": 10, "max_exit_delay_bars": 5,
    "same_symbol_overlap": "forbidden", "portfolio_weighting": "equal_weight",
}
_DATASET_FIELDS = {"schema_version", "status", "asset_dataset_id", "benchmark_dataset_id", "calendar_id", "price_fields", "metadata"}
_PIT_FIELDS = {"schema_version", "status", "manifest_id", "membership_policy", "metadata"}
_DATASET_SCHEMA = "gen2-future-dataset-contract/v1"
_PIT_SCHEMA = "gen2-pit-universe-contract/v1"


def _verify_dataset_contract(value: Mapping[str, Any]) -> None:
    if set(value) != _DATASET_FIELDS or value.get("schema_version") != _DATASET_SCHEMA:
        raise ValueError("Stage2 dataset contract schema invalid")
    if value.get("status") not in {"future_not_arrived", "contract_only"}:
        raise ValueError("Stage2 cannot claim an existing dataset snapshot")
    if (not all(isinstance(value.get(key), str) and value[key] for key in ("asset_dataset_id", "benchmark_dataset_id", "calendar_id"))
            or value.get("price_fields") != ["open", "high", "low", "close", "volume"]
            or not isinstance(value.get("metadata"), Mapping)):
        raise ValueError("Stage2 dataset contract fields invalid")


def _verify_pit_contract(value: Mapping[str, Any]) -> None:
    if set(value) != _PIT_FIELDS or value.get("schema_version") != _PIT_SCHEMA:
        raise ValueError("Stage2 PIT contract schema invalid")
    if (value.get("status") not in {"future_not_arrived", "contract_only"}
            or not isinstance(value.get("manifest_id"), str) or not value["manifest_id"]
            or value.get("membership_policy") != "point_in_time"
            or not isinstance(value.get("metadata"), Mapping)):
        raise ValueError("Stage2 PIT contract fields invalid")
_STATISTICS_PLAN = {
    "family": "all_candidates_x_5_10_20", "fdr": "BH",
    "event_hac_lag_equals_horizon": True, "portfolio_path": True,
    "cost_stress_multiplier": 2.0, "min_events": 100,
    "minimum_positive_horizons": 2, "both_regimes_required": False,
    "ranking_is_not_approval": True,
}


def _new(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists(): raise FileExistsError(f"write-once artifact already exists: {path}")
    write_json(path, dict(value))


def build_stage2_contract(gen2_protocol: Mapping[str, Any], *, dataset_contract: Mapping[str, Any], pit_universe_contract: Mapping[str, Any], output: Path | None = None, project_root: Path | None = None) -> dict[str, Any]:
    """Freeze execution/statistical plan; dataset is a contract, never a claim it exists."""
    if gen2_protocol.get("schema_version") != "gen2-discovery-protocol/v1" or gen2_protocol.get("status") != "preregistered_no_screen_run":
        raise ValueError("Gen2 protocol must be verified preregistration")
    periods = gen2_protocol["periods"]
    if periods.get("planned_fresh_oos") is not True or periods.get("final_lockbox_read") is not False:
        raise ValueError("Gen2 protocol has invalid future/lockbox status")
    _verify_dataset_contract(dataset_contract)
    _verify_pit_contract(pit_universe_contract)
    code = {"status": "not_captured_dry_run"}
    if output is not None:
        if output.exists(): raise FileExistsError(f"stage2 output exists: {output}")
        output.mkdir(parents=True)
        manifest_path = (output / "code_snapshot.json").resolve()
        code = {
            **build_code_snapshot((project_root or Path.cwd()).resolve(), manifest_path),
            "manifest_path": str(manifest_path),
        }
    preregistered_at = datetime.now(timezone.utc).isoformat()
    identity = {
        "schema_version": STAGE2_SCHEMA, "status": "preregistered_contract_only",
        "gen2_protocol_hash": gen2_protocol["protocol_hash"], "candidate_semantic_ids": sorted(x["candidate_semantic_id"] for x in gen2_protocol["candidate_space"]["candidates"]),
        "benchmark_symbol": gen2_protocol["grammar"]["benchmark_symbol"], "dataset_contract": dict(dataset_contract), "pit_universe_contract": dict(pit_universe_contract),
        "periods": {"validation_start": periods["validation_start"], "research_end": periods["research_end"], "final_lockbox_start": periods["final_lockbox_start"]},
        "execution": _EXECUTION_PLAN,
        "statistics": _STATISTICS_PLAN,
        "code_snapshot": code, "final_lockbox_read": False, "preregistered_at": preregistered_at,
    }
    payload = {**identity, "contract_hash": canonical_hash(identity), "contract_id": "gen2_stage2_" + canonical_hash(identity)[7:31]}
    if output is not None: _new(output / "stage2_contract.json", payload)
    return payload


def verify_stage2_contract(contract: Mapping[str, Any], *, gen2_protocol: Mapping[str, Any], ledger: Mapping[str, Any], parent_protocol_path: Path, parent_closure_result_path: Path, project_root: Path | None = None) -> None:
    """Fail closed before a contract or a shard is considered executable."""
    allowed = {"schema_version", "status", "gen2_protocol_hash", "candidate_semantic_ids", "benchmark_symbol", "dataset_contract", "pit_universe_contract", "periods", "execution", "statistics", "code_snapshot", "final_lockbox_read", "preregistered_at", "contract_hash", "contract_id"}
    if set(contract) != allowed: raise ValueError("Stage2 contract fields invalid")
    identity = {key: contract[key] for key in contract if key not in {"contract_hash", "contract_id"}}
    digest = canonical_hash(identity)
    if (contract.get("schema_version") != STAGE2_SCHEMA or contract.get("status") != "preregistered_contract_only"
            or contract.get("final_lockbox_read") is not False
            or contract.get("contract_hash") != digest or contract.get("contract_id") != "gen2_stage2_" + digest[7:31]):
        raise ValueError("Stage2 contract hash/id invalid or status invalid")
    verify_gen2_protocol(gen2_protocol, ledger=ledger, parent_protocol_path=parent_protocol_path, parent_closure_result_path=parent_closure_result_path)
    if contract["gen2_protocol_hash"] != gen2_protocol["protocol_hash"]: raise ValueError("Stage2 Gen2 protocol binding invalid")
    expected_ids = sorted(x["candidate_semantic_id"] for x in gen2_protocol["candidate_space"]["candidates"])
    if contract["candidate_semantic_ids"] != expected_ids or contract["benchmark_symbol"] != gen2_protocol["grammar"]["benchmark_symbol"]: raise ValueError("Stage2 candidate/benchmark binding invalid")
    _verify_dataset_contract(contract["dataset_contract"])
    _verify_pit_contract(contract["pit_universe_contract"])
    if set(contract["periods"]) != {"validation_start", "research_end", "final_lockbox_start"}: raise ValueError("Stage2 periods fields invalid")
    if contract["periods"] != {key: gen2_protocol["periods"][key] for key in contract["periods"]}: raise ValueError("Stage2 periods binding invalid")
    stamp = datetime.fromisoformat(contract["preregistered_at"])
    if stamp.tzinfo is None or stamp >= datetime.combine(date.fromisoformat(str(contract["periods"]["validation_start"])), datetime.min.time(), tzinfo=timezone.utc): raise ValueError("Stage2 preregistration too late")
    if contract["execution"] != _EXECUTION_PLAN: raise ValueError("Stage2 execution plan invalid")
    if contract["statistics"] != _STATISTICS_PLAN: raise ValueError("Stage2 statistics plan invalid")
    code = contract["code_snapshot"]
    if code.get("status") == "not_captured_dry_run":
        raise ValueError("Stage2 dry-run contract is not executable")
    if not code.get("manifest_path") or verify_code_snapshot((project_root or Path.cwd()).resolve(), Path(code["manifest_path"]))["status"] != "valid": raise ValueError("Stage2 code snapshot invalid")


def _verified_candidate(candidate: Mapping[str, Any], *, contract: Mapping[str, Any], gen2_protocol: Mapping[str, Any], ledger: Mapping[str, Any], parent_protocol_path: Path, parent_closure_result_path: Path, project_root: Path | None) -> dict[str, Any]:
    """Return the one immutable protocol candidate, never a caller substitute."""
    verify_stage2_contract(contract, gen2_protocol=gen2_protocol, ledger=ledger, parent_protocol_path=parent_protocol_path, parent_closure_result_path=parent_closure_result_path, project_root=project_root)
    candidate_id = candidate.get("candidate_semantic_id")
    matches = [item for item in gen2_protocol["candidate_space"]["candidates"] if item["candidate_semantic_id"] == candidate_id]
    if len(matches) != 1 or dict(candidate) != matches[0]:
        raise ValueError("candidate mapping differs from verified Gen2 protocol")
    frozen = matches[0]
    base = RuleDefinition(**frozen["base_definition"])
    if (frozen.get("candidate_semantic_id") != gen2_candidate_semantic_id(base, frozen["context_filters"], frozen["benchmark_symbol"])
            or frozen.get("base_rule_semantic_hash") != compile_rule(base).semantic_hash
            or frozen.get("base_rule_logic_hash") != rule_logic_hash(base)
            or frozen.get("benchmark_symbol") != contract["benchmark_symbol"]
            or frozen.get("benchmark_symbol") != gen2_protocol["grammar"]["benchmark_symbol"]
            or frozen["candidate_semantic_id"] not in contract["candidate_semantic_ids"]):
        raise ValueError("verified Gen2 candidate binding invalid")
    return frozen


def evaluate_future_candidate(candidate: Mapping[str, Any], asset: pd.DataFrame, benchmark: pd.DataFrame, *, symbol: str, pit_active: pd.Series, contract: Mapping[str, Any], gen2_protocol: Mapping[str, Any], ledger: Mapping[str, Any], parent_protocol_path: Path, parent_closure_result_path: Path, project_root: Path | None = None) -> list[dict[str, Any]]:
    """Evaluate only a verified, frozen Gen2 candidate with T+1 semantics."""
    candidate = _verified_candidate(candidate, contract=contract, gen2_protocol=gen2_protocol, ledger=ledger, parent_protocol_path=parent_protocol_path, parent_closure_result_path=parent_closure_result_path, project_root=project_root)
    validation_start = date.fromisoformat(str(contract["periods"]["validation_start"]))
    research_end = date.fromisoformat(str(contract["periods"]["research_end"]))
    final_lockbox_start = date.fromisoformat(str(contract["periods"]["final_lockbox_start"]))
    if not isinstance(asset.index, pd.DatetimeIndex) or not asset.index.is_monotonic_increasing or not asset.index.is_unique: raise ValueError("asset dates invalid")
    if any(index.date() >= final_lockbox_start for index in asset.index) or any(index.date() >= final_lockbox_start for index in benchmark.index): raise ValueError("final lockbox rows must not be read")
    for label, frame, fields in (("asset", asset, ("open", "high", "low", "close", "volume")), ("benchmark", benchmark, ("open", "close"))):
        if not isinstance(frame.index, pd.DatetimeIndex) or not frame.index.is_unique or not frame.index.is_monotonic_increasing or any(key not in frame for key in fields):
            raise ValueError(f"{label} OHLCV/date input invalid")
    base = RuleDefinition(**candidate["base_definition"]); compiled = compile_rule(base)
    cols = _base_columns(asset); cols.update(compute_indicators(asset, needs=compiled.required_indicators))
    base_signal = vectorized_evaluate(compiled.normalized_expression, cols, base.parameters)
    signal = apply_context_filters(asset, benchmark, base_signal, candidate["context_filters"], benchmark_symbol=candidate["benchmark_symbol"])
    if not pit_active.index.equals(asset.index) or not pd.api.types.is_bool_dtype(pit_active.dtype): raise ValueError("PIT universe signal must be aligned bool")
    out: list[dict[str, Any]] = []
    execution = contract["execution"]
    costs = 2 * (float(execution["commission_bps_per_side"]) + float(execution["slippage_bps_per_side"])) / 10_000
    for t, matched in enumerate(signal.fillna(False)):
        if not matched or not bool(pit_active.fillna(False).iloc[t]) or not validation_start <= asset.index[t].date() <= research_end: continue
        for horizon in execution["horizons"]:
            entry_i, exit_i = t + 1, t + horizon
            if exit_i >= len(asset):
                out.append({"candidate_semantic_id": candidate["candidate_semantic_id"], "symbol": symbol, "signal_date": asset.index[t].date().isoformat(), "horizon": horizon, "status": "tail_purged"}); continue
            if asset.index[entry_i].date() > research_end or asset.index[exit_i].date() > research_end:
                out.append({"candidate_semantic_id": candidate["candidate_semantic_id"], "symbol": symbol, "signal_date": asset.index[t].date().isoformat(), "horizon": horizon, "status": "tail_purged"}); continue
            entry, exit_ = asset.iloc[entry_i], asset.iloc[exit_i]
            buy = assess_execution({**entry.to_dict(), "date": asset.index[entry_i]}, symbol=symbol, side="buy", price_at="open", require_session_liquidity=False)
            sell = assess_execution({**exit_.to_dict(), "date": asset.index[exit_i]}, symbol=symbol, side="sell", price_at="close")
            if not buy.executable or not sell.executable: continue
            bframe = benchmark.reindex([asset.index[entry_i], asset.index[exit_i]])
            bentry, bexit = bframe.iloc[0]["open"], bframe.iloc[1]["close"]
            if not np.isfinite(bentry) or not np.isfinite(bexit) or bentry <= 0 or bexit <= 0: continue
            gross = float(exit_["close"]) / float(entry["open"]) - 1
            out.append({"candidate_semantic_id": candidate["candidate_semantic_id"], "symbol": symbol, "signal_date": asset.index[t].date().isoformat(), "entry_date": asset.index[entry_i].date().isoformat(), "exit_date": asset.index[exit_i].date().isoformat(), "horizon": horizon, "status": "completed", "net_return": gross - costs, "benchmark_return": float(bexit / bentry - 1), "excess_return": gross - costs - float(bexit / bentry - 1), "cost_round_trip": costs})
    return out


def summarize_future_evidence(events: Iterable[Mapping[str, Any]], *, expected_candidates: Iterable[str], as_of: date, research_end: date, contract: Mapping[str, Any] | None = None, portfolio_confirmation: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Event evidence is descriptive until an independent portfolio ledger passes."""
    horizons = list(contract["execution"]["horizons"]) if contract is not None else [5, 10, 20]
    records = [x for x in events if x.get("status", "completed") == "completed"]
    tails = [x for x in events if x.get("status") == "tail_purged"]
    if as_of < research_end: return {"status": "accumulating_not_adjudicable", "winner_selection": "forbidden", "event_count": len(records)}
    stats = []
    for candidate in expected_candidates:
        for horizon in horizons:
            selected = [x for x in records if x["candidate_semantic_id"] == candidate and int(x["horizon"]) == horizon]
            by_day: dict[str, list[float]] = {}
            for row in selected: by_day.setdefault(str(row["signal_date"]), []).append(float(row["excess_return"]))
            values = [float(np.mean(by_day[day])) for day in sorted(by_day)]
            item = {"candidate": candidate, "horizon": horizon, "events": len(selected), "dates": len(values), **hac_mean(values, horizon)} if len(selected) >= 100 else {"candidate": candidate, "horizon": horizon, "events": len(selected), "dates": len(values), "evidence_status": "insufficient_events", "raw_p_value": 1.0}
            stats.append(item)
    family = [dict(x) for x in stats]
    for x in family: x.setdefault("raw_p_value", 1.0)
    apply_fdr_bh(family, 0.05)
    for item, adjusted in zip(stats, family): item["adjusted_p_value"], item["fdr_reject"] = adjusted.get("adjusted_p_value"), adjusted.get("fdr_reject", False)
    stress_family = []
    for item in stats:
        selected = [x for x in records if x["candidate_semantic_id"] == item["candidate"] and int(x["horizon"]) == item["horizon"]]
        by_day: dict[str, list[float]] = {}
        for row in selected: by_day.setdefault(str(row["signal_date"]), []).append(float(row["excess_return"]) - float(row.get("cost_round_trip", 0.0)))
        values = [float(np.mean(by_day[day])) for day in sorted(by_day)]
        stress = hac_mean(values, int(item["horizon"])) if len(selected) >= 100 else {"raw_p_value": 1.0, "evidence_status": "insufficient_events", "n_dates": len(values)}
        item["cost_stress_2x"] = stress; item["cost_stress_2x_mean_excess"] = stress.get("effect")
        stress_family.append({"candidate": item["candidate"], "horizon": item["horizon"], **stress})
        item["tail_purged_count"] = sum(1 for row in tails if row.get("candidate_semantic_id") == item["candidate"] and int(row.get("horizon", -1)) == item["horizon"])
    for item in stress_family: item.setdefault("raw_p_value", 1.0)
    apply_fdr_bh(stress_family, 0.05)
    for item, adjusted in zip(stats, stress_family):
        item["cost_stress_2x"]["adjusted_p_value"] = adjusted["adjusted_p_value"]
        item["cost_stress_2x"]["fdr_reject"] = adjusted["fdr_reject"]
    valid_confirmation = bool(contract is not None and portfolio_confirmation is not None and portfolio_confirmation.get("status") == "completed"
                              and portfolio_confirmation.get("contract_hash") == contract.get("contract_hash")
                              and portfolio_confirmation.get("candidate_semantic_ids") == list(expected_candidates)
                              and portfolio_confirmation.get("horizons") == horizons)
    portfolio_status = "missing_fail_closed" if portfolio_confirmation is None else "completed" if valid_confirmation else "portfolio_failed_closed"
    return {"status": "adjudicable_with_portfolio_confirmation" if valid_confirmation else "adjudicable_event_only_portfolio_confirmation_missing" if portfolio_confirmation is None else "portfolio_failed_closed", "statistics": stats, "tail_purged_count": len(tails), "approval": "forbidden", "publication": "forbidden", "winner_selection": "forbidden", "portfolio_confirmation": portfolio_status, "event_fdr_family": len(family), "cost_stress_fdr_family": len(stress_family)}


def _portfolio_ledger(plans: list[Mapping[str, Any]], prices: Mapping[str, pd.DataFrame], calendar: list[pd.Timestamp], *, candidate: str, horizon: int, execution: Mapping[str, Any], cost_multiplier: float) -> dict[str, Any]:
    """Small deterministic cash/position ledger; it intentionally has no IO."""
    index = {day.date().isoformat(): position for position, day in enumerate(calendar)}
    selected = []
    tail_purged = 0
    for plan in plans:
        if plan["candidate_semantic_id"] != candidate or int(plan["horizon"]) != horizon: continue
        if set(plan) != {"candidate_semantic_id", "symbol", "signal_date", "horizon"}: raise ValueError("portfolio plan schema invalid")
        signal = str(plan["signal_date"])
        if signal not in index: raise ValueError("portfolio signal absent from frozen calendar")
        entry_i, exit_i = index[signal] + 1, index[signal] + horizon
        if exit_i >= len(calendar): tail_purged += 1; continue
        selected.append({**plan, "entry_i": entry_i, "exit_i": exit_i})
    entries: dict[int, list[dict[str, Any]]] = {}
    for plan in selected: entries.setdefault(plan["entry_i"], []).append(plan)
    side_cost = (float(execution["commission_bps_per_side"]) + float(execution["slippage_bps_per_side"])) / 10_000 * cost_multiplier
    cash, active, curve, turnover, rejected, unresolved = 1.0, {}, [], 0.0, {}, 0
    max_positions, max_delay = int(execution["max_positions"]), int(execution["max_exit_delay_bars"])
    for position, day in enumerate(calendar):
        day_text = day.date().isoformat()
        for symbol, holding in list(active.items()):
            if position < holding["exit_i"]: continue
            frame = prices.get(symbol); row = frame.loc[day] if frame is not None and day in frame.index else None
            sell = assess_execution({**(row.to_dict() if row is not None else {}), "date": day}, symbol=symbol, side="sell", price_at="close")
            if sell.executable:
                proceeds = holding["shares"] * float(row["close"]) * (1 - side_cost); cash += proceeds; turnover += proceeds; del active[symbol]
            elif position - holding["exit_i"] >= max_delay:
                unresolved += 1; rejected["unresolved_exit"] = rejected.get("unresolved_exit", 0) + 1; del active[symbol]
        equity_open = cash + sum(item["shares"] * item["last_close"] for item in active.values())
        for plan in sorted(entries.get(position, []), key=lambda item: (item["symbol"], item["signal_date"])):
            symbol = str(plan["symbol"])
            if symbol in active:
                rejected["same_symbol_overlap_forbidden"] = rejected.get("same_symbol_overlap_forbidden", 0) + 1; continue
            if len(active) >= max_positions:
                rejected["max_positions"] = rejected.get("max_positions", 0) + 1; continue
            frame = prices.get(symbol); row = frame.loc[day] if frame is not None and day in frame.index else None
            buy = assess_execution({**(row.to_dict() if row is not None else {}), "date": day}, symbol=symbol, side="buy", price_at="open", require_session_liquidity=False)
            if not buy.executable:
                rejected["entry_untradeable"] = rejected.get("entry_untradeable", 0) + 1; continue
            notional = min(cash, equity_open / max_positions)
            if notional <= 0: continue
            price = float(row["open"]) * (1 + side_cost); cash -= notional; turnover += notional
            active[symbol] = {**plan, "shares": notional / price, "last_close": float(row["open"])}
        for symbol, holding in active.items():
            frame = prices.get(symbol); row = frame.loc[day] if frame is not None and day in frame.index else None
            if row is None or not np.isfinite(float(row["close"])) or float(row["close"]) <= 0:
                unresolved += 1; rejected["missing_mark"] = rejected.get("missing_mark", 0) + 1; continue
            holding["last_close"] = float(row["close"])
        equity = cash + sum(item["shares"] * item["last_close"] for item in active.values())
        curve.append({"date": day_text, "equity": equity, "cash": cash, "positions": len(active)})
    if active: unresolved += len(active)
    blocked = unresolved > 0
    returns = []; previous = 1.0
    for item in curve:
        returns.append(item["equity"] / previous - 1 if previous else 0.0)
        previous = item["equity"]
    peak = 0.0; drawdown = 0.0
    for item in curve: peak = max(peak, item["equity"]); drawdown = min(drawdown, item["equity"] / peak - 1 if peak else 0.0)
    return {"status": "blocked_unresolved_exit" if blocked else "completed", "candidate": candidate, "horizon": horizon, "cost_multiplier": cost_multiplier, "portfolio_net_return": None if blocked else curve[-1]["equity"] - 1 if curve else 0.0, "daily_returns": returns, "hac": {**hac_mean(returns, horizon), "evidence_status": "blocked_ledger"} if blocked else hac_mean(returns, horizon), "equity_curve": curve, "max_drawdown": None if blocked else drawdown, "gross_turnover": None if blocked else turnover, "rejected_counts": rejected, "unresolved_count": unresolved, "tail_purged_count": tail_purged, "accounting": "daily_cash_positions_equity"}


def evaluate_portfolio_confirmation(plans: Iterable[Mapping[str, Any]], prices: Mapping[str, pd.DataFrame], *, calendar: pd.DatetimeIndex, contract: Mapping[str, Any]) -> dict[str, Any]:
    """Independent portfolio gate for a frozen calendar and frozen daily plans."""
    if not isinstance(calendar, pd.DatetimeIndex) or not calendar.is_monotonic_increasing or not calendar.is_unique:
        raise ValueError("portfolio calendar invalid")
    periods = contract["periods"]; start, end, lockbox = (date.fromisoformat(str(periods[key])) for key in ("validation_start", "research_end", "final_lockbox_start"))
    if any(day.date() < start or day.date() > end or day.date() >= lockbox for day in calendar): raise ValueError("portfolio calendar crosses contract boundary")
    for symbol, frame in prices.items():
        if (not isinstance(frame, pd.DataFrame) or not isinstance(frame.index, pd.DatetimeIndex) or not frame.index.is_monotonic_increasing or not frame.index.is_unique
                or any(field not in frame for field in ("open", "high", "low", "close", "volume"))):
            raise ValueError(f"portfolio price frame invalid: {symbol}")
        if any(day.date() < start or day.date() > end or day.date() >= lockbox for day in frame.index):
            raise ValueError("portfolio price frame crosses contract boundary")
        for field in ("open", "high", "low", "close", "volume"):
            numeric = pd.to_numeric(frame[field], errors="coerce")
            if (~np.isfinite(numeric) | (numeric <= 0)).any(): raise ValueError(f"portfolio price field invalid: {symbol}.{field}")
    frozen = list(plans); candidates, horizons, execution = list(contract["candidate_semantic_ids"]), list(contract["execution"]["horizons"]), contract["execution"]
    if any(plan.get("candidate_semantic_id") not in candidates or int(plan.get("horizon", -1)) not in horizons for plan in frozen): raise ValueError("portfolio plan not bound to contract")
    plan_keys = [(plan.get("candidate_semantic_id"), plan.get("symbol"), plan.get("signal_date"), plan.get("horizon")) for plan in frozen]
    if len(plan_keys) != len(set(plan_keys)): raise ValueError("duplicate portfolio plan key")
    ledgers: dict[str, dict[str, Any]] = {}
    base_family, stress_family = [], []
    days = list(calendar)
    for candidate in candidates:
        ledgers[candidate] = {}
        for horizon in horizons:
            base = _portfolio_ledger(frozen, prices, days, candidate=candidate, horizon=horizon, execution=execution, cost_multiplier=1.0)
            stress = _portfolio_ledger(frozen, prices, days, candidate=candidate, horizon=horizon, execution=execution, cost_multiplier=2.0)
            ledgers[candidate][str(horizon)] = {**base, "cost_stress": {"2.0": stress}}
            base_family.append({"candidate": candidate, "horizon": horizon, **base["hac"]}); stress_family.append({"candidate": candidate, "horizon": horizon, **stress["hac"]})
    for family in (base_family, stress_family):
        for item in family: item.setdefault("raw_p_value", 1.0)
        apply_fdr_bh(family, 0.05)
    for family, stressed in ((base_family, False), (stress_family, True)):
        for item in family:
            target = ledgers[item["candidate"]][str(item["horizon"])]["cost_stress"]["2.0"]["hac"] if stressed else ledgers[item["candidate"]][str(item["horizon"])]["hac"]
            target["adjusted_p_value"], target["fdr_reject"] = item["adjusted_p_value"], item["fdr_reject"]
    status = "completed" if all(value["status"] == "completed" and value["cost_stress"]["2.0"]["status"] == "completed" for values in ledgers.values() for value in values.values()) else "failed_closed"
    return {"status": status, "contract_hash": contract["contract_hash"], "candidate_semantic_ids": candidates, "horizons": horizons, "accounting": "fixed_equal_weight_cash_positions", "base_fdr_family": base_family, "cost_stress_fdr_family": stress_family, "ledgers": ledgers, "approval": "forbidden", "publication": "forbidden"}


def run_synthetic_smoke() -> dict[str, Any]:
    """Execute a real, tiny in-memory portfolio ledger with no filesystem IO."""
    calendar = pd.date_range("2026-09-02", periods=8, freq="B")
    values = np.arange(len(calendar), dtype=float)
    frame = pd.DataFrame({"open": 100 + values, "high": 101 + values, "low": 99 + values, "close": 100.5 + values, "volume": np.full(len(calendar), 1000.0)}, index=calendar)
    candidate = "sha256:" + "s" * 64
    contract = {"contract_hash": "sha256:" + "f" * 64, "candidate_semantic_ids": [candidate], "periods": {"validation_start": "2026-09-02", "research_end": calendar[-1].date().isoformat(), "final_lockbox_start": "2026-10-01"}, "execution": {"horizons": [5], "commission_bps_per_side": 3.0, "slippage_bps_per_side": 5.0, "max_positions": 1, "max_exit_delay_bars": 1, "same_symbol_overlap": "forbidden", "portfolio_weighting": "equal_weight"}}
    result = evaluate_portfolio_confirmation([{"candidate_semantic_id": candidate, "symbol": "000001", "signal_date": calendar[0].date().isoformat(), "horizon": 5}], {"000001": frame}, calendar=calendar, contract=contract)
    if result["status"] != "completed": raise AssertionError("synthetic portfolio smoke did not complete")
    return {"portfolio_status": result["status"], "ledger_count": 1, "writes_data": False, "market_data_read": False, "fixture_checks": ["in_memory_portfolio_completed", "no_filesystem_io", "no_market_data_source"]}


def _event_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (row["candidate_semantic_id"], row["symbol"], row["signal_date"], row["horizon"])


def _validate_commit_sidecar(commit: Mapping[str, Any], shard: Mapping[str, Any], *, contract_hash: str) -> set[tuple[Any, ...]]:
    if (set(commit) != {"schema_version", "shard_hash", "keys", "contract_hash"}
            or commit["schema_version"] != "gen2-observation-commit/v1"
            or commit["contract_hash"] != contract_hash
            or commit["shard_hash"] != shard.get("shard_hash")
            or not isinstance(commit.get("keys"), list)):
        raise ValueError("commit sidecar invalid")
    actual = {_event_key(row) for row in shard.get("rows", [])}
    try: recorded = {tuple(key) for key in commit["keys"]}
    except TypeError as exc: raise ValueError("commit sidecar keys invalid") from exc
    if len(recorded) != len(commit["keys"]) or recorded != actual:
        raise ValueError("commit sidecar keys differ from shard")
    return recorded


def commit_future_observation_shard(root: Path, events: Iterable[Mapping[str, Any]], *, contract: Mapping[str, Any]) -> dict[str, Any]:
    """Write one resumable immutable shard; duplicate logical rows fail closed."""
    rows = list(events); keys = set(); periods = contract["periods"]
    validation_start = date.fromisoformat(str(periods["validation_start"]))
    research_end = date.fromisoformat(str(periods["research_end"]))
    final_lockbox_start = date.fromisoformat(str(periods["final_lockbox_start"]))
    candidate_ids = set(contract["candidate_semantic_ids"])
    expected_cost = 2 * (float(contract["execution"]["commission_bps_per_side"]) + float(contract["execution"]["slippage_bps_per_side"])) / 10_000
    for row in rows:
        completed = row.get("status") == "completed"
        tail = row.get("status") == "tail_purged"
        completed_fields = {"candidate_semantic_id", "symbol", "signal_date", "entry_date", "exit_date", "horizon", "status", "net_return", "benchmark_return", "excess_return", "cost_round_trip"}
        tail_fields = {"candidate_semantic_id", "symbol", "signal_date", "horizon", "status"}
        if set(row) != (completed_fields if completed else tail_fields if tail else set()): raise ValueError("observation row schema invalid")
        day = date.fromisoformat(str(row["signal_date"]))
        if day < validation_start or day > research_end or day >= final_lockbox_start:
            raise ValueError("observation outside future validation boundary")
        key = _event_key(row)
        if row["candidate_semantic_id"] not in candidate_ids or int(row["horizon"]) not in contract["execution"]["horizons"]:
            raise ValueError("observation row binding/arithmetic invalid")
        if completed:
            entry_day, exit_day = date.fromisoformat(str(row["entry_date"])), date.fromisoformat(str(row["exit_date"]))
            numbers = (float(row["net_return"]), float(row["benchmark_return"]), float(row["excess_return"]), float(row["cost_round_trip"]))
            if (entry_day > research_end or exit_day > research_end or any(value >= final_lockbox_start for value in (entry_day, exit_day))
                    or not day < entry_day <= exit_day or not all(np.isfinite(value) for value in numbers)
                    or abs(float(row["excess_return"]) - (float(row["net_return"]) - float(row["benchmark_return"]))) > 1e-12
                    or abs(float(row["cost_round_trip"]) - expected_cost) > 1e-12):
                raise ValueError("observation row binding/arithmetic invalid")
        if key in keys: raise ValueError("duplicate candidate/symbol/date/horizon observation")
        keys.add(key)
    identity = {"schema_version": OBS_SCHEMA, "contract_hash": contract["contract_hash"], "dataset_identity": contract["dataset_contract"], "pit_manifest_identity": contract["pit_universe_contract"], "candidate_semantic_ids": sorted(candidate_ids), "periods": periods, "rows": sorted(rows, key=lambda x: (x["candidate_semantic_id"], x["symbol"], x["signal_date"], x["horizon"]))}
    digest = canonical_hash(identity); path = root / "shards" / f"{digest[7:]}.json"; commits = root / "commits"
    root.mkdir(parents=True, exist_ok=True); lock = root / ".observation-commit.lock"
    try:
        # mkdir is exclusive on supported local filesystems.  We deliberately
        # never steal a stale lock: an operator must audit it first.
        lock.mkdir()
    except FileExistsError as exc:
        raise ValueError("observation commit lock exists; explicit audit/recovery required") from exc
    try:
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get("shard_hash") != digest or canonical_hash({key: value for key, value in existing.items() if key not in {"shard_hash", "committed_at"}}) != digest: raise ValueError("existing shard collision/tamper")
            sidecar_path = commits / f"{digest[7:]}.json"
            if not sidecar_path.is_file(): raise ValueError("orphan shard requires explicit audit/recovery")
            _validate_commit_sidecar(json.loads(sidecar_path.read_text(encoding="utf-8")), existing, contract_hash=contract["contract_hash"])
            return {"status": "already_committed", "path": str(path), "shard_hash": digest}
        seen = set()
        for commit_path in sorted(commits.glob("*.json")) if commits.is_dir() else []:
            commit = json.loads(commit_path.read_text(encoding="utf-8"))
            shard_path = root / "shards" / f"{commit.get('shard_hash', '')[7:]}.json"
            if not shard_path.is_file(): raise ValueError("committed shard missing")
            shard = json.loads(shard_path.read_text(encoding="utf-8"))
            if shard.get("shard_hash") != commit.get("shard_hash") or canonical_hash({key:value for key,value in shard.items() if key not in {"shard_hash","committed_at"}}) != commit.get("shard_hash"): raise ValueError("committed shard tampered")
            for item in _validate_commit_sidecar(commit, shard, contract_hash=contract["contract_hash"]):
                if item in seen: raise ValueError("duplicate key in committed sidecars")
                seen.add(item)
        if seen.intersection(keys): raise ValueError("cross-shard duplicate observation")
        _new(path, {**identity, "shard_hash": digest, "committed_at": datetime.now(timezone.utc).isoformat()})
        _new(commits / f"{digest[7:]}.json", {"schema_version": "gen2-observation-commit/v1", "shard_hash": digest, "contract_hash": contract["contract_hash"], "keys": [list(key) for key in sorted(keys)]})
        return {"status": "committed", "path": str(path), "shard_hash": digest}
    finally:
        try:
            lock.rmdir()
        except FileNotFoundError:
            # Do not mask the original validation/write failure.  A missing
            # owned lock is still visible in the primary exception path.
            pass
