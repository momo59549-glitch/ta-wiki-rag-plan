import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import inspect
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from packages.research.candidate_comparison import (
    FIXED_RULES, PUBLICATION, apply_fdr_bh, apply_trading_bar_cooldown, build_comparison_protocol,
    hac_mean, pairwise_overlap, run_comparison, validate_completed_case, verify_comparison_protocol,
    _apply_portfolio_fdr, _calendar_for_events, _finalize_ranking, _staged_portfolio, _staged_statistics,
)
from packages.research.comparison_panel import PANEL_SCHEMA, REQUIRED_FIELDS
from packages.research.comparison_panel import ShardedPanel, build_comparison_panel
from packages.research.comparison_staging import build_compact_staging, iter_candidate_events, verify_staging
from packages.contracts import Candle, RuleDefinition
from unittest.mock import patch
from packages.research.json_store import write_json
from packages.research.pipeline import PipelineConfig
from packages.research.protocol import build_experiment_protocol
from packages.research.promotion import build_frozen_campaign_rule
from packages.research.readiness import build_code_snapshot
from packages.research.run_artifacts import build_checkpoint, canonical_hash, file_hash, write_batch
from packages.rule_dsl import compile_rule


def _fixture_snapshot_id(dataset: str) -> str:
    return canonical_hash({"schema_version": "dataset-snapshot/v1", "mode": "strong_content_sha256", "dataset": dataset, "symbols": ["000001", "000002"], "files": []})


def _panel(directory: Path, *, future=False, dataset="ignored-builder-value", project_root=None):
    by_symbol = {}
    for symbol in ("000001", "000002"):
        rows = []
        for index in range(61):
            day = f"2020-03-{index + 1:02d}" if index < 31 else f"2020-04-{index - 30:02d}" if index < 61 else f"2020-05-{index - 60:02d}"
            rows.append({"symbol": symbol, "date": day, "open": 10 + index / 100, "close": 10.05 + index / 100,
                         "prev_close": 10 if index == 0 else 10.05 + (index - 1) / 100, "volume": 1000, "amount": 10000, "is_st": False, "tradeable_open": True, "tradeable_close": True,
                         "open_reason_codes": [], "close_reason_codes": []})
        by_symbol[symbol] = rows
    if future:
        by_symbol["000001"].append({"symbol": "000001", "date": "2021-01-01", "open": 1, "close": 1, "prev_close": by_symbol["000001"][-1]["close"], "volume": 1, "amount": 1, "is_st": False,
                                    "tradeable_open": True, "tradeable_close": True, "open_reason_codes": [], "close_reason_codes": []})
    shards = []
    for symbol, rows in by_symbol.items():
        path = directory / "symbols" / f"{symbol}.jsonl"; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        shards.append({"symbol": symbol, "path": f"symbols/{symbol}.jsonl", "rows": len(rows), "first_date": rows[0]["date"], "last_date": rows[-1]["date"],
                       "skipped_initial_rows": {"count": 0, "dates": [], "reason": None, "close_references": []}, "sha256": file_hash(path)})
    builder_snapshot_path = directory / "builder_code_snapshot.json"
    builder_snapshot = build_code_snapshot(Path(project_root) if project_root else Path(__file__).resolve().parents[1], builder_snapshot_path)
    identity = {"schema_version": PANEL_SCHEMA, "builder": "packages.research.comparison_panel.build_comparison_panel",
                "builder_code_snapshot_id": builder_snapshot["code_snapshot_id"], "builder_code_snapshot": {"path": "builder_code_snapshot.json", "sha256": file_hash(builder_snapshot_path)},
                "dataset_snapshot_id": _fixture_snapshot_id(dataset),
                "source_snapshot_fingerprint": "sha256:fixture",
                "source_root": "fixture", "source_dataset": "fixture", "oos": {"start": "2020-03-01", "end": "2020-04-30", "lockbox_start": "2021-01-01"},
                "symbols": ["000001", "000002"], "required_fields": list(REQUIRED_FIELDS), "execution_policy": {"fixture": True},
                "skipped_initial_rows": {"count": 0, "rows": []}, "shards": shards}
    manifest = {**identity, "panel_id": canonical_hash(identity)}
    manifest_path = directory / "panel_manifest.json"; write_json(manifest_path, manifest)
    return manifest_path, by_symbol


def _reseal_panel(manifest_path: Path, by_symbol):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for item in manifest["shards"]:
        rows = by_symbol[item["symbol"]]; path = manifest_path.parent / item["path"]
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        item.update({"rows": len(rows), "first_date": rows[0]["date"] if rows else None, "last_date": rows[-1]["date"] if rows else None, "sha256": file_hash(path)})
    identity = {key: manifest[key] for key in ("schema_version", "builder", "builder_code_snapshot_id", "builder_code_snapshot", "dataset_snapshot_id", "source_snapshot_fingerprint", "source_root", "source_dataset", "oos", "symbols", "required_fields", "execution_policy", "skipped_initial_rows", "shards")}
    manifest["panel_id"] = canonical_hash(identity); write_json(manifest_path, manifest)


def _ledger_fixture(root: Path, rows_by_symbol, signals, *, horizon=2, delay=1, max_positions=1, seed=11, cost_bps=0, cost_multiplier=1.0):
    class Panel:
        def load_symbol(self, symbol):
            rows = rows_by_symbol[symbol]
            return rows, {row["date"]: index for index, row in enumerate(rows)}
    plans = []
    for symbol, signal_index in signals:
        rows = rows_by_symbol[symbol]
        plans.append({"candidate": "x", "horizon": horizon, "symbol": symbol, "signal_date": rows[signal_index]["date"],
                      "entry_date": rows[signal_index + 1]["date"], "entry_open": rows[signal_index + 1]["open"],
                      "entry_tradeable": rows[signal_index + 1]["tradeable_open"], "entry_reason_codes": [],
                      "scheduled_exit_date": rows[signal_index + horizon]["date"], "deadline_date": rows[signal_index + horizon + delay]["date"],
                      "selection_rank": __import__("hashlib").sha256(f"{seed}|x|{symbol}|{rows[signal_index]['date']}".encode()).hexdigest()})
    shards = []
    for day, items in __import__("itertools").groupby(sorted(plans, key=lambda item: item["entry_date"]), key=lambda item: item["entry_date"]):
        path = root / "plans" / "x" / str(horizon) / f"{day}.jsonl"; path.parent.mkdir(parents=True, exist_ok=True)
        values = list(items); path.write_text("\n".join(json.dumps(item) for item in values) + "\n", encoding="utf-8")
        shards.append({"candidate": "x", "horizon": horizon, "entry_date": day, "path": path.relative_to(root).as_posix()})
    staging = {"plan_shards": shards, "candidates": [{"candidate": "x", "tail_purged_counts": {str(horizon): 0}, "tail_purged_samples": {str(horizon): []}}]}
    protocol = {"execution": {"audit_sample_limit": 10, "max_positions": max_positions, "base_cost_bps_per_side": {"commission": cost_bps, "slippage": 0}}}
    dates = sorted({row["date"] for rows in rows_by_symbol.values() for row in rows})
    return _staged_portfolio(root, staging, Panel(), protocol, "x", horizon, dates, cost_multiplier=cost_multiplier)


def _make_case(root: Path, name: str, dataset_id: str, offset: int = 0) -> Path:
    case_id = f"case_{name}"
    case_dir = root / name / case_id
    run_dir = case_dir / "research_run" / "run_1"
    receipt_names = {"rsi": "rsi7_lt25.json", "roc": "roc5_lt_minus5pct.json", "breakdown": "breakdown_low20.json"}
    receipt_source = Path(__file__).resolve().parents[1] / "data" / "auto_discovery" / "g_20260809_01" / "promotion_receipts" / receipt_names[name]
    receipt = json.loads(receipt_source.read_text(encoding="utf-8"))
    rule = compile_rule(RuleDefinition(**receipt["selected_definition"]))
    config = PipelineConfig(horizons=(5, 10, 20), start=__import__("datetime").date(2020, 1, 1), end=__import__("datetime").date(2020, 4, 30),
                            out_of_sample_start=__import__("datetime").date(2020, 3, 1), universe_manifest="u.jsonl", lockbox_start=__import__("datetime").date(2021, 1, 1))
    dataset_identity = {"schema_version": "dataset-snapshot/v1", "mode": "strong_content_sha256", "dataset": dataset_id, "symbols": ["000001", "000002"], "files": []}
    snapshot_id = canonical_hash(dataset_identity)
    code_identity = {"schema_version": "code-snapshot/v1", "files": []}
    import hashlib
    code_snapshot_id = "sha256:" + hashlib.sha256(json.dumps(code_identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    protocol = build_experiment_protocol(rule, config, ["000001", "000002"], snapshot_id, case_dir / "experiment_protocol.json",
                                         minimum_oos_observations=1, code_snapshot_id=code_snapshot_id)
    campaign_dir = root / name / "campaign"
    write_json(campaign_dir / "promotion_receipt.json", receipt)
    write_json(campaign_dir / "frozen_rule_definition.json", build_frozen_campaign_rule(receipt))
    write_json(campaign_dir / "experiment_protocol.json", protocol)
    write_json(campaign_dir / "dataset_snapshot.json", {**dataset_identity, "dataset_snapshot_id": snapshot_id, "roots": {}})
    write_json(campaign_dir / "code_snapshot.json", {**code_identity, "code_snapshot_id": code_snapshot_id})
    dates = [f"2020-03-{3 + offset:02d}", f"2020-03-{24 + offset:02d}"]
    observations = []
    outcomes = []
    for number, day in enumerate(dates):
        oid = f"obs_{name}_{number}"
        observations.append({"id": oid, "symbol": "000001", "observed_at": day + "T00:00:00", "executable_at": day + "T00:00:00"})
        for horizon in (5, 10, 20):
            outcomes.append({"observation_id": oid, "horizon_bars": horizon, "sample_split": "out_of_sample", "market_regime": "bearish",
                             "net_excess_return": 0.02 + horizon / 10000, "net_return": 0.02, "raw_return": 0.021,
                             "benchmark_return": 0.0, "entry_executable": True, "exit_executable": True})
    identity = {"case_id": case_id, "run_id": "run_1", "dataset_snapshot_id": snapshot_id, "experiment_protocol_id": protocol["protocol_id"], "candidate": name}
    commit = write_batch(run_dir, 0, ["000001", "000002"], observations, outcomes, canonical_hash(identity), loaded_symbols=2, skipped_symbols=[])
    write_json(run_dir / "checkpoint.json", build_checkpoint(identity, [commit], status="completed", started_at="2020-01-01T00:00:00+00:00"))
    write_json(run_dir / "artifact_manifest.json", {"schema_version": "research-artifact-manifest/v1", "execution_identity": identity,
               "execution_identity_hash": canonical_hash(identity), "committed_batches": 1, "observations": len(observations), "outcomes": len(outcomes), "commit_hashes": [commit["commit_hash"]]})
    write_json(run_dir / "run.json", {"run_id": "run_1", "dataset_snapshot_id": snapshot_id, "observations": len(observations), "outcomes": len(outcomes)})
    write_json(run_dir / "progress.json", {"status": "completed"})
    write_json(case_dir / "dataset_snapshot_manifest.json", {**dataset_identity, "dataset_snapshot_id": canonical_hash(dataset_identity), "roots": {}})
    write_json(case_dir / "code_snapshot.json", {**code_identity, "code_snapshot_id": code_snapshot_id})
    write_json(case_dir / "qa_review.json", {"status": "passed"})
    write_json(case_dir / "case.json", {"case_id": case_id, "qa_status": "passed", "dataset_snapshot_id": snapshot_id, "research_run": "research_run/run_1",
               "rule": {"id": rule.definition.id, "version": rule.definition.version, "semantic_hash": rule.semantic_hash}})
    write_json(case_dir.parent / "execution_request.json", {"campaign": str(campaign_dir), "case_id": case_id, "protocol_id": protocol["protocol_id"],
               "dataset_snapshot_id": snapshot_id, "final_lockbox_consumed": False})
    return case_dir


class CandidateComparisonUnitTests(unittest.TestCase):
    def test_fixed_promoted_rule_identities_are_complete_and_distinct(self):
        self.assertEqual(set(FIXED_RULES), {"rsi", "roc", "breakdown"})
        self.assertEqual(len({item["semantic_hash"] for item in FIXED_RULES.values()}), 3)
        self.assertTrue(all(set(item) == {"semantic_hash", "logic_hash", "receipt_id", "receipt_hash"} for item in FIXED_RULES.values()))

    def test_cooldown_is_deduplicated_and_order_stable(self):
        calendar = {"A": {f"2020-01-{day:02d}": day - 1 for day in range(1, 32)}}
        events = [{"event_id": "b", "symbol": "A", "date": "2020-01-22"}, {"event_id": "a", "symbol": "A", "date": "2020-01-01"},
                  {"event_id": "dup", "symbol": "A", "date": "2020-01-01"}, {"event_id": "near", "symbol": "A", "date": "2020-01-20"}]
        expected = [("A", "2020-01-01"), ("A", "2020-01-22")]
        for ordering in (events, list(reversed(events))):
            self.assertEqual([(item["symbol"], item["date"]) for item in apply_trading_bar_cooldown(ordering, calendar, 20)], expected)

    def test_exact_and_proximity_overlap(self):
        calendar = {"A": {f"d{index}": index for index in range(20)}}
        result = pairwise_overlap([{"symbol": "A", "date": "d1"}, {"symbol": "A", "date": "d10"}],
                                  [{"symbol": "A", "date": "d1"}, {"symbol": "A", "date": "d14"}], calendar, 5)
        self.assertAlmostEqual(result["exact_jaccard"], 1 / 3)
        self.assertEqual(result["left_unique_fraction"], 0.5)
        self.assertEqual(result["proximity_left_covered"], 1.0)

    def test_hac_matches_hand_calculation_and_fdr_is_global(self):
        result = hac_mean([1.0, 2.0, 3.0, 4.0], lag=1)
        self.assertAlmostEqual(result["effect"], 2.5)
        self.assertAlmostEqual(result["standard_error"], 0.625)
        records = [{"raw_p_value": value} for value in (0.001, 0.01, 0.02, 0.2, 0.3, 0.4, 0.5, 0.8, 0.9)]
        apply_fdr_bh(records)
        self.assertEqual(len(records), 9)
        self.assertAlmostEqual(records[0]["adjusted_p_value"], 0.009)
        empty_family = [hac_mean([], lag=horizon) for horizon in (5, 10, 20)]
        apply_fdr_bh(empty_family)
        self.assertTrue(all(item["raw_p_value"] is None and item["adjusted_p_value"] == 1.0 and not item["fdr_reject"] for item in empty_family))

    def test_portfolio_uses_next_open_not_signal_close(self):
        with TemporaryDirectory() as temp:
            dates = [(datetime(2020, 3, 1) + timedelta(days=i)).date().isoformat() for i in range(8)]
            base = [{"date": day, "open": 10 + i, "close": 1000 if i == 0 else 10.5 + i, "tradeable_open": True, "tradeable_close": True} for i, day in enumerate(dates)]
            result = _ledger_fixture(Path(temp) / "base", {"A": base}, [("A", 0)], horizon=5, delay=2, max_positions=2, seed=7)
            trade = result["trade_audit_sample"][0]
            self.assertEqual(trade["entry_date"], dates[1])
            altered = [dict(row) for row in base]; altered[0]["close"] = -999999
            same = _ledger_fixture(Path(temp) / "altered", {"A": altered}, [("A", 0)], horizon=5, delay=2, max_positions=2, seed=7)
            self.assertEqual(result["portfolio_net_return"], same["portfolio_net_return"])
            delayed_rows = [dict(row) for row in base]; delayed_rows[5]["tradeable_close"] = False
            delayed = _ledger_fixture(Path(temp) / "delayed", {"A": delayed_rows}, [("A", 0)], horizon=5, delay=2, max_positions=2, seed=7)
            self.assertEqual(delayed["trade_audit_sample"][0]["exit_date"], dates[6])
            blocked_rows = [dict(row) for row in base]
            for index in (5, 6, 7): blocked_rows[index]["tradeable_close"] = False
            blocked = _ledger_fixture(Path(temp) / "blocked", {"A": blocked_rows}, [("A", 0)], horizon=5, delay=2, max_positions=2, seed=7)
            self.assertEqual(blocked["status"], "blocked_unresolved_exit"); self.assertIsNone(blocked["portfolio_net_return"])
            self.assertIsNone(blocked["hac"]["raw_p_value"]); self.assertEqual(blocked["hac"]["evidence_status"], "blocked_ledger")

    def test_2x_cost_is_run_through_the_portfolio_ledger(self):
        with TemporaryDirectory() as temp:
            dates = [f"2020-01-{day:02d}" for day in range(1, 8)]
            rows = {"A": [{"date": day, "open": 10, "close": 10.1 if index >= 2 else 10, "tradeable_open": True, "tradeable_close": True} for index, day in enumerate(dates)]}
            base = _ledger_fixture(Path(temp) / "base", rows, [("A", 0)], horizon=2, delay=1, max_positions=1, cost_bps=10, cost_multiplier=1.0)
            stressed = _ledger_fixture(Path(temp) / "stress", rows, [("A", 0)], horizon=2, delay=1, max_positions=1, cost_bps=10, cost_multiplier=2.0)
            self.assertEqual(base["cost_multiplier"], 1.0); self.assertEqual(stressed["cost_multiplier"], 2.0)
            self.assertLess(stressed["portfolio_net_return"], base["portfolio_net_return"])

    def test_daily_equity_reuses_slots_and_same_day_selection_is_deterministic(self):
        with TemporaryDirectory() as temp:
            dates = [(datetime(2020, 3, 1) + timedelta(days=i)).date().isoformat() for i in range(9)]
            rows = {symbol: [{"date": day, "open": 10, "close": 12 if i == 2 else 10, "tradeable_open": True, "tradeable_close": True} for i, day in enumerate(dates)] for symbol in ("A", "B", "C")}
            first = _ledger_fixture(Path(temp) / "first", rows, [("A", 0), ("B", 0)], horizon=2, delay=1, max_positions=1)
            second = _ledger_fixture(Path(temp) / "second", rows, [("B", 0), ("A", 0)], horizon=2, delay=1, max_positions=1)
            self.assertEqual(first["trade_audit_sample"][0]["symbol"], second["trade_audit_sample"][0]["symbol"])
            self.assertAlmostEqual(first["portfolio_net_return"], 0.2)
            reused = _ledger_fixture(Path(temp) / "reuse", rows, [("A", 0), ("C", 2)], horizon=2, delay=1, max_positions=1)
            self.assertEqual(reused["trades_count"], 2)
            self.assertLessEqual(reused["diagnostics"]["peak_plans"], 1); self.assertLessEqual(reused["diagnostics"]["peak_active_positions"], 1)
            idle = _ledger_fixture(Path(temp) / "idle", rows, [], horizon=2, delay=1, max_positions=1)
            self.assertEqual(idle["n_days"], len(dates)); self.assertEqual(idle["daily_returns"], [0.0] * len(dates))

    def test_portfolio_confirmation_fdr_cost_stress_and_overlap_order(self):
        names, horizons = ["rsi", "roc", "breakdown"], [5, 10, 20]
        event_rows = []
        for candidate in names:
            for horizon in horizons:
                lower = 0.5 if candidate == "rsi" else 0.1
                event_rows.append({"candidate": candidate, "horizon": horizon, "evidence_status": "descriptive_hac", "n_dates": 100,
                                   "confidence_interval": {"lower": lower, "upper": lower + 0.1}, "fdr_reject": True,
                                   "stress": {"2.0": {"confidence_interval": {"lower": lower, "upper": lower + 0.1}}}, "positive_year_fraction": 1.0})
        def ledgers():
            return {candidate: {str(horizon): {"status": "completed", "portfolio_net_return": 0.2,
                    "hac": {"effect": 0.01, "standard_error": 0.001, "confidence_interval": {"lower": 0.008 if candidate == "roc" else 0.002, "upper": 0.012},
                            "n_dates": 100, "lag": horizon, "raw_p_value": 0.0001, "evidence_status": "descriptive_hac"},
                    "cost_stress": {"2.0": {"status": "completed", "portfolio_net_return": 0.1}}} for horizon in horizons} for candidate in names}
        portfolio = ledgers(); family = _apply_portfolio_fdr(portfolio, names, horizons, 0.05)
        self.assertEqual(len(family), 9); self.assertTrue(all(item["fdr_reject"] for item in family))
        protocol = {"elimination": {"minimum_dates_per_horizon": 20, "minimum_positive_hac_ci_horizons": 2, "stress_multiplier_required": 2.0,
                    "minimum_positive_year_fraction": 0.5, "high_overlap_threshold": 0.6,
                    "portfolio_confirmation": {"minimum_completed_positive_fdr_ci_horizons": 2, "minimum_2x_positive_net_return_horizons": 2}}}
        portfolio["breakdown"]["5"]["cost_stress"]["2.0"]["portfolio_net_return"] = -0.1
        portfolio["breakdown"]["10"]["cost_stress"]["2.0"]["portfolio_net_return"] = -0.1
        ranking = _finalize_ranking(event_rows, [], [], portfolio, protocol, names, horizons)
        self.assertEqual(next(item for item in ranking if item["candidate"] == "breakdown")["status"], "research_eliminated_portfolio")
        portfolio = ledgers(); _apply_portfolio_fdr(portfolio, names, horizons, 0.05)
        for horizon in horizons: portfolio["rsi"][str(horizon)]["portfolio_net_return"] = -0.1
        for horizon in horizons:
            portfolio["breakdown"][str(horizon)]["status"] = "blocked_unresolved_exit"
            portfolio["breakdown"][str(horizon)]["hac"].update({"raw_p_value": None, "fdr_reject": False, "confidence_interval": None, "evidence_status": "blocked_ledger"})
        ranking = _finalize_ranking(event_rows, [], [], portfolio, protocol, names, horizons)
        self.assertEqual(next(item for item in ranking if item["candidate"] == "rsi")["status"], "research_eliminated_portfolio")
        self.assertEqual(next(item for item in ranking if item["candidate"] == "breakdown")["status"], "research_eliminated_portfolio")
        portfolio = ledgers(); _apply_portfolio_fdr(portfolio, names, horizons, 0.05)
        overlap = [{"left": "rsi", "right": "roc", "same_rule_family": True, "left_unique_fraction": 0.1, "right_unique_fraction": 0.1,
                    "proximity_left_covered": 0.9, "proximity_right_covered": 0.9, "exact_jaccard": 0.8}]
        ranking = _finalize_ranking(event_rows, [], overlap, portfolio, protocol, names, horizons)
        self.assertEqual(next(item for item in ranking if item["candidate"] == "roc")["status"], "research_survivor")
        self.assertEqual(next(item for item in ranking if item["candidate"] == "rsi")["status"], "research_eliminated_redundant_high_overlap")

    def test_panel_reader_is_symbol_sharded_not_whole_file_materialized(self):
        import packages.research.candidate_comparison as comparison_module
        import packages.research.comparison_panel as panel_module
        source = inspect.getsource(comparison_module)
        panel_source = inspect.getsource(panel_module.ShardedPanel.iter_symbol)
        self.assertNotIn("_load_panel", source)
        self.assertNotIn("_events_and_returns", source)
        self.assertNotIn("outcome_maps", source)
        self.assertNotIn("build_conservative_portfolio", source)
        self.assertNotIn("read_text", panel_source)
        self.assertIn("with path.open", panel_source)

    def test_many_shards_keep_only_event_date_indexes(self):
        class SpyPanel:
            def __init__(self): self.calls = 0; self.max_symbol_rows = 0
            def load_symbol(self, symbol):
                self.calls += 1
                rows = [{"date": f"d{i:04d}"} for i in range(1000)]
                self.max_symbol_rows = max(self.max_symbol_rows, len(rows))
                return rows, {row["date"]: index for index, row in enumerate(rows)}
        panel = SpyPanel(); events = [[{"symbol": f"S{i:03d}", "date": "d0500"}] for i in range(40)]
        calendar = _calendar_for_events(panel, events)
        self.assertEqual(panel.calls, 40)
        self.assertEqual(panel.max_symbol_rows, 1000)
        self.assertEqual(sum(len(values) for values in calendar.values()), 40)

    def test_panel_rejects_duplicate_nan_and_bad_prev_close_after_valid_reseal(self):
        mutations = {
            "duplicate": lambda rows: rows["000001"].append(dict(rows["000001"][-1])),
            "nan": lambda rows: rows["000001"][1].update(open=float("nan")),
            "prev": lambda rows: rows["000001"][1].update(prev_close=None),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), TemporaryDirectory() as temp:
                manifest, rows = _panel(Path(temp) / "panel", dataset="x"); mutate(rows); _reseal_panel(manifest, rows)
                store = ShardedPanel(manifest, expected_snapshot_id=_fixture_snapshot_id("x"), expected_oos={"start": "2020-03-01", "end": "2020-04-30", "lockbox_start": "2021-01-01"})
                with self.assertRaises(ValueError): store.validate_all()

    def test_commit_bounded_join_peak_is_batch_not_total_and_matches_cooldown_baseline(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); run_dir = root / "run"; identity = {"run_id": "scaled"}; commits = []
            batch_count, observations_per_batch = 8, 1000
            for batch in range(batch_count):
                symbol = f"S{batch:03d}"; observations = []; outcomes = []
                for index in range(observations_per_batch):
                    day_index = index % 61; oid = f"o{batch}_{index}"; day = f"2020-03-{day_index + 1:02d}"
                    observations.append({"id": oid, "symbol": symbol, "observed_at": day + "T00:00:00"})
                    outcomes.extend({"observation_id": oid, "horizon_bars": horizon, "sample_split": "out_of_sample", "market_regime": "bearish", "net_excess_return": 0.01} for horizon in (5, 10, 20))
                commits.append(write_batch(run_dir, batch, [symbol], observations, outcomes, canonical_hash(identity), loaded_symbols=1, skipped_symbols=[]))
            write_json(run_dir / "artifact_manifest.json", {"execution_identity_hash": canonical_hash(identity)})
            class Panel:
                manifest = {"panel_id": "sha256:panel"}
                def load_symbol(self, symbol):
                    rows = [{"date": f"2020-03-{index + 1:02d}", "open": 10, "tradeable_open": True, "open_reason_codes": []} for index in range(61)]
                    return rows, {row["date"]: index for index, row in enumerate(rows)}
            protocol = {"comparison_id": "comparison_scaled", "comparison_hash": "sha256:comparison", "oos": {"start": "2020-03-01", "end": "2020-03-61"},
                        "comparison_code_snapshot": {"code_snapshot_id": "sha256:code"},
                        "candidates": [{"candidate": "scaled", "case_id": "case_scaled", "protocol_id": "protocol_scaled",
                                        "artifact_commit_hashes": [item["commit_hash"] for item in commits]}],
                        "analysis": {"horizons": [5, 10, 20], "cooldown_trading_bars": 20, "seed": 1},
                        "execution": {"max_exit_delay_bars": 5, "audit_sample_limit": 10}}
            validated = {"scaled": {"research_run": str(run_dir), "case_id": "case_scaled", "protocol_id": "protocol_scaled", "artifact_commit_hashes": [item["commit_hash"] for item in commits]}}
            staging_dir = root / "staging"; manifest = build_compact_staging(protocol, validated, Panel(), staging_dir)
            manifest = verify_staging(protocol, Panel(), staging_dir)
            self.assertEqual(manifest["diagnostics"]["peak_batch_observations"], observations_per_batch)
            self.assertEqual(manifest["diagnostics"]["peak_batch_outcomes"], observations_per_batch * 3)
            self.assertLess(manifest["diagnostics"]["peak_batch_observations"], batch_count * observations_per_batch)
            self.assertEqual(manifest["diagnostics"]["outcome_stream_buffer_rows"], 1)
            events = list(iter_candidate_events(staging_dir, manifest, "scaled"))
            self.assertEqual(len(events), batch_count * 4)  # ordinals 0,20,40,60 per symbol
            self.assertEqual({tuple(sorted(item["horizons"])) for item in events}, {("10", "20", "5")})
            primary, _ = _staged_statistics(staging_dir, manifest, ["scaled"], [5, 10, 20], {"5": 5, "10": 10, "20": 20})
            self.assertTrue(all(item["n_dates"] == 4 and abs(item["effect"] - 0.01) < 1e-12 for item in primary))
            with self.assertRaisesRegex(FileExistsError, "mixed retry"):
                build_compact_staging(protocol, validated, Panel(), staging_dir)
            victim = staging_dir / manifest["candidates"][0]["shards"][0]["path"]
            victim.write_text(victim.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "event staging shard hash mismatch"):
                verify_staging(protocol, Panel(), staging_dir)


class CandidateComparisonE2ETests(unittest.TestCase):
    @staticmethod
    def _code_root(root: Path) -> Path:
        code = root / "code"; (code / "packages").mkdir(parents=True); (code / "scripts").mkdir()
        (code / "packages" / "core.py").write_text("VALUE = 1\n", encoding="utf-8")
        (code / "scripts" / "tool.py").write_text("print('ok')\n", encoding="utf-8")
        (code / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='1'\n", encoding="utf-8")
        return code

    def test_panel_code_snapshot_tamper_and_partial_path_are_rejected(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); code = self._code_root(root); manifest_path, _ = _panel(root / "panel", dataset="x", project_root=code)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8")); snapshot = manifest_path.parent / "builder_code_snapshot.json"
            snapshot.write_text(snapshot.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "snapshot hash mismatch"):
                ShardedPanel(manifest_path, expected_snapshot_id=_fixture_snapshot_id("x"), expected_oos={"start": "2020-03-01", "end": "2020-04-30", "lockbox_start": "2021-01-01"})
            manifest_path, _ = _panel(root / "panel_path", dataset="x", project_root=code); manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["builder_code_snapshot"]["path"] = "partial/builder_code_snapshot.json"
            identity_keys = ("schema_version", "builder", "builder_code_snapshot_id", "builder_code_snapshot", "dataset_snapshot_id", "source_snapshot_fingerprint", "source_root", "source_dataset", "oos", "symbols", "required_fields", "execution_policy", "skipped_initial_rows", "shards")
            manifest["panel_id"] = canonical_hash({key: manifest[key] for key in identity_keys}); write_json(manifest_path, manifest)
            with self.assertRaisesRegex(ValueError, "snapshot path invalid"):
                ShardedPanel(manifest_path, expected_snapshot_id=_fixture_snapshot_id("x"), expected_oos={"start": "2020-03-01", "end": "2020-04-30", "lockbox_start": "2021-01-01"})

    def test_prepare_rejects_code_change_partial_snapshot_and_snapshot_id_difference(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); code = self._code_root(root); cases = {name: _make_case(root, name, "x") for name in ("rsi", "roc", "breakdown")}
            panel, _ = _panel(root / "panel", dataset="x", project_root=code)
            (code / "packages" / "core.py").write_text("VALUE = 2\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "rebuild panel"):
                build_comparison_protocol(cases, panel, root / "changed" / "protocol.json", project_root=code)
            code = self._code_root(root / "fresh"); panel, _ = _panel(root / "fresh_panel", dataset="x", project_root=code)
            partial = root / "partial" / "comparison_code_snapshot.json"; partial.parent.mkdir(); partial.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(FileExistsError, "code snapshot already exists"):
                build_comparison_protocol(cases, panel, partial.parent / "protocol.json", project_root=code)
            def different_snapshot(project_root, output):
                payload = build_code_snapshot(project_root, output); payload["code_snapshot_id"] = "sha256:different"; return payload
            with patch("packages.research.candidate_comparison.build_code_snapshot", side_effect=different_snapshot):
                with self.assertRaisesRegex(ValueError, "snapshots differ"):
                    build_comparison_protocol(cases, panel, root / "different" / "protocol.json", project_root=code)

    def test_run_rejects_post_prepare_code_change_before_staging(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); code = self._code_root(root); cases = {name: _make_case(root, name, "x") for name in ("rsi", "roc", "breakdown")}
            panel, _ = _panel(root / "panel", dataset="x", project_root=code); protocol_path = root / "frozen" / "protocol.json"; output = root / "result.json"
            protocol = build_comparison_protocol(cases, panel, protocol_path, result_path=output, project_root=code)
            self.assertEqual(protocol["comparison_code_snapshot"]["code_snapshot_id"], json.loads((panel.parent / "builder_code_snapshot.json").read_text(encoding="utf-8"))["code_snapshot_id"])
            (code / "scripts" / "tool.py").write_text("print('changed')\n", encoding="utf-8")
            with patch("packages.research.candidate_comparison.REPOSITORY_ROOT", code):
                with self.assertRaisesRegex(ValueError, "differs from frozen comparison snapshot"):
                    run_comparison(protocol_path, output)
            self.assertFalse(Path(protocol["result"]["staging_path"]).exists()); self.assertFalse(output.exists())

    def test_ipo_first_bar_is_auditable_skip_internal_missing_and_forgery_fail(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); snapshot = root / "snapshot.json"; write_json(snapshot, {"dataset_snapshot_id": "sha256:fixture", "symbols": ["001220"]})
            class Source:
                dataset = "fixture"
                def __init__(self, candles): self.root = root; self.candles = candles
                def load(self, symbol, end=None): return self.candles
            first = Candle(datetime(2020, 3, 1, 15, tzinfo=timezone.utc), 10, 10.1, 9.9, 10, 100, 1000, None, False)
            second = Candle(datetime(2020, 3, 2, 15, tzinfo=timezone.utc), 10.1, 10.2, 10, 10.1, 100, 1000, 10, False)
            source_check = {"status": "valid", "source_root": str(root), "source_dataset": "fixture", "snapshot_manifest_fingerprint": "sha256:fixture"}
            with patch("packages.research.comparison_panel.verify_source_against_strong_snapshot", return_value=source_check):
                manifest_path = build_comparison_panel(Source([first, second]), snapshot, ["001220"], start=__import__("datetime").date(2020, 3, 1),
                    end=__import__("datetime").date(2020, 3, 2), lockbox_start=__import__("datetime").date(2021, 1, 1), output_dir=root / "ipo_panel")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8")); skipped = manifest["shards"][0]["skipped_initial_rows"]
            self.assertEqual(skipped, {"count": 1, "dates": ["2020-03-01"], "reason": "missing_prev_close_first_available_bar", "close_references": [10.0]})
            store = ShardedPanel(manifest_path, expected_snapshot_id="sha256:fixture", expected_oos={"start": "2020-03-01", "end": "2020-03-02", "lockbox_start": "2021-01-01"})
            self.assertEqual([row["date"] for row in store.iter_symbol("001220")], ["2020-03-02"])
            with self.assertRaisesRegex(ValueError, "absent from frozen panel calendar"):
                _calendar_for_events(store, [[{"symbol": "001220", "date": "2020-03-01"}]])
            missing = Candle(datetime(2020, 3, 2, 15, tzinfo=timezone.utc), 10.1, 10.2, 10, 10.1, 100, 1000, None, False)
            forged = Candle(datetime(2020, 3, 2, 15, tzinfo=timezone.utc), 10.1, 10.2, 10, 10.1, 100, 1000, 9.5, False)
            with patch("packages.research.comparison_panel.verify_source_against_strong_snapshot", return_value=source_check):
                with self.assertRaisesRegex(ValueError, "missing inside series"):
                    build_comparison_panel(Source([first, missing]), snapshot, ["001220"], start=__import__("datetime").date(2020, 3, 1),
                        end=__import__("datetime").date(2020, 3, 2), lockbox_start=__import__("datetime").date(2021, 1, 1), output_dir=root / "missing_panel")
                with self.assertRaisesRegex(ValueError, "prev_close inconsistent"):
                    build_comparison_panel(Source([first, forged]), snapshot, ["001220"], start=__import__("datetime").date(2020, 3, 1),
                        end=__import__("datetime").date(2020, 3, 2), lockbox_start=__import__("datetime").date(2021, 1, 1), output_dir=root / "forged_panel")
            manifest["shards"][0]["skipped_initial_rows"]["close_references"] = [9.5]
            manifest["skipped_initial_rows"]["rows"][0]["close_reference"] = 9.5
            identity_keys = ("schema_version", "builder", "builder_code_snapshot_id", "builder_code_snapshot", "dataset_snapshot_id", "source_snapshot_fingerprint", "source_root", "source_dataset", "oos", "symbols", "required_fields", "execution_policy", "skipped_initial_rows", "shards")
            manifest["panel_id"] = canonical_hash({key: manifest[key] for key in identity_keys}); write_json(manifest_path, manifest)
            tampered = ShardedPanel(manifest_path, expected_snapshot_id="sha256:fixture", expected_oos={"start": "2020-03-01", "end": "2020-03-02", "lockbox_start": "2021-01-01"})
            with self.assertRaisesRegex(ValueError, "differs from skipped source close"):
                list(tampered.iter_symbol("001220"))

    def test_legacy_case_is_rejected_but_new_sharded_fixed_rule_case_is_accepted(self):
        with TemporaryDirectory() as temp:
            case = _make_case(Path(temp), "rsi", "x")
            self.assertEqual(validate_completed_case(case)["semantic_hash"], FIXED_RULES["rsi"]["semantic_hash"])
            (case / "research_run" / "run_1" / "artifact_manifest.json").unlink()
            with self.assertRaisesRegex(ValueError, "integrity-checked sharded artifacts"):
                validate_completed_case(case)

    def test_replacement_or_duplicate_promoted_rule_is_rejected(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); cases = {name: _make_case(root, name, "x") for name in ("rsi", "roc", "breakdown")}
            panel, _ = _panel(root / "panel", dataset="x")
            swapped = {**cases, "rsi": cases["roc"], "roc": cases["rsi"]}
            with self.assertRaisesRegex(ValueError, "fixed promoted rule identities"):
                build_comparison_protocol(swapped, panel, root / "swap.json")
            duplicated = {**cases, "rsi": cases["roc"]}
            with self.assertRaisesRegex(ValueError, "fixed promoted rule identities"):
                build_comparison_protocol(duplicated, panel, root / "duplicate.json")

    def test_production_panel_builder_uses_snapshot_source_and_formal_execution_gate(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            snapshot = root / "snapshot.json"
            write_json(snapshot, {"dataset_snapshot_id": "sha256:fixture", "symbols": ["000001"]})
            class Source:
                dataset = "fixture"
                def __init__(self, source_root): self.root = source_root
                def load(self, symbol, end=None):
                    return [
                        Candle(datetime(2020, 2, 29, 15, tzinfo=timezone.utc), 10, 10.1, 9.9, 10, 100, 1000, 9.9, False),
                        Candle(datetime(2020, 3, 1, 15, tzinfo=timezone.utc), 10, 10.1, 9.9, 10, 100, 1000, 10, False),
                        Candle(datetime(2020, 3, 2, 15, tzinfo=timezone.utc), 11, 11.2, 10.9, 11, 100, 1000, 10, False),
                    ]
            source_check = {"status": "valid", "source_root": str(root), "source_dataset": "fixture", "snapshot_manifest_fingerprint": "sha256:fixture"}
            with patch("packages.research.comparison_panel.verify_source_against_strong_snapshot", return_value=source_check):
                manifest_path = build_comparison_panel(Source(root), snapshot, ["000001"], start=__import__("datetime").date(2020, 3, 1),
                                                       end=__import__("datetime").date(2020, 3, 2), lockbox_start=__import__("datetime").date(2021, 1, 1), output_dir=root / "panel")
            store = ShardedPanel(manifest_path, expected_snapshot_id="sha256:fixture", expected_oos={"start": "2020-03-01", "end": "2020-03-02", "lockbox_start": "2021-01-01"})
            rows = list(store.iter_symbol("000001"))
            self.assertEqual(len(rows), 2)
            self.assertFalse(rows[1]["tradeable_open"])
            self.assertEqual(rows[1]["open_reason_codes"], ["limit_up_buy_unavailable"])

    def test_protocol_once_tamper_gates_and_research_only_e2e(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            dataset = "ignored-builder-value"
            cases = {name: _make_case(root, name, dataset, offset=index) for index, name in enumerate(("rsi", "roc", "breakdown"))}
            panel, _ = _panel(root / "panel")
            protocol_path = root / "comparison_protocol.json"
            protocol = build_comparison_protocol(cases, panel, protocol_path)
            self.assertEqual(verify_comparison_protocol(protocol)["status"], "valid")
            self.assertEqual(protocol["comparison_code_snapshot"]["code_snapshot_id"], json.loads((panel.parent / "builder_code_snapshot.json").read_text(encoding="utf-8"))["code_snapshot_id"])
            self.assertEqual(protocol["publication"], PUBLICATION)
            tampered_protocol = deepcopy(protocol); tampered_protocol["analysis"]["seed"] += 1
            self.assertEqual(verify_comparison_protocol(tampered_protocol)["status"], "invalid")
            with self.assertRaises(FileExistsError): build_comparison_protocol(cases, panel, protocol_path)
            wrong_identity = dict(protocol["candidates"][0]); wrong_identity["protocol_id"] = "protocol_wrong"
            with self.assertRaisesRegex(ValueError, "frozen identity mismatch"):
                validate_completed_case(cases[protocol["candidates"][0]["candidate"]], wrong_identity)
            output = root / "comparison_result.json"
            result = run_comparison(protocol_path, output)
            self.assertEqual(len(result["primary_hac"]), 9)
            self.assertTrue(all("adjusted_p_value" in item for item in result["primary_hac"]))
            self.assertEqual(result["approval"], "forbidden"); self.assertEqual(result["publication"], "forbidden")
            self.assertFalse(result["final_lockbox_read"])
            staging_manifest = json.loads((Path(protocol["result"]["staging_path"]) / "staging_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(staging_manifest["comparison_code_snapshot_id"], protocol["comparison_code_snapshot"]["code_snapshot_id"])
            self.assertLess(output.stat().st_size, 500_000)
            for ledgers in result["portfolio_validation"]["ledgers"].values():
                for ledger in ledgers.values():
                    self.assertNotIn("trades", ledger); self.assertNotIn("rejected", ledger)
                    self.assertLessEqual(len(ledger["trade_audit_sample"]), 100)
                    self.assertLessEqual(len(ledger["rejected_audit_sample"]), 100)
                    self.assertLessEqual(ledger["diagnostics"]["peak_active_positions"], 20)
            with self.assertRaises(FileExistsError): run_comparison(protocol_path, output)

            # Shard content tampering is detected independently of row parsing.
            victim = Path(protocol["candidates"][0]["research_run"]) / "shards" / "outcomes" / "batch_000000.jsonl"
            victim.write_text(victim.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "integrity hash mismatch"):
                validate_completed_case(cases[protocol["candidates"][0]["candidate"]], protocol["candidates"][0])

    def test_future_panel_row_is_rejected(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); cases = {name: _make_case(root, name, "x") for name in ("rsi", "roc", "breakdown")}
            panel, _ = _panel(root / "panel", future=True, dataset="x")
            with self.assertRaisesRegex(ValueError, "outside OOS/lockbox"):
                build_comparison_protocol(cases, panel, root / "p.json", result_path=root / "result.json")

    def test_entry_tradeability_cannot_use_same_session_volume(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); cases = {name: _make_case(root, name, "x") for name in ("rsi", "roc", "breakdown")}
            panel, rows = _panel(root / "panel", dataset="x")
            rows["000001"][0]["tradeable_open"] = False; rows["000001"][0]["open_reason_codes"] = ["zero_volume"]
            shard = panel.parent / "symbols" / "000001.jsonl"
            shard.write_text("\n".join(json.dumps(row) for row in rows["000001"]) + "\n", encoding="utf-8")
            manifest = json.loads(panel.read_text(encoding="utf-8")); manifest["shards"][0]["sha256"] = file_hash(shard)
            identity = {key: manifest[key] for key in ("schema_version", "builder", "builder_code_snapshot_id", "builder_code_snapshot", "dataset_snapshot_id", "source_snapshot_fingerprint", "source_root", "source_dataset", "oos", "symbols", "required_fields", "execution_policy", "skipped_initial_rows", "shards")}
            manifest["panel_id"] = canonical_hash(identity); write_json(panel, manifest)
            with self.assertRaisesRegex(ValueError, "open execution semantics mismatch"):
                build_comparison_protocol(cases, panel, root / "p.json", result_path=root / "result.json")

    def test_different_dataset_snapshots_are_rejected_before_protocol(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            cases = {"rsi": _make_case(root, "rsi", "same"), "roc": _make_case(root, "roc", "same"), "breakdown": _make_case(root, "breakdown", "different")}
            panel, _ = _panel(root / "panel", dataset="same")
            with self.assertRaisesRegex(ValueError, "different dataset snapshots"):
                build_comparison_protocol(cases, panel, root / "p.json")


if __name__ == "__main__": unittest.main()
