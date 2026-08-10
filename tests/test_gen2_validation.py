from __future__ import annotations
from dataclasses import asdict
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from copy import deepcopy
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from packages.contracts import RuleDefinition
from packages.research.auto_discovery import discovery_semantic_hash
from packages.research.gen2_discovery import (
    Gen2Config, Gen2Periods, build_gen2_protocol, canonical_hash,
    initialize_global_trial_ledger, load_gen1_candidate_references,
    load_global_trial_ledger, verify_gen1_protocol,
)
from packages.research.gen2_validation import (
    build_stage2_contract, commit_future_observation_shard,
    evaluate_future_candidate, evaluate_portfolio_confirmation, summarize_future_evidence, verify_stage2_contract,
)
from packages.rule_dsl import compile_rule
from _gen2_closure_fixture import closure_fixture


def _parent_protocol(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    definition = RuleDefinition(
        id="legacy_rsi", version="1", name_zh="旧 RSI",
        expression={"lt": [{"metric": {"name": "rsi", "window": 14, "offset": 0}}, {"param": "threshold"}]},
        parameters={"threshold": 30.0},
    )
    payload = {
        "schema_version": "auto-discovery-protocol/v1", "status": "preregistered",
        "generation": {"generation_id": "g_20260809_01", "candidate_budget": 1},
        "periods": {"research_start": "2020-01-01", "validation_start": "2022-01-01", "research_end": "2026-08-04", "final_lockbox_start": "2026-09-01"},
        "candidate_space": {"candidates": [{"definition": asdict(definition), "rule_semantic_hash": compile_rule(definition).semantic_hash, "discovery_semantic_hash": discovery_semantic_hash(definition)}]},
    }
    payload["protocol_hash"] = canonical_hash(payload)
    payload["auto_discovery_protocol_id"] = "auto_discovery_" + payload["protocol_hash"].removeprefix("sha256:")[:24]
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _protocol_and_ledger(root: Path) -> tuple[dict, dict, Path, Path]:
    parent = _parent_protocol(root / "parent.json")
    closure_path, closure = closure_fixture(root)
    ledger_root = root / "ledger"
    initialize_global_trial_ledger(ledger_root, global_trial_budget=80, legacy_trial_count=64, legacy_inventory_hash="sha256:" + "c" * 64)
    ledger = load_global_trial_ledger(ledger_root)
    config = Gen2Config("g2", "g_20260809_01", verify_gen1_protocol(parent)["protocol_hash"], 4, seed=17)
    protocol = build_gen2_protocol(
        config, Gen2Periods(date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 30), date(2026, 10, 1)),
        parent_research_end=date(2026, 8, 4), global_ledger=ledger, parent_closure=closure,
        gen1_references=load_gen1_candidate_references(parent),
    )
    return protocol, ledger, parent, closure_path


def _rehash_contract(contract: dict) -> dict:
    identity = {key: value for key, value in contract.items() if key not in {"contract_hash", "contract_id"}}
    digest = canonical_hash(identity)
    contract["contract_hash"] = digest
    contract["contract_id"] = "gen2_stage2_" + digest[7:31]
    return contract


def _simple_candidate() -> dict:
    definition = RuleDefinition(
        id="always_signal", version="1", name_zh="测试信号",
        expression={"lt": [{"metric": {"name": "close", "offset": 0}}, {"param": "threshold"}]},
        parameters={"threshold": 1_000_000.0},
    )
    return {"candidate_semantic_id": "candidate", "base_definition": asdict(definition), "context_filters": [], "benchmark_symbol": "000300"}


def _frames(periods: int = 22) -> tuple[pd.DataFrame, pd.DataFrame]:
    index = pd.date_range("2026-09-01", periods=periods, freq="B")
    values = np.arange(periods, dtype=float)
    asset = pd.DataFrame({"open": 100 + values, "high": 101 + values, "low": 99 + values, "close": 100.5 + values, "volume": np.full(periods, 1000.0)}, index=index)
    benchmark = pd.DataFrame({"open": 200 + 2 * values, "close": 201 + 2 * values}, index=index)
    return asset, benchmark


def _contract():
    return {"contract_hash": "sha256:" + "a" * 64, "candidate_semantic_ids": ["sha256:" + "b" * 64], "dataset_contract": _dataset_contract(), "pit_universe_contract": _pit_contract(), "periods": {"validation_start": "2026-09-02", "research_end": "2026-09-30", "final_lockbox_start": "2026-10-01"}, "execution": {"commission_bps_per_side": 3.0, "slippage_bps_per_side": 5.0, "horizons": [5, 10, 20]}}


def _dataset_contract(status="future_not_arrived"):
    return {"schema_version": "gen2-future-dataset-contract/v1", "status": status, "asset_dataset_id": "future-assets-v1", "benchmark_dataset_id": "future-benchmark-v1", "calendar_id": "future-calendar-v1", "price_fields": ["open", "high", "low", "close", "volume"], "metadata": {}}


def _pit_contract():
    return {"schema_version": "gen2-pit-universe-contract/v1", "status": "future_not_arrived", "manifest_id": "pit-universe-v1", "membership_policy": "point_in_time", "metadata": {}}


def _row(day="2026-09-03", symbol="000001"):
    return {"candidate_semantic_id": "sha256:" + "b" * 64, "symbol": symbol, "signal_date": day, "entry_date": "2026-09-04", "exit_date": "2026-09-08", "horizon": 5, "status": "completed", "net_return": 0.01, "benchmark_return": 0.002, "excess_return": 0.008, "cost_round_trip": 0.0016}


class Stage2ContractTests(unittest.TestCase):
    def _built(self, root: Path) -> tuple[dict, dict, dict, Path, Path, Path]:
        protocol, ledger, parent, closure_path = _protocol_and_ledger(root)
        code_root = root / "code-root"; (code_root / "packages").mkdir(parents=True)
        (code_root / "packages" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
        (code_root / "pyproject.toml").write_text("[project]\nname = 'stage2-test'\nversion = '0'\n", encoding="utf-8")
        contract = build_stage2_contract(
            protocol,
            dataset_contract=_dataset_contract(), pit_universe_contract=_pit_contract(),
            output=root / "stage2", project_root=code_root,
        )
        return contract, protocol, ledger, parent, closure_path, code_root

    def test_written_contract_is_executable_and_code_snapshot_is_bound(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); contract, protocol, ledger, parent, closure_path, code_root = self._built(root)
            self.assertTrue((root / "stage2" / "stage2_contract.json").is_file())
            self.assertTrue(Path(contract["code_snapshot"]["manifest_path"]).is_file())
            verify_stage2_contract(contract, gen2_protocol=protocol, ledger=ledger, parent_protocol_path=parent, parent_closure_result_path=closure_path, project_root=code_root)

    def test_dry_run_contract_cannot_be_executed(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); protocol, ledger, parent, closure_path = _protocol_and_ledger(root)
            contract = build_stage2_contract(
                protocol, dataset_contract=_dataset_contract("contract_only"), pit_universe_contract=_pit_contract(),
            )
            self.assertEqual(contract["code_snapshot"]["status"], "not_captured_dry_run")
            with self.assertRaisesRegex(ValueError, "dry-run"):
                verify_stage2_contract(contract, gen2_protocol=protocol, ledger=ledger, parent_protocol_path=parent, parent_closure_result_path=closure_path)

    def test_rejects_contract_identity_and_all_critical_bindings(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); contract, protocol, ledger, parent, closure_path, code_root = self._built(root)
            for field in ("contract_hash", "contract_id"):
                tampered = deepcopy(contract); tampered[field] = "bad"
                with self.assertRaisesRegex(ValueError, "hash/id"):
                    verify_stage2_contract(tampered, gen2_protocol=protocol, ledger=ledger, parent_protocol_path=parent, parent_closure_result_path=closure_path, project_root=code_root)
            for field, value in (("status", "other"), ("final_lockbox_read", True)):
                with self.subTest(field=field), self.assertRaisesRegex(ValueError, "status"):
                    verify_stage2_contract(_rehash_contract({**deepcopy(contract), field: value}), gen2_protocol=protocol, ledger=ledger, parent_protocol_path=parent, parent_closure_result_path=closure_path, project_root=code_root)
            mutations = (
                ("candidate_semantic_ids", ["sha256:" + "0" * 64], "candidate/benchmark"),
                ("benchmark_symbol", "tampered-benchmark", "candidate/benchmark"),
                ("periods", {**contract["periods"], "research_end": "2026-09-29"}, "periods binding"),
                ("statistics", {**contract["statistics"], "min_events": 99}, "statistics plan"),
                ("execution", {**contract["execution"], "commission_bps_per_side": 1.0}, "execution plan"),
                ("dataset_contract", _dataset_contract("available"), "cannot claim"),
            )
            for field, value, message in mutations:
                tampered = _rehash_contract({**deepcopy(contract), field: value})
                with self.subTest(field=field), self.assertRaisesRegex(ValueError, message):
                    verify_stage2_contract(tampered, gen2_protocol=protocol, ledger=ledger, parent_protocol_path=parent, parent_closure_result_path=closure_path, project_root=code_root)

    def test_rejects_gen2_protocol_parent_and_preregistration_tampering(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); contract, protocol, ledger, parent, closure_path, code_root = self._built(root)
            changed = deepcopy(protocol); changed["grammar"]["benchmark_symbol"] = "other"; changed["protocol_hash"] = canonical_hash({key: value for key, value in changed.items() if key not in {"protocol_hash", "protocol_id", "created_at"}}); changed["protocol_id"] = "gen2_" + changed["protocol_hash"][7:31]
            with self.assertRaises(ValueError):
                verify_stage2_contract(contract, gen2_protocol=changed, ledger=ledger, parent_protocol_path=parent, parent_closure_result_path=closure_path, project_root=code_root)
            changed = deepcopy(protocol); changed["candidate_space"]["candidates"][0]["profile"] = "substituted_after_draw"; changed["protocol_hash"] = canonical_hash({key: value for key, value in changed.items() if key not in {"protocol_hash", "protocol_id", "created_at"}}); changed["protocol_id"] = "gen2_" + changed["protocol_hash"][7:31]
            with self.assertRaisesRegex(ValueError, "candidate space differs"):
                verify_stage2_contract(contract, gen2_protocol=changed, ledger=ledger, parent_protocol_path=parent, parent_closure_result_path=closure_path, project_root=code_root)
            parent_payload = json.loads(parent.read_text(encoding="utf-8")); parent_payload["periods"]["research_end"] = "2026-08-05"; parent.write_text(json.dumps(parent_payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                verify_stage2_contract(contract, gen2_protocol=protocol, ledger=ledger, parent_protocol_path=parent, parent_closure_result_path=closure_path, project_root=code_root)
            # Restore the parent so the remaining contract-only tamper is isolated.
            parent = _parent_protocol(parent)
            too_late = _rehash_contract({**deepcopy(contract), "preregistered_at": "2026-09-02T00:00:00+00:00"})
            with self.assertRaisesRegex(ValueError, "too late"):
                verify_stage2_contract(too_late, gen2_protocol=protocol, ledger=ledger, parent_protocol_path=parent, parent_closure_result_path=closure_path, project_root=code_root)

    def test_rejects_changed_code_snapshot_contents(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); contract, protocol, ledger, parent, closure_path, code_root = self._built(root)
            (code_root / "packages" / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "code snapshot"):
                verify_stage2_contract(contract, gen2_protocol=protocol, ledger=ledger, parent_protocol_path=parent, parent_closure_result_path=closure_path, project_root=code_root)


class FutureEvaluatorTests(unittest.TestCase):
    def setUp(self):
        self._temp = TemporaryDirectory(); root = Path(self._temp.name)
        self.contract, self.protocol, self.ledger, self.parent, self.closure_path, self.code_root = Stage2ContractTests()._built(root)
        self.candidate = self.protocol["candidate_space"]["candidates"][0]

    def tearDown(self): self._temp.cleanup()

    def _evaluate(self, asset: pd.DataFrame, benchmark: pd.DataFrame, pit_active: pd.Series) -> list[dict]:
        with patch("packages.research.gen2_validation.apply_context_filters", return_value=pd.Series(True, index=asset.index)):
            return evaluate_future_candidate(self.candidate, asset, benchmark, symbol="000001", pit_active=pit_active, contract=self.contract, gen2_protocol=self.protocol, ledger=self.ledger, parent_protocol_path=self.parent, parent_closure_result_path=self.closure_path, project_root=self.code_root)

    def test_t_plus_1_open_benchmark_open_costs_and_tail_purge(self):
        asset, benchmark = _frames(); events = self._evaluate(asset, benchmark, pd.Series(True, index=asset.index, dtype="boolean"))
        completed = next(row for row in events if row["signal_date"] == "2026-09-02" and row["horizon"] == 5)
        self.assertEqual(completed["entry_date"], "2026-09-03")
        self.assertEqual(completed["exit_date"], "2026-09-09")
        gross = asset.loc[pd.Timestamp("2026-09-09"), "close"] / asset.loc[pd.Timestamp("2026-09-03"), "open"] - 1
        benchmark_return = benchmark.loc[pd.Timestamp("2026-09-09"), "close"] / benchmark.loc[pd.Timestamp("2026-09-03"), "open"] - 1
        self.assertAlmostEqual(completed["net_return"], gross - 0.0016)
        self.assertAlmostEqual(completed["benchmark_return"], benchmark_return)
        self.assertAlmostEqual(completed["excess_return"], gross - 0.0016 - benchmark_return)
        self.assertAlmostEqual(completed["cost_round_trip"], 0.0016)
        self.assertTrue(any(row["status"] == "tail_purged" for row in events))

    def test_lockbox_pit_na_and_future_bars_fail_closed_or_do_not_change_past(self):
        asset, benchmark = _frames()
        late_asset = pd.concat([asset, asset.iloc[[-1]].rename(index={asset.index[-1]: pd.Timestamp("2026-10-01")})])
        with self.assertRaisesRegex(ValueError, "lockbox"):
            self._evaluate(late_asset, benchmark, pd.Series(True, index=late_asset.index, dtype="boolean"))
        late_benchmark = pd.concat([benchmark, benchmark.iloc[[-1]].rename(index={benchmark.index[-1]: pd.Timestamp("2026-10-01")})])
        with self.assertRaisesRegex(ValueError, "lockbox"):
            self._evaluate(asset, late_benchmark, pd.Series(True, index=asset.index, dtype="boolean"))
        pit = pd.Series(True, index=asset.index, dtype="boolean"); pit.iloc[1] = pd.NA
        rows = self._evaluate(asset, benchmark, pit)
        self.assertFalse(any(row["signal_date"] == "2026-09-02" for row in rows))
        baseline = self._evaluate(asset, benchmark, pd.Series(True, index=asset.index, dtype="boolean"))
        changed_asset, changed_benchmark = asset.copy(), benchmark.copy()
        changed_asset.iloc[15, changed_asset.columns.get_loc("close")] *= 3
        changed_benchmark.iloc[15, changed_benchmark.columns.get_loc("close")] *= 0.2
        changed = self._evaluate(changed_asset, changed_benchmark, pd.Series(True, index=asset.index, dtype="boolean"))
        before = [row for row in baseline if row.get("exit_date", "9999") < "2026-09-22"]
        changed_before = [row for row in changed if row.get("exit_date", "9999") < "2026-09-22"]
        self.assertEqual(before, changed_before)

class ShardTests(unittest.TestCase):
    def test_idempotent_append_and_cross_shard_conflict(self):
        with TemporaryDirectory() as temp:
            root=Path(temp); contract=_contract()
            first=commit_future_observation_shard(root, [_row()], contract=contract)
            self.assertEqual(commit_future_observation_shard(root, [_row()], contract=contract)["status"], "already_committed")
            second=_row("2026-09-04"); second["entry_date"]="2026-09-05"; second["exit_date"]="2026-09-09"
            self.assertEqual(commit_future_observation_shard(root, [second], contract=contract)["status"], "committed")
            duplicate=_row(); duplicate["net_return"]=0.02; duplicate["excess_return"]=0.018
            with self.assertRaisesRegex(ValueError, "cross-shard"):
                commit_future_observation_shard(root, [duplicate], contract=contract)
            shard=Path(first["path"]); payload=json.loads(shard.read_text(encoding="utf-8")); payload["rows"][0]["net_return"]=9; shard.write_text(json.dumps(payload),encoding="utf-8")
            newer=_row("2026-09-05"); newer["entry_date"]="2026-09-08"; newer["exit_date"]="2026-09-10"
            with self.assertRaisesRegex(ValueError, "tampered"):
                commit_future_observation_shard(root, [newer], contract=contract)

    def test_boundaries_schema_and_arithmetic_fail_closed(self):
        with TemporaryDirectory() as temp:
            contract=_contract()
            with self.assertRaises(ValueError): commit_future_observation_shard(Path(temp), [_row("2026-09-01")], contract=contract)
            with self.assertRaises(ValueError): commit_future_observation_shard(Path(temp), [_row("2026-10-01")], contract=contract)
            crosses_lockbox=_row("2026-09-29"); crosses_lockbox["entry_date"]="2026-10-01"; crosses_lockbox["exit_date"]="2026-10-01"
            with self.assertRaises(ValueError): commit_future_observation_shard(Path(temp), [crosses_lockbox], contract=contract)
            bad=_row(); bad["excess_return"]=0.1
            with self.assertRaises(ValueError): commit_future_observation_shard(Path(temp), [bad], contract=contract)
            bad_cost=_row(); bad_cost["cost_round_trip"]=0.0
            with self.assertRaises(ValueError): commit_future_observation_shard(Path(temp), [bad_cost], contract=contract)

    def test_tail_rows_and_lock_or_sidecar_tampering_fail_closed(self):
        with TemporaryDirectory() as temp:
            root=Path(temp); contract=_contract(); tail={"candidate_semantic_id": "sha256:" + "b" * 64, "symbol": "000002", "signal_date": "2026-09-04", "horizon": 10, "status": "tail_purged"}
            result=commit_future_observation_shard(root, [_row(), tail], contract=contract)
            self.assertEqual(result["status"], "committed")
            sidecar=root / "commits" / (result["shard_hash"][7:] + ".json")
            payload=json.loads(sidecar.read_text(encoding="utf-8")); payload["keys"]=[]; sidecar.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "keys differ"):
                commit_future_observation_shard(root, [_row(), tail], contract=contract)
        with TemporaryDirectory() as temp:
            root=Path(temp); (root / ".observation-commit.lock").mkdir()
            with self.assertRaisesRegex(ValueError, "lock exists"):
                commit_future_observation_shard(root, [_row()], contract=_contract())


class StatisticsTests(unittest.TestCase):
    def test_early_and_missing_portfolio_are_not_adjudication(self):
        early=summarize_future_evidence([], expected_candidates=["x"], as_of=date(2026,9,2), research_end=date(2026,9,30))
        self.assertEqual(early["status"], "accumulating_not_adjudicable")
        events=[]
        for day in ("2026-09-03", "2026-09-04"):
            for _ in range(50): events.append({"candidate_semantic_id":"x","horizon":5,"signal_date":day,"excess_return":0.01,"status":"completed"})
        final=summarize_future_evidence(events, expected_candidates=["x","y"], as_of=date(2026,9,30), research_end=date(2026,9,30))
        self.assertEqual(final["status"], "adjudicable_event_only_portfolio_confirmation_missing")
        self.assertEqual(len(final["statistics"]), 6)
        self.assertEqual(final["winner_selection"], "forbidden")
        x5=next(item for item in final["statistics"] if item["candidate"]=="x" and item["horizon"]==5)
        self.assertAlmostEqual(x5["cost_stress_2x_mean_excess"], 0.01)

    def test_same_day_cross_section_fdr_family_and_two_x_cost_are_complete(self):
        events=[]
        # 100 rows but only two signal days: HAC must receive two daily
        # cross-sectional means, never the 100 overlapping stock events.
        for day, value in (("2026-09-03", 0.01), ("2026-09-04", 0.03)):
            for number in range(50):
                events.append({"candidate_semantic_id": "x", "horizon": 5, "signal_date": day, "excess_return": value + number * 0.0, "cost_round_trip": 0.0016, "status": "completed"})
        captured=[]
        def fake_hac(values, lag):
            values=list(values); captured.append((values, lag))
            return {"effect": float(np.mean(values)) if values else None, "raw_p_value": 0.01, "n_dates": len(values), "lag": lag, "evidence_status": "descriptive_hac"}
        with patch("packages.research.gen2_validation.hac_mean", side_effect=fake_hac):
            result=summarize_future_evidence(events, expected_candidates=["x", "y"], as_of=date(2026,9,30), research_end=date(2026,9,30))
        self.assertEqual(len(result["statistics"]), 6)  # expected candidates x 3 horizons, including empty cells
        self.assertIn(([0.01, 0.03], 5), captured)
        x5=next(item for item in result["statistics"] if item["candidate"] == "x" and item["horizon"] == 5)
        self.assertAlmostEqual(x5["effect"], 0.02)
        self.assertAlmostEqual(x5["cost_stress_2x_mean_excess"], 0.0184)
        self.assertTrue(all("adjusted_p_value" in item and "fdr_reject" in item for item in result["statistics"]))


class PortfolioTests(unittest.TestCase):
    def test_equal_weight_overlap_and_unresolved_are_fail_closed(self):
        asset, _ = _frames(); asset=asset.iloc[1:]; contract=_contract(); contract["execution"].update({"max_positions": 1, "max_exit_delay_bars": 0, "same_symbol_overlap": "forbidden", "portfolio_weighting": "equal_weight"})
        plans=[{"candidate_semantic_id": "sha256:" + "b" * 64, "symbol": "000001", "signal_date": "2026-09-03", "horizon": 5}, {"candidate_semantic_id": "sha256:" + "b" * 64, "symbol": "000001", "signal_date": "2026-09-04", "horizon": 5}]
        result=evaluate_portfolio_confirmation(plans, {"000001": asset}, calendar=asset.index, contract=contract)
        ledger=result["ledgers"]["sha256:" + "b" * 64]["5"]
        self.assertEqual(result["status"], "completed")
        self.assertGreaterEqual(ledger["rejected_counts"].get("same_symbol_overlap_forbidden", 0), 1)
        evidence=summarize_future_evidence([], expected_candidates=contract["candidate_semantic_ids"], as_of=date(2026,9,30), research_end=date(2026,9,30), contract=contract, portfolio_confirmation=result)
        self.assertEqual(evidence["status"], "adjudicable_with_portfolio_confirmation")
        forged={**result, "contract_hash": "other"}
        rejected=summarize_future_evidence([], expected_candidates=contract["candidate_semantic_ids"], as_of=date(2026,9,30), research_end=date(2026,9,30), contract=contract, portfolio_confirmation=forged)
        self.assertEqual(rejected["status"], "portfolio_failed_closed")
        halted=asset.copy(); halted["suspended"]=False; halted.loc[asset.index[6], "suspended"] = True
        blocked=evaluate_portfolio_confirmation(plans[:1], {"000001": halted}, calendar=asset.index, contract=contract)
        self.assertEqual(blocked["status"], "failed_closed")
        with self.assertRaisesRegex(ValueError, "duplicate portfolio"):
            evaluate_portfolio_confirmation([plans[0], plans[0]], {"000001": asset}, calendar=asset.index, contract=contract)
        outside=asset.copy(); outside.loc[pd.Timestamp("2026-10-01")] = outside.iloc[-1]
        with self.assertRaisesRegex(ValueError, "crosses"):
            evaluate_portfolio_confirmation(plans[:1], {"000001": outside}, calendar=asset.index, contract=contract)


class Gen2ValidationCliTests(unittest.TestCase):
    def test_synthetic_smoke_writes_no_data_and_has_no_market_mode(self):
        root=Path(__file__).resolve().parents[1]
        result=subprocess.run([sys.executable, "scripts/run_gen2_validation.py", "synthetic-smoke"], cwd=root, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload=json.loads(result.stdout); self.assertFalse(payload["writes_data"]); self.assertFalse(payload["market_data_read"])
        help_result=subprocess.run([sys.executable, "scripts/run_gen2_validation.py", "--help"], cwd=root, capture_output=True, text=True, check=False)
        self.assertNotIn("market-root", help_result.stdout)


if __name__ == "__main__": unittest.main()
