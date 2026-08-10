from datetime import date
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import json
import unittest
from unittest.mock import patch

from packages.research import PipelineConfig, build_experiment_protocol
from packages.rule_dsl import compile_rule
from packages.rules import HAMMER_V1


def _runner_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_frozen_campaign.py"
    spec = importlib.util.spec_from_file_location("test_run_frozen_campaign_module", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _campaign_protocol(campaign: Path) -> dict:
    config = PipelineConfig(
        horizons=(1,),
        start=date(2020, 1, 1),
        end=date(2020, 12, 31),
        out_of_sample_start=date(2020, 7, 1),
        universe_manifest="universe.jsonl",
        lockbox_start=date(2021, 6, 1),
    )
    campaign.mkdir(parents=True)
    return build_experiment_protocol(
        compile_rule(HAMMER_V1),
        config,
        ["000001"],
        "sha256:data",
        campaign / "experiment_protocol.json",
        minimum_oos_observations=2,
        code_snapshot_id="sha256:code",
    )


class FrozenCampaignRunnerGateTests(unittest.TestCase):
    def test_legacy_interrupted_execution_without_checkpoint_requires_new_campaign(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            campaign = root / "campaign"
            _campaign_protocol(campaign)
            output = root / "execution"
            output.mkdir()
            (output / "execution_request.json").write_text(json.dumps({"schema_version": "frozen-campaign-execution/v1", "status": "failed"}), encoding="utf-8")
            runner = _runner_module()
            with patch.object(runner, "verify_source_against_strong_snapshot") as source_verify, patch.object(
                sys, "argv", ["run_frozen_campaign.py", "--campaign", str(campaign), "--output-root", str(output), "--resume"],
            ), patch.object(runner.argparse.ArgumentParser, "error", side_effect=ValueError) as error, self.assertRaises(ValueError):
                runner.main()
            source_verify.assert_not_called()
            self.assertIn("derive a new Campaign", error.call_args.args[0])

    def test_existing_execution_is_rejected_before_any_source_hash(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            campaign = root / "campaign"
            _campaign_protocol(campaign)
            output = root / "execution"
            output.mkdir()
            (output / "execution_request.json").write_text("{}", encoding="utf-8")
            runner = _runner_module()
            with patch.object(runner, "verify_source_against_strong_snapshot") as source_verify, patch.object(
                sys,
                "argv",
                ["run_frozen_campaign.py", "--campaign", str(campaign), "--output-root", str(output)],
            ), self.assertRaises(SystemExit):
                runner.main()
            source_verify.assert_not_called()

    def test_incomplete_campaign_metadata_is_rejected_before_any_source_hash(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            campaign = root / "campaign"
            _campaign_protocol(campaign)
            runner = _runner_module()
            with patch.object(runner, "verify_source_against_strong_snapshot") as source_verify, patch.object(
                sys,
                "argv",
                ["run_frozen_campaign.py", "--campaign", str(campaign), "--output-root", str(root / "execution")],
            ), self.assertRaises(SystemExit):
                runner.main()
            source_verify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
