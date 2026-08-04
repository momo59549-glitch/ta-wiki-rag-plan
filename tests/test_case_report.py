import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from packages.research.case_report import render_case_report


class CaseReportTests(unittest.TestCase):
    def test_renders_case_and_statistics(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            run = root / "research_run" / "r1"
            run.mkdir(parents=True)
            (root / "case.json").write_text(json.dumps({"case_id":"c1","state":"needs_more_evidence","publication":"blocked_until_human_approval","rule":{"id":"hammer","version":"1","semantic_hash":"sha256:x"},"dataset_snapshot_id":"sha256:d","research_run":"research_run/r1"}), encoding="utf-8")
            (root / "qa_review.json").write_text(json.dumps({"status":"passed","minimum_oos_observations":300,"research_candidates":0}), encoding="utf-8")
            (root / "hypothesis_draft.json").write_text(json.dumps({"summary":"no candidates"}), encoding="utf-8")
            (root / "statistics_out_of_sample.json").write_text(json.dumps({"outcomes_received":1,"outcomes_excluded":0,"groups":[{"horizon_bars":3,"market_regime":"bearish","sample_size":2,"mean_return":0.01,"t_statistic":1.2,"confidence_interval":{"lower":0.0,"upper":0.02}}]}), encoding="utf-8")
            (root / "agent_runs.jsonl").write_text(json.dumps({"agent":"QA","status":"passed","summary":"ok"}) + "\n", encoding="utf-8")
            (run / "outcomes.jsonl").write_text(json.dumps({"entry_executable":True,"exit_executable":True}) + "\n", encoding="utf-8")
            report = render_case_report(root).read_text(encoding="utf-8")
            self.assertIn("样本外统计摘要", report)
            self.assertIn("needs_more_evidence", report)


if __name__ == "__main__":
    unittest.main()
