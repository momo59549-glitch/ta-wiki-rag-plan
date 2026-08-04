import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from packages.agents import FileResearchTeam, TeamConfig
from packages.market_data import LocalParquetMarketData
from packages.research import PipelineConfig
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


if __name__ == "__main__":
    unittest.main()
