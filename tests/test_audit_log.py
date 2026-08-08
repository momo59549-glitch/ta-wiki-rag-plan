from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from packages.governance import FileAuditLog


class AuditLogTests(unittest.TestCase):
    def test_hash_chain_detects_tampering(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "audit.jsonl"
            log = FileAuditLog(path)
            log.append("one", "alice", "operator", {"x": 1})
            log.append("two", "bob", "reviewer", {"x": 2})
            self.assertTrue(log.verify()["valid"])
            text = path.read_text(encoding="utf-8").replace('"x": 1', '"x": 9', 1)
            path.write_text(text, encoding="utf-8")
            self.assertFalse(log.verify()["valid"])


if __name__ == "__main__":
    unittest.main()
