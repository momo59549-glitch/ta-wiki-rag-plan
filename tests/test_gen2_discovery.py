from __future__ import annotations

from dataclasses import asdict
from copy import deepcopy
from datetime import date
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import sys
import unittest

import numpy as np
import pandas as pd

from packages.contracts import RuleDefinition
from packages.research.gen2_discovery import (
    Gen2Config,
    Gen2Periods,
    apply_context_filters,
    build_gen2_protocol,
    canonical_hash,
    historical_trial_inventory,
    gen2_candidate_semantic_id,
    initialize_global_trial_ledger,
    load_gen1_candidate_references,
    load_global_trial_ledger,
    preregister_gen2_generation,
    verify_parent_generation_closure,
    verify_gen1_protocol,
    verify_gen2_protocol,
)
from packages.research.auto_discovery import discovery_semantic_hash
from packages.rule_dsl import compile_rule, rule_logic_hash
from _gen2_closure_fixture import closure_fixture


def _parent_protocol(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    definition = RuleDefinition(
        id="legacy_rsi", version="1", name_zh="旧 RSI",
        expression={"lt": [{"metric": {"name": "rsi", "window": 14, "offset": 0}}, {"param": "threshold"}]},
        parameters={"threshold": 30.0},
    )
    payload = {
        "schema_version": "auto-discovery-protocol/v1",
        "status": "preregistered",
        "generation": {"generation_id": "g_20260809_01", "candidate_budget": 1},
        "periods": {"research_start": "2020-01-01", "validation_start": "2022-01-01", "research_end": "2026-08-04", "final_lockbox_start": "2026-09-01"},
        "candidate_space": {"candidates": [{"definition": asdict(definition), "rule_semantic_hash": compile_rule(definition).semantic_hash, "discovery_semantic_hash": discovery_semantic_hash(definition)}]},
    }
    payload["protocol_hash"] = canonical_hash(payload)
    payload["auto_discovery_protocol_id"] = "auto_discovery_" + payload["protocol_hash"].removeprefix("sha256:")[:24]
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _ledger(root: Path, budget: int = 80) -> dict:
    initialize_global_trial_ledger(root, global_trial_budget=budget, legacy_trial_count=64, legacy_inventory_hash="sha256:" + "c" * 64)
    return load_global_trial_ledger(root)


def _protocol(root: Path, *, generation: str = "g2", budget: int = 4) -> dict:
    parent = _parent_protocol(root / "parent.json")
    _, closure = closure_fixture(root)
    config = Gen2Config(generation, "g_20260809_01", verify_gen1_protocol(parent)["protocol_hash"], budget, seed=17)
    return build_gen2_protocol(
        config,
        Gen2Periods(date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 30), date(2026, 10, 1)),
        parent_research_end=date(2026, 8, 4),
        global_ledger=_ledger(root / "ledger"),
        parent_closure=closure,
        gen1_references=load_gen1_candidate_references(parent),
    )


class Gen2ProtocolTests(unittest.TestCase):
    def test_real_parent_closure_and_tampering_are_fail_closed(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); result_path, closure = closure_fixture(root)
            self.assertEqual(verify_parent_generation_closure(result_path)["result_hash"], closure["result_hash"])
            payload = json.loads(result_path.read_text(encoding="utf-8")); payload["result_hash"] = "sha256:" + "0" * 64; result_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "result hash"):
                verify_parent_generation_closure(result_path)
        with TemporaryDirectory() as temp:
            root = Path(temp); result_path, _ = closure_fixture(root); protocol = result_path.with_name("comparison_protocol.json")
            payload = json.loads(protocol.read_text(encoding="utf-8")); payload["comparison_id"] = "tampered"; protocol.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "protocol"):
                verify_parent_generation_closure(result_path)
        with TemporaryDirectory() as temp:
            root = Path(temp); result_path, _ = closure_fixture(root); payload = json.loads(result_path.read_text(encoding="utf-8")); payload["ranking"][0]["status"] = "research_survivor"; identity = {key: value for key, value in payload.items() if key not in {"completed_at", "result_hash"}}; payload["result_hash"] = canonical_hash(identity); result_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "survivor"):
                verify_parent_generation_closure(result_path)
        with TemporaryDirectory() as temp:
            root = Path(temp); result_path, _ = closure_fixture(root); payload = json.loads(result_path.read_text(encoding="utf-8")); payload["ranking"][0]["candidate"] = "substituted"; identity = {key: value for key, value in payload.items() if key not in {"completed_at", "result_hash"}}; payload["result_hash"] = canonical_hash(identity); result_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "candidate mismatch"):
                verify_parent_generation_closure(result_path)
        with TemporaryDirectory() as temp:
            root = Path(temp); result_path, _ = closure_fixture(root); protocol = result_path.with_name("comparison_protocol.json"); payload = json.loads(protocol.read_text(encoding="utf-8")); payload["oos"]["lockbox_start"] = "2026-08-01"; protocol.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "protocol"):
                verify_parent_generation_closure(result_path)

    def test_protocol_freezes_future_boundary_context_wrapper_and_gen1_reference(self):
        with TemporaryDirectory() as temp:
            protocol = _protocol(Path(temp))
            self.assertTrue(protocol["periods"]["planned_fresh_oos"])
            self.assertFalse(protocol["periods"]["final_lockbox_read"])
            self.assertEqual(protocol["governance"]["2022_2026_fresh_oos"], "forbidden")
            self.assertTrue(all(item["composition"] == "base_rule AND every_context_filter" for item in protocol["candidate_space"]["candidates"]))
            self.assertTrue(all(len(item["context_filters"]) == 4 for item in protocol["candidate_space"]["candidates"]))
            rsi = next(item for item in protocol["candidate_space"]["candidates"] if item["base_definition"]["id"] == "gen2_rsi_oversold")
            self.assertTrue(rsi["cross_generation_deduplication"]["base_logic_previously_tested_in_gen1"])
            self.assertEqual(rsi["cross_generation_deduplication"]["decision"], "new_contextual_composite")

    def test_refuses_seen_oos_and_global_budget_overrun(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            parent = _parent_protocol(root / "parent.json")
            _, closure = closure_fixture(root)
            config = Gen2Config("g2", "g_20260809_01", verify_gen1_protocol(parent)["protocol_hash"], 4)
            with self.assertRaisesRegex(ValueError, "2022–2026"):
                build_gen2_protocol(
                    config, Gen2Periods(date(2022, 1, 1), date(2022, 2, 1), date(2023, 1, 1), date(2027, 1, 1)),
                    parent_research_end=date(2026, 8, 4), global_ledger=_ledger(root / "ledger"),
                    parent_closure=closure,
                    gen1_references=load_gen1_candidate_references(parent),
                )

    def test_refuses_parent_reference_hash_mismatch(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); parent = _parent_protocol(root / "parent.json")
            _, closure = closure_fixture(root)
            config = Gen2Config("g2", "g_20260809_01", "sha256:" + "a" * 64, 4)
            with self.assertRaisesRegex(ValueError, "parent generation/protocol hash"):
                build_gen2_protocol(config, Gen2Periods(date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 30), date(2026, 10, 1)), parent_research_end=date(2026, 8, 4), global_ledger=_ledger(root / "ledger"), parent_closure=closure, gen1_references=load_gen1_candidate_references(parent))
            with self.assertRaisesRegex(ValueError, "全局 trial budget 不足"):
                valid_config = Gen2Config("g2_budget", "g_20260809_01", verify_gen1_protocol(parent)["protocol_hash"], 4)
                build_gen2_protocol(
                    valid_config, Gen2Periods(date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 30), date(2026, 10, 1)),
                    parent_research_end=date(2026, 8, 4), global_ledger=_ledger(root / "small", budget=65), parent_closure=closure,
                    gen1_references=load_gen1_candidate_references(parent),
                )

    def test_semantic_id_ignores_context_order_and_base_display_identity(self):
        left = RuleDefinition("a", "1", "甲", {"lt": [{"metric": {"name": "rsi", "window": 14, "offset": 0}}, {"param": "x"}]}, {"x": 30.0})
        right = RuleDefinition("b", "9", "乙", {"lt": [{"metric": {"name": "rsi", "window": 14, "offset": 0}}, {"param": "threshold"}]}, {"threshold": 30})
        filters = [
            {"kind": "market_regime", "state": "above_sma", "window": 20, "observed_at": "signal_bar_close"},
            {"kind": "relative_strength", "window": 5, "operator": "gte", "threshold": 0.0, "benchmark_symbol": "000300"},
        ]
        self.assertEqual(rule_logic_hash(left), rule_logic_hash(right))
        self.assertEqual(gen2_candidate_semantic_id(left, filters, "000300"), gen2_candidate_semantic_id(right, list(reversed(filters)), "000300"))

    def test_write_once_append_only_ledger(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            protocol = _protocol(root)
            ledger_root = root / "ledger"
            closure_path = root / "parent_closure" / "comparison_result.json"
            result = preregister_gen2_generation(protocol, output_root=root / "output", ledger_root=ledger_root, parent_protocol_path=root / "parent.json", parent_closure_result_path=closure_path)
            self.assertTrue(Path(result["ledger_entry"]).is_file())
            state = load_global_trial_ledger(ledger_root)
            self.assertEqual(state["used_trial_count"], 68)
            with self.assertRaises(FileExistsError):
                preregister_gen2_generation(protocol, output_root=root / "output", ledger_root=ledger_root, parent_protocol_path=root / "parent.json", parent_closure_result_path=closure_path)

    def test_registered_entry_binds_exact_artifact_protocol_and_periods(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); protocol = _protocol(root); ledger_root = root / "ledger"; closure_path = root / "parent_closure" / "comparison_result.json"
            preregister_gen2_generation(protocol, output_root=root / "output", ledger_root=ledger_root, parent_protocol_path=root / "parent.json", parent_closure_result_path=closure_path)
            ledger = load_global_trial_ledger(ledger_root)
            verify_gen2_protocol(protocol, ledger=ledger, parent_protocol_path=root / "parent.json", parent_closure_result_path=closure_path)
            for field, value in (("preregistered_at", "2026-01-01T00:00:00+00:00"), ("periods", {**protocol["periods"], "research_end": "2027-08-30"})):
                altered = deepcopy(protocol); altered[field] = value
                identity = {key: item for key, item in altered.items() if key not in {"protocol_hash", "protocol_id", "created_at"}}; altered["protocol_hash"] = canonical_hash(identity); altered["protocol_id"] = "gen2_" + altered["protocol_hash"][7:31]
                with self.assertRaises(ValueError): verify_gen2_protocol(altered, ledger=ledger, parent_protocol_path=root / "parent.json", parent_closure_result_path=closure_path)
            artifact = root / "output" / "gen2_protocol.json"; payload=json.loads(artifact.read_text(encoding="utf-8")); payload["generation"]["candidate_budget"] = 99; artifact.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "artifacts differ"):
                verify_gen2_protocol(protocol, ledger=ledger, parent_protocol_path=root / "parent.json", parent_closure_result_path=closure_path)

    def test_ledger_refuses_duplicate_semantics_even_if_protocol_is_manually_reused(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            first = _protocol(root, generation="g2a")
            ledger_root = root / "ledger"
            closure_path = root / "parent_closure" / "comparison_result.json"
            preregister_gen2_generation(first, output_root=root / "output-a", ledger_root=ledger_root, parent_protocol_path=root / "parent.json", parent_closure_result_path=closure_path)
            second = _protocol(root / "second", generation="g2b")
            # A caller must not bypass build-time exclusion by copying a
            # previously registered candidate space into a different protocol.
            second["candidate_space"] = first["candidate_space"]
            second["generation"]["candidate_budget"] = first["generation"]["candidate_budget"]
            with self.assertRaisesRegex(ValueError, "未绑定|semantic id 已在"):
                preregister_gen2_generation(second, output_root=root / "output-b", ledger_root=ledger_root, parent_protocol_path=root / "second" / "parent.json", parent_closure_result_path=root / "second" / "parent_closure" / "comparison_result.json")


class ContextFilterTests(unittest.TestCase):
    def _frames(self) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
        index = pd.date_range("2026-01-01", periods=90, freq="B")
        asset = pd.DataFrame({"close": 100 * (1.012 ** np.arange(len(index))), "volume": np.full(len(index), 100.0)}, index=index)
        benchmark = pd.DataFrame({"close": 100 * (1.004 ** np.arange(len(index)))}, index=index)
        return asset, benchmark, pd.Series(True, index=index)

    def test_relative_strength_requires_exact_complete_benchmark_window(self):
        asset, benchmark, signal = self._frames()
        filters = [{"kind": "relative_strength", "window": 5, "operator": "gte", "threshold": 0.0, "benchmark_symbol": "000300"}]
        accepted = apply_context_filters(asset, benchmark, signal, filters, benchmark_symbol="000300")
        self.assertTrue(accepted.iloc[-1])
        missing_date = asset.index[-3]
        missing = benchmark.drop(index=missing_date)
        blocked = apply_context_filters(asset, missing, signal, filters, benchmark_symbol="000300")
        self.assertFalse(blocked.loc[missing_date])
        self.assertFalse(blocked.iloc[-1])  # missing date remains inside its 5-day return window

    def test_context_filters_do_not_read_future_bars(self):
        asset, benchmark, signal = self._frames()
        filters = [
            {"kind": "market_regime", "state": "above_sma", "window": 10, "observed_at": "signal_bar_close"},
            {"kind": "relative_strength", "window": 5, "operator": "gte", "threshold": 0.0, "benchmark_symbol": "000300"},
            {"kind": "realized_volatility", "window": 5, "operator": "lte", "threshold": 0.02},
            {"kind": "volume_category", "window": 5, "operator": "gte", "multiple": 1.0},
        ]
        baseline = apply_context_filters(asset, benchmark, signal, filters, benchmark_symbol="000300")
        changed_asset, changed_benchmark = asset.copy(), benchmark.copy()
        changed_asset.iloc[-1, changed_asset.columns.get_loc("close")] *= 0.1
        changed_benchmark.iloc[-1, changed_benchmark.columns.get_loc("close")] *= 3.0
        changed = apply_context_filters(changed_asset, changed_benchmark, signal, filters, benchmark_symbol="000300")
        pd.testing.assert_series_equal(baseline.iloc[:-1], changed.iloc[:-1])

    def test_rejects_ignored_filter_fields_and_invalid_signal_or_prices(self):
        asset, benchmark, signal = self._frames()
        good = {"kind": "relative_strength", "window": 5, "operator": "gte", "threshold": 0.0, "benchmark_symbol": "000300"}
        for invalid in (
            {**good, "ignored": "bypass"},
            {key: value for key, value in good.items() if key != "threshold"},
            {**good, "threshold": float("nan")},
            {**good, "threshold": float("inf")},
        ):
            with self.assertRaises(ValueError):
                apply_context_filters(asset, benchmark, signal, [invalid], benchmark_symbol="000300")
        with self.assertRaises(ValueError):
            apply_context_filters(asset, benchmark, pd.Series([1] * len(asset), index=asset.index), [good], benchmark_symbol="000300")
        bad_asset = asset.copy(); bad_asset.iloc[-1, bad_asset.columns.get_loc("close")] = 0.0
        with self.assertRaises(ValueError):
            apply_context_filters(bad_asset, benchmark, signal, [good], benchmark_symbol="000300")
        nullable_signal = signal.astype("boolean")
        nullable_signal.iloc[-1] = pd.NA
        result = apply_context_filters(asset, benchmark, nullable_signal, [good], benchmark_symbol="000300")
        self.assertFalse(result.iloc[-1])


class Gen1VerificationTests(unittest.TestCase):
    def test_tampered_parent_hash_or_candidate_is_rejected(self):
        with TemporaryDirectory() as temp:
            path = _parent_protocol(Path(temp) / "parent.json")
            verify_gen1_protocol(path)
            payload = json.loads(path.read_text(encoding="utf-8")); payload["periods"]["research_end"] = "2026-08-05"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "protocol_hash"):
                verify_gen1_protocol(path)


class Gen2CliTests(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            parent = _parent_protocol(root / "parent.json")
            closure = Path(__file__).resolve().parents[1] / "data" / "candidate_comparisons" / "g_20260809_01" / "comparison_result.json"
            command = [
                sys.executable, "scripts/run_gen2_discovery.py", "--generation-id", "g2_cli", "--parent-protocol", str(parent),
                "--parent-closure-result", str(closure),
                "--research-start", "2026-09-01", "--validation-start", "2026-09-02", "--research-end", "2026-09-30", "--lockbox-start", "2026-10-01",
                "--candidate-budget", "4", "--global-trial-budget", "1000", "--ledger-root", str(root / "ledger"), "--dry-run",
            ]
            result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertTrue(json.loads(result.stdout)["dry_run"])
            self.assertFalse((root / "ledger").exists())


if __name__ == "__main__":
    unittest.main()
