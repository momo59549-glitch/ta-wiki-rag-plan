import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from packages.market_data import LocalParquetMarketData, build_strong_snapshot, verify_strong_snapshot


class StrongSnapshotTests(unittest.TestCase):
    def test_content_hash_detects_same_size_mutation(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            dataset = root / "trend_cache"
            dataset.mkdir()
            price = dataset / "000001.parquet"
            price.write_bytes(b"first-content")
            original_times = (price.stat().st_atime_ns, price.stat().st_mtime_ns)
            manifest_path = root / "snapshot.json"
            first = build_strong_snapshot(LocalParquetMarketData(root), ["000001"], manifest_path)

            price.write_bytes(b"other-content")
            os.utime(price, ns=original_times)

            self.assertEqual(price.stat().st_size, first["files"][0]["size"])
            self.assertEqual(verify_strong_snapshot(manifest_path)["status"], "invalid")
            second = build_strong_snapshot(LocalParquetMarketData(root), ["000001"], root / "snapshot-2.json")
            self.assertNotEqual(first["dataset_snapshot_id"], second["dataset_snapshot_id"])


if __name__ == "__main__":
    unittest.main()
