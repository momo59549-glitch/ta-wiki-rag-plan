import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from packages.research.market_summary import aggregate_market_cases, render_market_summary


class MarketSummaryTests(unittest.TestCase):
    def test_aggregates_only_oos_and_blocks_publication(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            case_dir = root / "case_one"
            run_dir = case_dir / "research_run" / "one"
            run_dir.mkdir(parents=True)
            (case_dir / "case.json").write_text(json.dumps({"case_id": "case_one", "research_run": "research_run/one", "dataset_snapshot_id": "snap", "rule": {"id": "hammer", "version": "1", "semantic_hash": "hash"}}), encoding="utf-8")
            (case_dir / "qa_review.json").write_text(json.dumps({"status": "passed"}), encoding="utf-8")
            rows = [
                {"sample_split": "in_sample", "horizon_bars": 1, "market_regime": "bullish", "net_excess_return": 0.5},
                {"sample_split": "out_of_sample", "horizon_bars": 1, "market_regime": "bullish", "net_excess_return": 0.01},
                {"sample_split": "out_of_sample", "horizon_bars": 1, "market_regime": "bullish", "net_excess_return": 0.02},
            ]
            (run_dir / "outcomes.jsonl").write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            summary = aggregate_market_cases(root)
            self.assertEqual(summary["outcomes_out_of_sample"], 2)
            self.assertEqual(summary["publication"], "blocked_until_human_approval")
            self.assertIn("FDR-BH", render_market_summary(summary))


if __name__ == "__main__":
    unittest.main()
