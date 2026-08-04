import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from packages.market_data import LocalParquetMarketData
from packages.research import FileResearchPipeline, PipelineConfig
from packages.rule_dsl import compile_rule
from packages.rules import HAMMER_V1


class FileResearchPipelineTests(unittest.TestCase):
    def test_local_parquet_to_observation_and_outcome(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            data = root / "trend_cache"
            data.mkdir()
            benchmark = root / "etf_cache"
            benchmark.mkdir()
            frame = pd.DataFrame(
                [
                    (11, 11.2, 10.8, 11),
                    (11, 11.1, 10.4, 10.6),
                    (10.6, 10.7, 9.9, 10.1),
                    (10.2, 10.3, 9.8, 10.0),
                    (10.0, 10.1, 9.6, 9.8),
                    (9.9, 10.0, 8.5, 10.0),
                    (10.1, 10.5, 10.0, 10.4),
                    (10.4, 10.7, 10.3, 10.6),
                ],
                columns=["open", "high", "low", "close"],
                index=pd.date_range("2026-01-01", periods=8, name="date"),
            )
            frame["volume"] = 1000
            frame.to_parquet(data / "000001.parquet")
            frame.to_parquet(benchmark / "000001.parquet")
            source = LocalParquetMarketData(root)
            output = FileResearchPipeline(source, root / "runs").run(
                ["000001"], compile_rule(HAMMER_V1), PipelineConfig((1, 2), benchmark_symbol="000001", out_of_sample_start=pd.Timestamp("2026-01-07").date())
            )
            observations = (output / "observations.jsonl").read_text(encoding="utf-8").splitlines()
            outcomes = (output / "outcomes.jsonl").read_text(encoding="utf-8").splitlines()
            summary = json.loads((output / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(len(observations), 1)
            self.assertEqual(len(outcomes), 2)
            self.assertEqual(summary["symbols_loaded"], 1)
            self.assertIn("分周期结果", (output / "report.md").read_text(encoding="utf-8"))
            self.assertIn("平均净超额", (output / "report.md").read_text(encoding="utf-8"))
            outcome = json.loads(outcomes[0])
            self.assertLess(outcome["net_return"], outcome["raw_return"])
            self.assertEqual(outcome["sample_split"], "out_of_sample")


if __name__ == "__main__":
    unittest.main()
