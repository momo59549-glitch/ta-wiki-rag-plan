import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from packages.market_data import LocalParquetMarketData
from packages.research import FileResearchPipeline, PipelineConfig
from packages.research.json_store import write_json
from packages.research.run_artifacts import build_checkpoint, iter_run_rows, load_commits
from packages.rule_dsl import compile_rule
from packages.rules import HAMMER_V1


class InjectedCrash(RuntimeError):
    pass


def _fixture(root: Path, symbol_count: int = 5):
    data = root / "trend_cache"
    benchmark = root / "etf_cache"
    data.mkdir(); benchmark.mkdir()
    frame = pd.DataFrame(
        [(11, 11.2, 10.8, 11), (11, 11.1, 10.4, 10.6), (10.6, 10.7, 9.9, 10.1),
         (10.2, 10.3, 9.8, 10.0), (10.0, 10.1, 9.6, 9.8), (9.9, 10.0, 8.5, 10.0),
         (10.1, 10.5, 10.0, 10.4), (10.4, 10.7, 10.3, 10.6)],
        columns=["open", "high", "low", "close"],
        index=pd.date_range("2026-01-01", periods=8, name="date"),
    )
    frame["volume"] = 1000
    symbols = [f"{index:06d}" for index in range(1, symbol_count + 1)]
    for symbol in symbols:
        frame.to_parquet(data / f"{symbol}.parquet")
    frame.to_parquet(benchmark / "000001.parquet")
    return LocalParquetMarketData(root), symbols


class ResumableResearchPipelineTests(unittest.TestCase):
    def _run_args(self, root: Path):
        source, symbols = _fixture(root)
        return FileResearchPipeline(source, root / "runs"), symbols, compile_rule(HAMMER_V1), PipelineConfig(
            (1, 2), benchmark_symbol="000001", out_of_sample_start=pd.Timestamp("2026-01-07").date()
        )

    def test_crash_at_each_write_phase_resumes_without_loss_or_duplicates(self):
        for phase in ("after_observations_shard", "after_outcomes_shard", "after_commit_marker", "after_checkpoint"):
            with self.subTest(phase=phase), TemporaryDirectory() as temp:
                root = Path(temp)
                pipeline, symbols, rule, config = self._run_args(root)
                crashed = False

                def fault(actual, batch_index):
                    nonlocal crashed
                    if not crashed and actual == phase and batch_index == 0:
                        crashed = True
                        raise InjectedCrash(phase)

                with self.assertRaises(InjectedCrash):
                    pipeline.run(symbols, rule, config, dataset_snapshot_id="sha256:frozen", experiment_protocol_id="protocol_x",
                                 experiment_protocol_hash="sha256:protocol", code_snapshot_id="sha256:code",
                                 case_id="case_x", run_id="run_x", batch_size=2, fault_injector=fault)
                run_dir = root / "runs" / "run_x"
                progress = json.loads((run_dir / "progress.json").read_text(encoding="utf-8"))
                self.assertEqual(progress["status"], "interrupted")
                output = pipeline.run(symbols, rule, config, dataset_snapshot_id="sha256:frozen", experiment_protocol_id="protocol_x",
                                      experiment_protocol_hash="sha256:protocol", code_snapshot_id="sha256:code",
                                      case_id="case_x", run_id="run_x", batch_size=2, resume=True)
                observations = list(iter_run_rows(output, "observations"))
                outcomes = list(iter_run_rows(output, "outcomes"))
                self.assertEqual(len(observations), len(symbols))
                self.assertEqual(len(outcomes), len(symbols) * 2)
                self.assertEqual(len({item["id"] for item in observations}), len(observations))
                self.assertEqual(len({(item["observation_id"], item["horizon_bars"]) for item in outcomes}), len(outcomes))
                self.assertEqual(json.loads((output / "progress.json").read_text(encoding="utf-8"))["status"], "completed")

    def test_checkpoint_tamper_and_identity_mismatches_are_rejected(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            pipeline, symbols, rule, config = self._run_args(root)
            def fault(phase, batch_index):
                if phase == "after_checkpoint" and batch_index == 0:
                    raise InjectedCrash()
            with self.assertRaises(InjectedCrash):
                pipeline.run(symbols, rule, config, dataset_snapshot_id="sha256:frozen", experiment_protocol_id="protocol_x",
                             experiment_protocol_hash="sha256:protocol", code_snapshot_id="sha256:code",
                             case_id="case_x", run_id="run_x", batch_size=2, fault_injector=fault)
            common = dict(dataset_snapshot_id="sha256:frozen", experiment_protocol_id="protocol_x",
                          experiment_protocol_hash="sha256:protocol", code_snapshot_id="sha256:code",
                          case_id="case_x", run_id="run_x", batch_size=2, resume=True)
            for changed in ({"dataset_snapshot_id": "sha256:other"}, {"experiment_protocol_hash": "sha256:other"},
                            {"code_snapshot_id": "sha256:other"}, {"case_id": "case_other"}, {"batch_size": 3}):
                with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, "identity mismatch"):
                    pipeline.run(symbols, rule, config, **{**common, **changed})
            checkpoint_path = root / "runs" / "run_x" / "checkpoint.json"
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            checkpoint["symbols_processed"] += 1
            checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "integrity hash mismatch"):
                pipeline.run(symbols, rule, config, **common)

    def test_batches_bound_peak_rows_and_completed_resume_is_idempotently_rejected(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            pipeline, symbols, rule, config = self._run_args(root)
            output = pipeline.run(symbols, rule, config, dataset_snapshot_id="sha256:frozen", case_id="case_x", run_id="run_x", batch_size=2)
            manifest = json.loads((output / "artifact_manifest.json").read_text(encoding="utf-8"))
            commits = load_commits(output, manifest["execution_identity_hash"])
            self.assertEqual(len(commits), 3)
            self.assertLessEqual(max(len(item["symbols"]) for item in commits), 2)
            self.assertLessEqual(max(item["observations"]["count"] for item in commits), 2)
            legacy_view = [json.loads(line) for line in (output / "outcomes.jsonl").read_text(encoding="utf-8").splitlines() if line]
            self.assertEqual(list(iter_run_rows(output, "outcomes")), legacy_view)
            with self.assertRaisesRegex(ValueError, "already completed"):
                pipeline.run(symbols, rule, config, dataset_snapshot_id="sha256:frozen", case_id="case_x", run_id="run_x", batch_size=2, resume=True)

    def test_valid_commit_ahead_of_checkpoint_is_adopted_without_redo(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            pipeline, symbols, rule, config = self._run_args(root)
            def crash(phase, batch_index):
                if phase == "after_commit_marker" and batch_index == 0:
                    raise InjectedCrash()
            with self.assertRaises(InjectedCrash):
                pipeline.run(symbols, rule, config, dataset_snapshot_id="sha256:frozen", case_id="case_x", run_id="run_x", batch_size=2, fault_injector=crash)
            checkpoint_path = root / "runs" / "run_x" / "checkpoint.json"
            current = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            # Model a power loss before checkpoint.replace(): the prior valid
            # zero-commit checkpoint remains while batch 0 commit is durable.
            write_json(checkpoint_path, build_checkpoint(current["execution_identity"], [], status="running", started_at=current["started_at"]))
            touched_batches = []
            def observe(phase, batch_index):
                if phase == "after_observations_shard":
                    touched_batches.append(batch_index)
            pipeline.run(symbols, rule, config, dataset_snapshot_id="sha256:frozen", case_id="case_x", run_id="run_x", batch_size=2, resume=True, fault_injector=observe)
            self.assertNotIn(0, touched_batches)
            self.assertEqual(touched_batches, [1, 2])


if __name__ == "__main__":
    unittest.main()
