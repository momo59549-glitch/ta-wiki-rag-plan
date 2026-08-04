import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from packages.governance import ApprovalError, approve_case, request_approval


class FileApprovalTests(unittest.TestCase):
    def _case(self, root: Path, candidates: list[dict]) -> Path:
        case = root / "case"
        case.mkdir()
        (case / "case.json").write_text(json.dumps({"case_id": "case_1", "publication": "blocked_until_human_approval", "rule": {"id": "hammer", "version": "1.0.0", "semantic_hash": "sha256:x"}, "dataset_snapshot_id": "sha256:data"}), encoding="utf-8")
        (case / "qa_review.json").write_text(json.dumps({"status": "passed"}), encoding="utf-8")
        (case / "hypothesis_draft.json").write_text(json.dumps({"candidate_horizons": candidates}), encoding="utf-8")
        return case

    def test_request_rejects_without_candidate(self):
        with TemporaryDirectory() as temp:
            with self.assertRaises(ApprovalError):
                request_approval(self._case(Path(temp), []))

    def test_explicit_approval_writes_immutable_registry_entry(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(root, [{"horizon_bars": 3, "observations": 300, "mean_net_excess_return": 0.01}])
            request_approval(case)
            target = approve_case(case, "research-lead", "approve", "样本外和成本已复核", root / "registry")
            self.assertTrue(target.is_file())
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["status"], "approved")


if __name__ == "__main__":
    unittest.main()
