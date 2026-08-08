from datetime import date
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from packages.market_data import audit_universe_price_coverage


class UniverseAuditTests(unittest.TestCase):
    def test_missing_optional_overlay_is_reported_not_fatal(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = root / "universe.jsonl"
            manifest.write_text(json.dumps({"symbol": "000001", "active_from": "2020-01-01"}) + "\n", encoding="utf-8")
            primary = root / "trend_cache"; primary.mkdir()
            (primary / "000001.parquet").touch()
            missing_overlay = root / "tushare_incremental_cache"
            result = audit_universe_price_coverage(manifest, (primary, missing_overlay), date(2026, 8, 5))
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["missing_optional_dataset_dirs"], [str(missing_overlay)])

    def test_requires_at_least_one_existing_dataset(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = root / "universe.jsonl"
            manifest.write_text(json.dumps({"symbol": "000001", "active_from": "2020-01-01"}), encoding="utf-8")
            with self.assertRaises(FileNotFoundError):
                audit_universe_price_coverage(manifest, (root / "missing",), date(2026, 8, 5))


if __name__ == "__main__":
    unittest.main()
