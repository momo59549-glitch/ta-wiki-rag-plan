import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from packages.research import build_code_snapshot, verify_code_snapshot


class StrategyReadinessTests(unittest.TestCase):
    def test_code_snapshot_detects_mutation(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "packages").mkdir()
            code = root / "packages" / "rule.py"
            code.write_text("VALUE = 1\n", encoding="utf-8")
            manifest = root / "code.json"
            first = build_code_snapshot(root, manifest)
            self.assertTrue(first["code_snapshot_id"].startswith("sha256:"))
            self.assertEqual(verify_code_snapshot(root, manifest)["status"], "valid")
            code.write_text("VALUE = 2\n", encoding="utf-8")
            self.assertEqual(verify_code_snapshot(root, manifest)["status"], "invalid")


if __name__ == "__main__":
    unittest.main()
