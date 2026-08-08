from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from packages.orchestration import CaseState, FileCaseStateMachine
from packages.orchestration.case_graph import build_case_graph


class LangGraphAdapterTests(unittest.TestCase):
    def test_terminal_domain_state_finishes_without_interrupt(self):
        with TemporaryDirectory() as temp:
            case = Path(temp) / "case"
            machine = FileCaseStateMachine.create(case, "case_graph")
            states = [CaseState.DATA_READY, CaseState.OBSERVATIONS_READY, CaseState.OUTCOMES_READY, CaseState.HYPOTHESIS_DRAFTED, CaseState.BACKTEST_REVIEWED, CaseState.KNOWLEDGE_DRAFTED, CaseState.REPORT_READY, CaseState.QA_FAILED, CaseState.NEEDS_MORE_EVIDENCE]
            for state in states:
                machine.transition(state, f"test.{state.value}")
            graph = build_case_graph()
            result = graph.invoke({"case_dir": str(case)}, config={"configurable": {"thread_id": "case_graph"}})
            self.assertEqual(result["domain_state"], "needs_more_evidence")
            self.assertIsNone(result["requires_action"])


if __name__ == "__main__":
    unittest.main()
