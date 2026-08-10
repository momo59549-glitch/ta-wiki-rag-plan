import json
from copy import deepcopy
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import sys
import unittest

import numpy as np
import pandas as pd

from packages.contracts import Candle, RuleDefinition
from packages.market_data import LocalParquetMarketData
from packages.research.auto_discovery import (
    DiscoveryConfig,
    GRAMMAR_V1_UNIQUE_LOGIC_CAPACITY,
    PUBLICATION_BLOCK,
    available_candidate_capacity,
    ast_node_count,
    archive_catalog_or_historical_duplicates,
    build_auto_discovery_protocol,
    build_regime_candidate_registry,
    condition_count,
    discovery_semantic_hash,
    generate_candidates,
    metric_offsets,
    rule_logic_reference,
    retire_expired_or_drifted,
    run_auto_discovery,
    select_current_regime_candidates,
)
from packages.research.indicators import compute_indicators
from packages.research.rule_search import (
    SearchConfig,
    _base_columns,
    build_search_protocol,
    vectorized_evaluate,
)
from packages.rule_dsl import compile_rule
from packages.rule_dsl import rule_logic_hash
from packages.rule_engine import evaluate


def _search_config() -> SearchConfig:
    return SearchConfig(
        horizons=(1, 3),
        start=date(2020, 1, 1),
        end=date(2020, 12, 31),
        out_of_sample_start=date(2020, 7, 1),
        lockbox_start=date(2021, 6, 1),
        min_out_of_sample_observations=2,
        cost_stress_multipliers=(2.0, 3.0),
        require_multiple_horizons=2,
    )


def _discovery_config(**overrides) -> DiscoveryConfig:
    values = {
        "generation_id": "g1",
        "candidate_budget": 6,
        "seed": 7,
        "revalidation_days": 10,
        "min_revalidation_observations": 3,
        "max_mean_return_drop": 0.02,
    }
    values.update(overrides)
    return DiscoveryConfig(**values)


def _random_frame(rows: int = 180, seed: int = 17) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 20.0 + np.cumsum(rng.normal(0.0, 0.2, rows))
    open_ = close * (1.0 + rng.normal(0.0, 0.003, rows))
    high = np.maximum(open_, close) * (1.0 + rng.uniform(0.0, 0.01, rows))
    low = np.minimum(open_, close) * (1.0 - rng.uniform(0.0, 0.01, rows))
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.integers(1_000, 10_000, rows),
            "amount": rng.integers(1_000_000, 10_000_000, rows),
            "prev_close": np.concatenate(([np.nan], close[:-1])),
            "is_st": False,
        },
        index=pd.date_range("2020-01-01", periods=rows, name="date"),
    )


def _candles(frame: pd.DataFrame) -> list[Candle]:
    return [
        Candle(
            timestamp=timestamp,
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
            amount=float(row.amount),
            prev_close=None if pd.isna(row.prev_close) else float(row.prev_close),
            is_st=bool(row.is_st),
            available_at=timestamp,
        )
        for timestamp, row in frame.iterrows()
    ]


def _detail_group(horizon: int, regime: str = "bullish", mean: float = 0.02) -> dict:
    return {
        "horizon_bars": horizon,
        "market_regime": regime,
        "sample_size": 10,
        "mean_return": mean,
        "sample_stddev": 0.01,
        "standard_error": 0.003,
        "t_statistic": 3.0,
        "confidence_interval": {"lower": 0.01, "upper": 0.03},
        "raw_p_value": 0.01,
        "adjusted_p_value": 0.02,
        "multiple_testing_reject": True,
    }


def _protocols_and_round(root: Path) -> tuple[SearchConfig, DiscoveryConfig, dict, dict, dict, dict]:
    config = _search_config()
    discovery = _discovery_config()
    definitions = generate_candidates(discovery)
    manifest = root / "universe.jsonl"
    manifest.write_text(json.dumps({"symbol": "000001", "active_from": "2020-01-01", "source": "test"}) + "\n", encoding="utf-8")
    snapshot = {"schema_version": "test-snapshot/v1", "id": "test"}
    search = build_search_protocol(
        definitions,
        ["000001"],
        config,
        root / "search",
        universe_manifest=manifest,
        data_snapshot=snapshot,
    )
    auto = build_auto_discovery_protocol(
        definitions,
        ["000001"],
        config,
        discovery,
        search_protocol_id=search["search_id"],
        data_snapshot=snapshot,
        universe_manifest=manifest,
    )
    candidates = auto["candidate_space"]["candidates"]
    passed = candidates[0]
    ledger = []
    for candidate in candidates:
        common = {
            "rule_id": candidate["definition"]["id"],
            "version": candidate["definition"]["version"],
            "semantic_hash": candidate["rule_semantic_hash"],
            "definition": candidate["definition"],
            "signals": 20,
            "outcomes_oos": 20,
            "best_group": None,
            "passing_groups": [],
            "status": "rejected",
            "rejection_reason": "no_passing_group",
        }
        if candidate is passed:
            common.update(
                {
                    "status": "passed_screen",
                    "rejection_reason": None,
                    "best_group": {
                        "horizon_bars": 3,
                        "market_regime": "bullish",
                        "mean_net_excess_return": 0.03,
                        "sample_size": 10,
                        "adjusted_p_value": 0.02,
                    },
                    "passing_groups": [
                        {
                            "horizon_bars": 1,
                            "market_regime": "bullish",
                            "mean_net_excess_return": 0.02,
                            "sample_size": 10,
                            "adjusted_p_value": 0.02,
                        },
                        {
                            "horizon_bars": 3,
                            "market_regime": "bullish",
                            "mean_net_excess_return": 0.03,
                            "sample_size": 10,
                            "adjusted_p_value": 0.02,
                        },
                    ],
                }
            )
        ledger.append(common)
    round_payload = {
        "schema_version": "rule-search-round/v1",
        "search_id": search["search_id"],
        "candidates": ledger,
    }
    detail = {
        "semantic_hash": passed["rule_semantic_hash"],
        "search_id": search["search_id"],
        "definition": deepcopy(passed["definition"]),
        "statistics": {"groups": [_detail_group(1, mean=0.02), _detail_group(3, mean=0.03)]},
        "stress_statistics": {
            "2.0": {"groups": [_detail_group(1, mean=0.015), _detail_group(3, mean=0.02)]},
            "3.0": {"groups": [_detail_group(1, mean=0.01), _detail_group(3, mean=0.015)]},
        },
    }
    records = {passed["rule_semantic_hash"]: detail}
    return config, discovery, search, auto, round_payload, records


def _write_market_fixture(root: Path) -> tuple[Path, Path, date]:
    trend = root / "trend_cache"
    benchmark = root / "etf_cache"
    trend.mkdir()
    benchmark.mkdir()
    end = date(2020, 9, 16)
    for symbol, seed in (("000001", 1), ("000002", 2), ("000003", 3)):
        frame = _random_frame(rows=260, seed=seed)
        frame.index = pd.date_range("2020-01-01", periods=len(frame), name="date")
        frame.to_parquet(trend / f"{symbol}.parquet")
    reference = _random_frame(rows=260, seed=99)
    reference.index = pd.date_range("2020-01-01", periods=len(reference), name="date")
    reference.to_parquet(benchmark / "000001.parquet")
    manifest = root / "universe.jsonl"
    manifest.write_text(
        "\n".join(json.dumps({"symbol": symbol, "active_from": "2020-01-01", "source": "test"}) for symbol in ("000001", "000002", "000003")) + "\n",
        encoding="utf-8",
    )
    return root, manifest, end


class CandidateGenerationTests(unittest.TestCase):
    def test_generation_is_deterministic_bounded_unique_and_no_lookahead(self):
        config = _discovery_config(candidate_budget=18, seed=41)
        first = generate_candidates(config)
        second = generate_candidates(config)
        self.assertEqual([asdict(item) for item in first], [asdict(item) for item in second])
        self.assertEqual(len(first), config.candidate_budget)
        self.assertEqual(len({compile_rule(item).semantic_hash for item in first}), len(first))
        self.assertEqual(len({discovery_semantic_hash(item) for item in first}), len(first))
        self.assertTrue(all(max(metric_offsets(item.expression), default=0) <= 0 for item in first))
        self.assertTrue(all(ast_node_count(item.expression) <= config.max_ast_nodes for item in first))
        self.assertTrue(all(condition_count(item.expression) <= config.max_conditions for item in first))

    def test_archived_logic_is_not_reemitted(self):
        config = _discovery_config(candidate_budget=10)
        excluded = discovery_semantic_hash(generate_candidates(config)[0])
        regenerated = generate_candidates(config, excluded_semantic_hashes={excluded})
        self.assertNotIn(excluded, {discovery_semantic_hash(item) for item in regenerated})

    def test_v1_capacity_is_finite_and_parent_exclusion_is_preflightable(self):
        first_config = _discovery_config(candidate_budget=64)
        first_generation = generate_candidates(first_config)
        excluded = {discovery_semantic_hash(item) for item in first_generation}
        next_config = _discovery_config(
            generation_id="g2",
            candidate_budget=37,
            parent_generation_id="g1",
            parent_archive_id="sha256:test",
            prior_cumulative_candidate_budget=64,
        )
        self.assertEqual(available_candidate_capacity(first_config), GRAMMAR_V1_UNIQUE_LOGIC_CAPACITY)
        self.assertEqual(available_candidate_capacity(next_config, excluded_semantic_hashes=excluded), 36)
        with self.assertRaisesRegex(ValueError, "剩余有限 grammar 容量 36"):
            generate_candidates(next_config, excluded_semantic_hashes=excluded)

    def test_two_candle_reversal_context_is_not_mislabeled_as_engulfing(self):
        definitions = generate_candidates(_discovery_config(candidate_budget=GRAMMAR_V1_UNIQUE_LOGIC_CAPACITY, seed=7))
        identifiers = {item.id for item in definitions}
        self.assertNotIn("auto_engulfing_bullish", identifiers)
        self.assertNotIn("auto_engulfing_bearish", identifiers)
        self.assertIn("auto_two_candle_reversal_context_bullish", identifiers)
        self.assertIn("auto_two_candle_reversal_context_bearish", identifiers)
        for definition in definitions:
            if definition.id.startswith("auto_two_candle_reversal_context_"):
                self.assertEqual(condition_count(definition.expression), 3)
                expected_context = "lower_close_count" if definition.id.endswith("bullish") else "higher_close_count"
                self.assertIn(expected_context, json.dumps(definition.expression))

    def test_generated_rules_match_formal_engine(self):
        frame = _random_frame()
        candles = _candles(frame)
        for definition in generate_candidates(_discovery_config(candidate_budget=18, seed=13)):
            rule = compile_rule(definition)
            columns = _base_columns(frame)
            indicators = compute_indicators(frame, needs=rule.required_indicators)
            columns.update(indicators)
            vectorized = vectorized_evaluate(rule.normalized_expression, columns, definition.parameters).to_numpy(dtype=bool)
            engine = np.zeros(len(candles), dtype=bool)
            precomputed = {key: values.tolist() for key, values in indicators.items()}
            for index in range(rule.max_lookback, len(candles) - 1):
                engine[index] = evaluate(candles, index, rule, indicators=precomputed).matched
            self.assertTrue(
                np.array_equal(vectorized[rule.max_lookback:-1], engine[rule.max_lookback:-1]),
                msg=f"{definition.id}@{definition.version}",
            )

    def test_logic_hash_ignores_id_version_parameter_label_and_all_order(self):
        catalog_style = RuleDefinition(
            id="rsi_oversold",
            version="1.0.0",
            name_zh="catalog",
            expression={"all": [
                {"lt": [{"metric": {"name": "rsi", "offset": 0, "window": 14}}, {"param": "threshold"}]},
                {"lt": [{"metric": {"name": "close", "offset": 0}}, {"metric": {"name": "sma", "offset": 0, "window": 20}}]},
            ]},
            parameters={"threshold": 30.0},
            warmup_bars=20,
        )
        renamed_auto = RuleDefinition(
            id="auto_rsi_combo",
            version="g1.0027",
            name_zh="auto",
            expression={"all": [
                {"lt": [{"metric": {"name": "close", "offset": 0}}, {"metric": {"name": "sma", "offset": 0, "window": 20}}]},
                {"lt": [{"metric": {"name": "rsi", "offset": 0, "window": 14}}, {"param": "cutoff"}]},
            ]},
            parameters={"cutoff": 30},
            warmup_bars=0,
        )
        self.assertNotEqual(compile_rule(catalog_style).semantic_hash, compile_rule(renamed_auto).semantic_hash)
        self.assertEqual(rule_logic_hash(catalog_style), rule_logic_hash(renamed_auto))


class ProtocolAndRegistryTests(unittest.TestCase):
    def test_protocol_identity_is_stable_and_immutable(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config, discovery, search, auto, _, _ = _protocols_and_round(root)
            definitions = generate_candidates(discovery)
            manifest = root / "universe.jsonl"
            duplicate = build_auto_discovery_protocol(
                definitions,
                ["000001"],
                config,
                discovery,
                search_protocol_id=search["search_id"],
                data_snapshot={"schema_version": "test-snapshot/v1", "id": "test"},
                universe_manifest=manifest,
            )
            self.assertEqual(auto["protocol_hash"], duplicate["protocol_hash"])
            self.assertEqual(auto["auto_discovery_protocol_id"], duplicate["auto_discovery_protocol_id"])
            changed = build_auto_discovery_protocol(
                definitions,
                ["000001"],
                config,
                _discovery_config(seed=8),
                search_protocol_id=search["search_id"],
                data_snapshot={"schema_version": "test-snapshot/v1", "id": "test"},
                universe_manifest=manifest,
            )
            self.assertNotEqual(auto["protocol_hash"], changed["protocol_hash"])
            with self.assertRaises(FileExistsError):
                build_search_protocol(
                    definitions,
                    ["000001"],
                    config,
                    root / "search",
                    universe_manifest=manifest,
                    data_snapshot={"schema_version": "test-snapshot/v1", "id": "test"},
                )

    def test_registry_rejects_tampered_protocol_round_and_candidate_detail(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config, discovery, search, auto, round_payload, records = _protocols_and_round(root)

            tampered_round = deepcopy(round_payload)
            tampered_round["candidates"][0]["definition"]["name_zh"] = "篡改定义"
            with self.assertRaisesRegex(ValueError, "round candidate definition"):
                build_regime_candidate_registry(
                    tampered_round, search, auto, config, screened_at=config.end, candidate_records=records
                )

            missing_search_id = deepcopy(round_payload)
            missing_search_id["search_id"] = None
            with self.assertRaisesRegex(ValueError, "筛选轮次"):
                build_regime_candidate_registry(
                    missing_search_id, search, auto, config, screened_at=config.end, candidate_records=records
                )

            semantic_hash = next(iter(records))
            for field, value in (
                ("semantic_hash", "sha256:tampered"),
                ("search_id", "search_tampered"),
                ("definition", {**records[semantic_hash]["definition"], "name_zh": "篡改 detail"}),
            ):
                with self.subTest(field=field):
                    tampered_records = deepcopy(records)
                    tampered_records[semantic_hash][field] = value
                    with self.assertRaisesRegex(ValueError, "candidate detail"):
                        build_regime_candidate_registry(
                            round_payload, search, auto, config, screened_at=config.end, candidate_records=tampered_records
                        )

            mismatched_auto = build_auto_discovery_protocol(
                generate_candidates(discovery),
                ["000001"],
                config,
                discovery,
                search_protocol_id=search["search_id"],
                data_snapshot={"schema_version": "test-snapshot/v1", "id": "other"},
                universe_manifest=root / "universe.jsonl",
            )
            with self.assertRaisesRegex(ValueError, "数据快照"):
                build_regime_candidate_registry(
                    round_payload, search, mismatched_auto, config, screened_at=config.end, candidate_records=records
                )

    def test_exact_regime_selection_expiry_drift_and_approval_block(self):
        with TemporaryDirectory() as temp:
            config, _, search, auto, round_payload, records = _protocols_and_round(Path(temp))
            registry = build_regime_candidate_registry(
                round_payload,
                search,
                auto,
                config,
                screened_at=config.end,
                candidate_records=records,
            )
            bullish = select_current_regime_candidates(registry, "bullish", config.end + timedelta(days=1))
            self.assertEqual(len(bullish), 1)
            self.assertEqual(select_current_regime_candidates(registry, "bearish", config.end + timedelta(days=1)), [])
            self.assertEqual(bullish[0]["approval_status"], "not_approved")
            self.assertEqual(bullish[0]["execution_authorization"], "blocked")
            self.assertEqual(bullish[0]["publication"], PUBLICATION_BLOCK)
            self.assertTrue(all(item["approval"]["automatic_approval"] is False for item in registry["candidates"]))
            self.assertEqual(registry["lifecycle_revision"], 0)
            self.assertEqual(registry["registry_id"], "regime_registry_" + registry["origin_registry_hash"].removeprefix("sha256:")[:24])

            tampered_registry = deepcopy(registry)
            tampered_registry["periods"]["research_end"] = "2030-01-01"
            with self.assertRaisesRegex(ValueError, "registry_hash"):
                select_current_regime_candidates(tampered_registry, "bullish", config.end + timedelta(days=1))

            wrong_state = retire_expired_or_drifted(
                registry,
                as_of=config.end + timedelta(days=2),
                revalidation_results={
                    bullish[0]["rule_semantic_hash"]: {
                        "market_regime": "bearish",
                        "validation_window_id": "new-oos-1",
                        "is_new_oos": True,
                        "validation_end": (config.end + timedelta(days=1)).isoformat(),
                        "sample_size": 10,
                        "mean_net_excess_return": -0.01,
                        "confidence_interval": {"lower": -0.02, "upper": 0.0},
                        "multiple_testing_reject": True,
                    }
                },
            )
            wrong_candidate = next(item for item in wrong_state["candidates"] if item["rule_semantic_hash"] == bullish[0]["rule_semantic_hash"])
            self.assertEqual(wrong_candidate["states"][0]["status"], "active")
            self.assertEqual(wrong_state["registry_id"], registry["registry_id"])
            self.assertEqual(wrong_state["origin_registry_hash"], registry["origin_registry_hash"])
            self.assertEqual(wrong_state["previous_registry_hash"], registry["registry_hash"])
            self.assertNotEqual(wrong_state["registry_hash"], registry["registry_hash"])
            self.assertNotEqual(wrong_state["registry_state_id"], registry["registry_state_id"])
            self.assertEqual(wrong_state["lifecycle_revision"], 1)

            drifted = retire_expired_or_drifted(
                registry,
                as_of=config.end + timedelta(days=2),
                revalidation_results={
                    bullish[0]["rule_semantic_hash"]: {
                        "market_regime": "bullish",
                        "validation_window_id": "new-oos-2",
                        "is_new_oos": True,
                        "validation_end": (config.end + timedelta(days=1)).isoformat(),
                        "sample_size": 10,
                        "mean_net_excess_return": -0.01,
                        "confidence_interval": {"lower": -0.02, "upper": 0.0},
                        "multiple_testing_reject": True,
                    }
                },
            )
            drift_candidate = next(item for item in drifted["candidates"] if item["rule_semantic_hash"] == bullish[0]["rule_semantic_hash"])
            self.assertEqual(drift_candidate["states"][0]["status"], "retired")
            self.assertEqual(drift_candidate["states"][0]["retirement_reason"], "drift_triggered")

            expired = retire_expired_or_drifted(registry, as_of=config.end + timedelta(days=10))
            expired_candidate = next(item for item in expired["candidates"] if item["rule_semantic_hash"] == bullish[0]["rule_semantic_hash"])
            self.assertEqual(expired_candidate["states"][0]["status"], "retired")
            self.assertEqual(expired_candidate["states"][0]["retirement_reason"], "expired")
            self.assertEqual(select_current_regime_candidates(expired, "bullish", config.end + timedelta(days=10)), [])

    def test_historical_logic_duplicate_is_archived_and_cannot_be_promoted(self):
        with TemporaryDirectory() as temp:
            config, _, search, auto, round_payload, records = _protocols_and_round(Path(temp))
            registry = build_regime_candidate_registry(
                round_payload,
                search,
                auto,
                config,
                screened_at=config.end,
                candidate_records=records,
            )
            candidate = next(item for item in registry["candidates"] if item["eligible_for_frozen_campaign"])
            definition = RuleDefinition(**candidate["definition"])
            historical_clone = RuleDefinition(
                id="previous_catalog_name",
                version="9.9.9",
                name_zh="旧规则",
                expression=deepcopy(definition.expression),
                parameters=deepcopy(definition.parameters),
                warmup_bars=definition.warmup_bars + 5,
            )
            archived = archive_catalog_or_historical_duplicates(
                registry,
                catalog_references=[rule_logic_reference(historical_clone, source_kind="catalog", source_id="previous_catalog_name@9.9.9")],
                historical_trial_references=[
                    rule_logic_reference(
                        historical_clone,
                        source_kind="frozen_campaign_adjudication",
                        source_id="protocol_old",
                        disposition="reject_publication",
                    )
                ],
                as_of=config.end + timedelta(days=1),
            )
            duplicate = next(item for item in archived["candidates"] if item["rule_semantic_hash"] == candidate["rule_semantic_hash"])
            self.assertEqual(duplicate["historical_deduplication"]["status"], "historical_duplicate")
            self.assertEqual(duplicate["archive_status"], "archived_negative")
            self.assertFalse(duplicate["eligible_for_frozen_campaign"])
            self.assertEqual(duplicate["promotion_status"], "historical_duplicate_archived_negative")
            self.assertEqual(archived["origin_registry_hash"], registry["origin_registry_hash"])
            self.assertEqual(archived["previous_registry_hash"], registry["registry_hash"])
            self.assertEqual(archived["lifecycle_revision"], 1)
            self.assertEqual(select_current_regime_candidates(archived, "bullish", config.end + timedelta(days=1)), [])

            tampered = rule_logic_reference(historical_clone, source_kind="catalog", source_id="bad")
            tampered["rule_logic_hash"] = "sha256:tampered"
            with self.assertRaisesRegex(ValueError, "rule_logic_hash"):
                archive_catalog_or_historical_duplicates(
                    registry,
                    catalog_references=[tampered],
                    as_of=config.end + timedelta(days=1),
                )


class AutoDiscoveryRunTests(unittest.TestCase):
    def test_end_to_end_writes_only_research_artifacts_and_refuses_reuse(self):
        with TemporaryDirectory() as temp:
            root, manifest, end = _write_market_fixture(Path(temp))
            output = root / "auto"
            config = SearchConfig(
                horizons=(1, 3),
                start=date(2020, 1, 1),
                end=end,
                out_of_sample_start=date(2020, 6, 1),
                lockbox_start=end + timedelta(days=14),
                min_out_of_sample_observations=2,
                market_regime_window=10,
                require_multiple_horizons=2,
            )
            result = run_auto_discovery(
                LocalParquetMarketData(root),
                ["000001", "000002", "000003"],
                config,
                _discovery_config(candidate_budget=4),
                output,
                universe_manifest=manifest,
            )
            self.assertEqual(result["publication"], PUBLICATION_BLOCK)
            self.assertTrue((output / "auto_discovery_protocol.json").is_file())
            self.assertTrue((output / "candidate_space.json").is_file())
            self.assertTrue((output / "trial_ledger.json").is_file())
            self.assertTrue((output / "regime_candidate_registry.json").is_file())
            registry = json.loads((output / "regime_candidate_registry.json").read_text(encoding="utf-8"))
            self.assertEqual(registry["publication"], PUBLICATION_BLOCK)
            self.assertFalse(any(path.name == "catalog.py" for path in output.rglob("*")))
            with self.assertRaises(FileExistsError):
                run_auto_discovery(
                    LocalParquetMarketData(root),
                    ["000001", "000002", "000003"],
                    config,
                    _discovery_config(candidate_budget=4),
                    output,
                    universe_manifest=manifest,
                )

    def test_cli_small_fixture_and_reuse_rejection(self):
        with TemporaryDirectory() as temp:
            root, manifest, end = _write_market_fixture(Path(temp))
            output = root / "cli-output"
            command = [
                sys.executable,
                "scripts/run_auto_discovery.py",
                "--model-data",
                str(root),
                "--output-root",
                str(output),
                "--start",
                "2020-01-01",
                "--end",
                end.isoformat(),
                "--oos-start",
                "2020-06-01",
                "--lockbox-start",
                (end + timedelta(days=14)).isoformat(),
                "--universe-manifest",
                str(manifest),
                "--symbol-limit",
                "3",
                "--horizons",
                "1,3",
                "--min-samples",
                "2",
                "--regime-window",
                "10",
                "--candidate-budget",
                "4",
                "--generation-id",
                "cli_g1",
            ]
            first = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=False)
            self.assertEqual(first.returncode, 0, msg=first.stderr)
            payload = json.loads(first.stdout)
            self.assertEqual(payload["publication"], PUBLICATION_BLOCK)
            protocol_bytes = (output / "auto_discovery_protocol.json").read_bytes()
            second = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=False)
            self.assertNotEqual(second.returncode, 0)
            self.assertEqual((output / "auto_discovery_protocol.json").read_bytes(), protocol_bytes)

    def test_cli_rejects_budget_above_remaining_parent_capacity_before_data_load(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config, _, search, auto, round_payload, records = _protocols_and_round(root)
            registry = build_regime_candidate_registry(
                round_payload,
                search,
                auto,
                config,
                screened_at=config.end,
                candidate_records=records,
            )
            parent = root / "parent_registry.json"
            parent.write_text(json.dumps(registry), encoding="utf-8")
            command = [
                sys.executable,
                "scripts/run_auto_discovery.py",
                "--model-data",
                str(root / "missing-data"),
                "--output-root",
                str(root / "new-output"),
                "--start",
                "2021-01-01",
                "--oos-start",
                "2021-07-01",
                "--end",
                "2021-12-31",
                "--lockbox-start",
                "2022-06-01",
                "--parent-registry",
                str(parent),
                "--generation-id",
                "g2",
                "--candidate-budget",
                "95",
            ]
            result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("剩余 grammar 容量", result.stderr)
            self.assertFalse((root / "new-output").exists())


if __name__ == "__main__":
    unittest.main()
