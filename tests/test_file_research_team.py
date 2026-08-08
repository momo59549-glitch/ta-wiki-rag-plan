import json
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from packages.agents import FileResearchTeam, TeamConfig
from packages.market_data import LocalParquetMarketData, build_strong_snapshot
from packages.research import PipelineConfig, build_code_snapshot, build_experiment_protocol
from packages.rule_dsl import compile_rule
from packages.rules import HAMMER_V1


class FileResearchTeamTests(unittest.TestCase):
    def test_all_agents_leave_auditable_records_and_cannot_publish(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            for directory in (root / "trend_cache", root / "etf_cache"):
                directory.mkdir()
                frame = pd.DataFrame(
                    [(11, 11.2, 10.8, 11), (11, 11.1, 10.4, 10.6), (10.6, 10.7, 9.9, 10.1), (10.2, 10.3, 9.8, 10.0), (10.0, 10.1, 9.6, 9.8), (9.9, 10, 8.5, 10), (10.1, 10.5, 10, 10.4), (10.4, 10.7, 10.3, 10.6)],
                    columns=["open", "high", "low", "close"], index=pd.date_range("2026-01-01", periods=8, name="date"),
                )
                frame.to_parquet(directory / "000001.parquet")
            source = LocalParquetMarketData(root)
            case_dir = FileResearchTeam(source, root / "cases").run(
                ["000001"], compile_rule(HAMMER_V1),
                TeamConfig(PipelineConfig((1,), benchmark_symbol="000001", out_of_sample_start=pd.Timestamp("2026-01-07").date()), 2),
            )
            agents = [json.loads(line)["agent"] for line in (case_dir / "agent_runs.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(agents, ["Coordinator", "Data", "Scanner", "Reviewer", "Research", "Backtest", "Knowledge", "Report", "QA"])
            case = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
            self.assertEqual(case["publication"], "blocked_until_human_approval")
            self.assertTrue((case_dir / "qa_review.json").is_file())
            self.assertTrue((case_dir / case["research_run"] / "report.md").is_file())
            self.assertTrue((case_dir / "dataset_snapshot_manifest.json").is_file())
            self.assertTrue((case_dir / "experiment_protocol.json").is_file())
            qa = json.loads((case_dir / "qa_review.json").read_text(encoding="utf-8"))
            self.assertEqual(qa["status"], "passed_with_limitations")
            self.assertTrue(qa["strategy_readiness_checks"]["strong_snapshot_valid"])
            self.assertFalse(qa["strategy_readiness_checks"]["experiment_preregistered"])

    def test_frozen_campaign_is_consumed_and_parameter_override_is_rejected(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            for directory in (root / "trend_cache", root / "etf_cache"):
                directory.mkdir()
                frame = pd.DataFrame(
                    [(11, 11.2, 10.8, 11), (11, 11.1, 10.4, 10.6), (10.6, 10.7, 9.9, 10.1), (10.2, 10.3, 9.8, 10.0), (10.0, 10.1, 9.6, 9.8), (9.9, 10, 8.5, 10), (10.1, 10.5, 10, 10.4), (10.4, 10.7, 10.3, 10.6)],
                    columns=["open", "high", "low", "close"], index=pd.date_range("2026-01-01", periods=8, name="date"),
                )
                frame.to_parquet(directory / "000001.parquet")
            universe = root / "universe.jsonl"
            universe.write_text(json.dumps({"symbol": "000001", "active_from": "2020-01-01", "source": "test"}), encoding="utf-8")
            source = LocalParquetMarketData(root)
            campaign = root / "campaign"
            campaign.mkdir()
            rule = compile_rule(HAMMER_V1)
            pipeline = PipelineConfig(
                (1,), date(2026, 1, 1), date(2026, 1, 8), "000001", "etf_cache", 3.0, 5.0,
                date(2026, 1, 7), 2, None, True, str(universe), date(2026, 2, 1),
            )
            team_config = TeamConfig(pipeline, 2, max_candidate_trials=20)
            dataset = build_strong_snapshot(
                source, ["000001"], campaign / "dataset_snapshot.json",
                extra_sources=(("benchmark", LocalParquetMarketData(root, "etf_cache"), ("000001",)),),
            )
            project_root = Path(__file__).resolve().parents[1]
            code = build_code_snapshot(project_root, campaign / "code_snapshot.json")
            protocol = build_experiment_protocol(
                rule, pipeline, ["000001"], dataset["dataset_snapshot_id"], campaign / "experiment_protocol.json",
                minimum_oos_observations=2, max_candidate_trials=20, code_snapshot_id=code["code_snapshot_id"],
            )
            (campaign / "readiness_report.json").write_text(json.dumps({"status": "ready", "checks": {"fixture": True}}), encoding="utf-8")

            case_dir = FileResearchTeam(source, root / "cases").run(["000001"], rule, team_config, frozen_campaign=campaign)

            case = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
            self.assertEqual(case["frozen_campaign"], str(campaign.resolve()))
            self.assertEqual(case["code_snapshot_id"], protocol["code_version"])
            self.assertTrue((case_dir / "campaign_readiness_report.json").is_file())
            overridden = PipelineConfig(
                (1, 2), pipeline.start, pipeline.end, pipeline.benchmark_symbol, pipeline.benchmark_dataset,
                pipeline.commission_bps_per_side, pipeline.slippage_bps_per_side, pipeline.out_of_sample_start,
                pipeline.market_regime_window, pipeline.min_signal_amount, pipeline.skip_untradeable,
                pipeline.universe_manifest, pipeline.lockbox_start,
            )
            with self.assertRaisesRegex(ValueError, "horizons_bound"):
                FileResearchTeam._validated_campaign(campaign, ["000001"], rule, TeamConfig(overridden, 2))


if __name__ == "__main__":
    unittest.main()
