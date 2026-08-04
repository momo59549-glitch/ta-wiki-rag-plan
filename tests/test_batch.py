from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from packages.research.batch import run_in_batches


class BatchTests(unittest.TestCase):
    def test_resume_skips_completed_batches(self):
        with TemporaryDirectory() as temp:
            calls = []
            def runner(symbols):
                calls.append(symbols)
                return {"count": len(symbols)}
            checkpoint = Path(temp) / "checkpoint.json"
            run_in_batches(["b", "a", "c"], 2, checkpoint, runner)
            run_in_batches(["b", "a", "c"], 2, checkpoint, runner)
            self.assertEqual(calls, [["a", "b"], ["c"]])


if __name__ == "__main__":
    unittest.main()
