from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from packages.orchestration import CaseState, FileCaseStateMachine, InvalidTransition


class CaseStateMachineTests(unittest.TestCase):
    def test_persists_transitions_and_rejects_skips(self):
        with TemporaryDirectory() as temp:
            machine = FileCaseStateMachine.create(Path(temp) / "case", "case_1")
            with self.assertRaises(InvalidTransition):
                machine.transition(CaseState.OUTCOMES_READY, "bad.skip")
            self.assertTrue(machine.transition(CaseState.DATA_READY, "data.ready", idempotency_key="data:1"))
            self.assertFalse(machine.transition(CaseState.DATA_READY, "data.ready", idempotency_key="data:1"))
            recovered = FileCaseStateMachine.open(Path(temp) / "case")
            self.assertEqual(recovered.state, CaseState.DATA_READY)
            self.assertEqual(recovered.sequence, 1)

    def test_approval_cannot_bypass_hypothesis_gate(self):
        with TemporaryDirectory() as temp:
            machine = FileCaseStateMachine.create(Path(temp) / "case", "case_2")
            with self.assertRaises(InvalidTransition):
                machine.transition(CaseState.RULE_APPROVED, "rule.approved")


if __name__ == "__main__":
    unittest.main()
