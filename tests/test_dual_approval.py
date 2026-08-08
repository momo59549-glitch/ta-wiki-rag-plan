import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from packages.governance import ApprovalError, request_hypothesis_approval, request_rule_approval, review_hypothesis, review_rule
from packages.orchestration import CaseState, FileCaseStateMachine


class DualApprovalTests(unittest.TestCase):
    def _case(self, root: Path) -> Path:
        case = root / "case"
        machine = FileCaseStateMachine.create(case, "case_1")
        path = [CaseState.DATA_READY, CaseState.OBSERVATIONS_READY, CaseState.OUTCOMES_READY, CaseState.HYPOTHESIS_DRAFTED, CaseState.BACKTEST_REVIEWED, CaseState.KNOWLEDGE_DRAFTED, CaseState.REPORT_READY, CaseState.QA_PASSED, CaseState.AWAITING_HYPOTHESIS_APPROVAL]
        for state in path:
            machine.transition(state, f"test.{state.value}")
        (case / "case.json").write_text(json.dumps({"case_id": "case_1", "state": machine.state.value, "rule": {"id": "hammer", "version": "1.0.0", "semantic_hash": "sha256:x"}, "dataset_snapshot_id": "sha256:data"}), encoding="utf-8")
        (case / "qa_review.json").write_text(json.dumps({"status": "passed"}), encoding="utf-8")
        (case / "hypothesis_draft.json").write_text(json.dumps({"candidate_horizons": [{"horizon_bars": 3}]}), encoding="utf-8")
        return case

    def test_two_distinct_humans_are_required(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(root)
            request_hypothesis_approval(case)
            review_hypothesis(case, "lead", "approve", "hypothesis reviewed")
            request_rule_approval(case)
            with self.assertRaises(ApprovalError):
                review_rule(case, "lead", "approve", "same person", root / "registry")
            review_rule(case, "owner", "approve", "rule reviewed", root / "registry")
            self.assertEqual(FileCaseStateMachine.open(case).state, CaseState.RULE_APPROVED)
            self.assertTrue((root / "registry" / "hammer-1.0.0.json").is_file())


if __name__ == "__main__":
    unittest.main()
