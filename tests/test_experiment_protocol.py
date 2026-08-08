from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from packages.research import PipelineConfig, build_experiment_protocol
from packages.rule_dsl import compile_rule
from packages.rules import HAMMER_V1


class ExperimentProtocolTests(unittest.TestCase):
    def test_complete_protocol_is_stable_and_ready(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config = PipelineConfig(
                horizons=(1, 3, 5), start=date(2015, 1, 1), end=date(2026, 8, 4),
                out_of_sample_start=date(2022, 1, 1), universe_manifest="universe.jsonl",
                lockbox_start=date(2026, 9, 1),
            )
            first = build_experiment_protocol(compile_rule(HAMMER_V1), config, ["000001"], "sha256:data", root / "one.json", minimum_oos_observations=300, code_snapshot_id="sha256:code")
            second = build_experiment_protocol(compile_rule(HAMMER_V1), config, ["000001"], "sha256:data", root / "two.json", minimum_oos_observations=300, code_snapshot_id="sha256:code")
            self.assertEqual(first["readiness"]["status"], "ready")
            self.assertEqual(first["protocol_hash"], second["protocol_hash"])
            self.assertEqual(len(first["execution"]["stress_cost_scenarios"]), 2)
            self.assertEqual(first["analysis"]["market_regime_window"], 60)
            self.assertEqual(first["analysis"]["benchmark_symbol"], "000001")
            self.assertEqual(first["analysis"]["benchmark_dataset"], "etf_cache")
            self.assertIsNone(first["analysis"]["min_signal_amount"])
            self.assertTrue(first["analysis"]["skip_untradeable"])

    def test_missing_lockbox_and_universe_is_incomplete(self):
        with TemporaryDirectory() as temp:
            config = PipelineConfig(horizons=(1,), out_of_sample_start=date(2022, 1, 1))
            protocol = build_experiment_protocol(compile_rule(HAMMER_V1), config, ["000001"], "sha256:data", Path(temp) / "protocol.json", minimum_oos_observations=300)
            self.assertEqual(protocol["readiness"]["status"], "incomplete")
            self.assertTrue(protocol["readiness"]["reasons"])

    def test_pipeline_rejects_reading_lockbox(self):
        with self.assertRaises(ValueError):
            PipelineConfig(end=date(2026, 9, 1), lockbox_start=date(2026, 9, 1))


if __name__ == "__main__":
    unittest.main()
