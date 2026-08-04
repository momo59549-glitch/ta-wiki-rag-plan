import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from packages.research.case_index import get_case, list_cases


class CaseIndexTests(unittest.TestCase):
    def test_lists_cases_and_blocks_path_escape(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            case = root / "case_1"
            case.mkdir()
            (case / "case.json").write_text(json.dumps({"case_id": "case_1", "created_at": "2026-01-01", "state": "needs_more_evidence", "rule": {}}), encoding="utf-8")
            (case / "qa_review.json").write_text(json.dumps({"status": "passed", "research_candidates": 0}), encoding="utf-8")
            self.assertEqual(list_cases(root)[0]["case_id"], "case_1")
            self.assertEqual(get_case(root, "case_1")["case"]["state"], "needs_more_evidence")
            with self.assertRaises(ValueError):
                get_case(root, "../case_1")


if __name__ == "__main__":
    unittest.main()
