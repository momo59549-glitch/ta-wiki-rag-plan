import json
from copy import deepcopy
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from packages.market_data import LocalParquetMarketData, build_strong_snapshot, verify_source_against_strong_snapshot
from packages.research import PipelineConfig, build_experiment_protocol, verify_experiment_protocol
from packages.research.auto_discovery import DiscoveryConfig, build_auto_discovery_protocol, build_regime_candidate_registry, generate_candidates
from packages.research.promotion import (
    build_auto_discovery_promotion_receipt,
    build_frozen_campaign_rule,
    verify_auto_discovery_promotion_receipt,
    verify_frozen_campaign_rule,
)
from packages.research.rule_search import SearchConfig, build_search_protocol
from packages.rule_dsl import compile_rule
from packages.rules import HAMMER_V1


def _hash_identity(identity: dict) -> str:
    return "sha256:" + sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def _active_registry(root: Path) -> tuple[dict, SearchConfig]:
    config = SearchConfig(
        horizons=(1, 3),
        start=date(2020, 1, 1),
        end=date(2020, 12, 31),
        out_of_sample_start=date(2020, 7, 1),
        lockbox_start=date(2021, 6, 1),
        min_out_of_sample_observations=2,
        cost_stress_multipliers=(2.0, 3.0),
        require_multiple_horizons=2,
    )
    discovery = DiscoveryConfig(generation_id="promotion_g1", candidate_budget=1, seed=9)
    definitions = generate_candidates(discovery)
    manifest = root / "universe.jsonl"
    manifest.write_text(json.dumps({"symbol": "000001", "active_from": "2020-01-01", "source": "test"}) + "\n", encoding="utf-8")
    snapshot = {"schema_version": "test-snapshot/v1", "id": "test"}
    search = build_search_protocol(definitions, ["000001"], config, root / "search", universe_manifest=manifest, data_snapshot=snapshot)
    auto = build_auto_discovery_protocol(
        definitions,
        ["000001"],
        config,
        discovery,
        search_protocol_id=search["search_id"],
        data_snapshot=snapshot,
        universe_manifest=manifest,
    )
    candidate = auto["candidate_space"]["candidates"][0]
    groups = [
        {
            "horizon_bars": horizon,
            "market_regime": "bearish",
            "sample_size": 10,
            "mean_return": 0.02 + horizon / 1000,
            "sample_stddev": 0.01,
            "standard_error": 0.003,
            "t_statistic": 3.0,
            "confidence_interval": {"lower": 0.01, "upper": 0.03},
            "raw_p_value": 0.01,
            "adjusted_p_value": 0.02,
            "multiple_testing_reject": True,
        }
        for horizon in (1, 3)
    ]
    round_payload = {
        "schema_version": "rule-search-round/v1",
        "search_id": search["search_id"],
        "candidates": [
            {
                "rule_id": candidate["definition"]["id"],
                "version": candidate["definition"]["version"],
                "semantic_hash": candidate["rule_semantic_hash"],
                "definition": deepcopy(candidate["definition"]),
                "signals": 20,
                "outcomes_oos": 20,
                "best_group": {"horizon_bars": 3, "market_regime": "bearish", "mean_net_excess_return": 0.023, "sample_size": 10, "adjusted_p_value": 0.02},
                "passing_groups": [
                    {"horizon_bars": 1, "market_regime": "bearish", "mean_net_excess_return": 0.021, "sample_size": 10, "adjusted_p_value": 0.02},
                    {"horizon_bars": 3, "market_regime": "bearish", "mean_net_excess_return": 0.023, "sample_size": 10, "adjusted_p_value": 0.02},
                ],
                "status": "passed_screen",
                "rejection_reason": None,
            }
        ],
    }
    detail = {
        "semantic_hash": candidate["rule_semantic_hash"],
        "search_id": search["search_id"],
        "definition": deepcopy(candidate["definition"]),
        "statistics": {"groups": groups},
        "stress_statistics": {
            "2.0": {"groups": deepcopy(groups)},
            "3.0": {"groups": deepcopy(groups)},
        },
    }
    registry = build_regime_candidate_registry(
        round_payload,
        search,
        auto,
        config,
        screened_at=config.end,
        candidate_records={candidate["rule_semantic_hash"]: detail},
    )
    return registry, config


class AutoDiscoveryPromotionTests(unittest.TestCase):
    def test_receipt_and_frozen_definition_are_hash_bound_and_research_only(self):
        with TemporaryDirectory() as temp:
            registry, config = _active_registry(Path(temp))
            candidate = next(item for item in registry["candidates"] if item["eligible_for_frozen_campaign"])
            receipt = build_auto_discovery_promotion_receipt(
                registry,
                rule_semantic_hash=candidate["rule_semantic_hash"],
                market_regime="bearish",
                as_of=config.end + timedelta(days=1),
                selector="test reviewer",
                rationale="one explicitly selected representative",
            )
            self.assertEqual(verify_auto_discovery_promotion_receipt(receipt)["status"], "valid")
            self.assertEqual(receipt["approval"]["status"], "not_approved")
            self.assertFalse(receipt["approval"]["automatic_approval"])
            frozen = build_frozen_campaign_rule(receipt)
            self.assertEqual(verify_frozen_campaign_rule(frozen, receipt)["status"], "valid")

            bad_receipt = deepcopy(receipt)
            bad_receipt["selected_definition"]["name_zh"] = "tampered"
            self.assertEqual(verify_auto_discovery_promotion_receipt(bad_receipt)["status"], "invalid")
            bad_frozen = deepcopy(frozen)
            bad_frozen["definition"]["warmup_bars"] += 1
            self.assertEqual(verify_frozen_campaign_rule(bad_frozen, receipt)["status"], "invalid")

    def test_protocol_verifier_detects_tampering_and_keeps_legacy_catalog_protocol_readable(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config = PipelineConfig(
                horizons=(1, 3),
                start=date(2020, 1, 1),
                end=date(2020, 12, 31),
                out_of_sample_start=date(2020, 7, 1),
                universe_manifest="universe.jsonl",
                lockbox_start=date(2021, 6, 1),
            )
            protocol = build_experiment_protocol(
                compile_rule(HAMMER_V1), config, ["000001"], "sha256:data", root / "protocol.json", minimum_oos_observations=2, code_snapshot_id="sha256:code"
            )
            self.assertEqual(verify_experiment_protocol(protocol)["status"], "valid")
            tampered = deepcopy(protocol)
            tampered["periods"]["research_end"] = "2021-01-01"
            self.assertIn("protocol_hash", verify_experiment_protocol(tampered)["failures"])
            tampered_rule = deepcopy(protocol)
            tampered_rule["rule"]["definition"]["warmup_bars"] += 1
            self.assertIn("rule_definition_hash", verify_experiment_protocol(tampered_rule)["failures"])

            legacy = deepcopy(protocol)
            legacy["rule"] = {
                key: legacy["rule"][key]
                for key in ("id", "version", "semantic_hash", "parameters")
            }
            identity = {
                key: legacy[key]
                for key in (
                    "schema_version", "status", "rule", "dataset_snapshot_id", "universe_manifest", "symbols", "periods", "outcomes", "validation", "execution", "analysis", "code_version", "publication"
                )
            }
            legacy["protocol_hash"] = _hash_identity(identity)
            legacy["protocol_id"] = "protocol_" + legacy["protocol_hash"].removeprefix("sha256:")[:24]
            legacy_check = verify_experiment_protocol(legacy)
            self.assertEqual(legacy_check["status"], "valid")
            self.assertEqual(legacy_check["definition_status"], "legacy_catalog_reference")

    def test_source_snapshot_requires_same_root_and_dataset(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            first_root = root / "first"
            second_root = root / "second"
            for location in (first_root, second_root):
                (location / "trend_cache").mkdir(parents=True)
                pd.DataFrame(
                    [(10.0, 10.1, 9.9, 10.0)],
                    columns=["open", "high", "low", "close"],
                    index=pd.date_range("2020-01-01", periods=1, name="date"),
                ).to_parquet(location / "trend_cache" / "000001.parquet")
            source = LocalParquetMarketData(first_root)
            manifest = root / "snapshot.json"
            build_strong_snapshot(source, ["000001"], manifest)
            self.assertEqual(verify_source_against_strong_snapshot(source, manifest)["status"], "valid")
            changed_root = LocalParquetMarketData(second_root)
            mismatch = verify_source_against_strong_snapshot(changed_root, manifest)
            self.assertEqual(mismatch["status"], "invalid")
            self.assertIn("source_root_mismatch", [item["reason"] for item in mismatch["failures"]])


if __name__ == "__main__":
    unittest.main()
