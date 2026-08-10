import os
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from packages.market_data import (
    LocalParquetMarketData,
    build_strong_snapshot,
    consume_source_snapshot_reuse_token,
    verify_source_against_strong_snapshot,
    verify_strong_snapshot,
)
from packages.market_data.snapshot import _file_sha256


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

    def test_manifest_identity_tampering_is_detected_even_before_file_content_changes(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "trend_cache").mkdir()
            (root / "trend_cache" / "000001.parquet").write_bytes(b"frozen-content")
            manifest_path = root / "snapshot.json"
            build_strong_snapshot(LocalParquetMarketData(root), ["000001"], manifest_path)
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["symbols"] = []
            manifest_path.write_text(json.dumps(payload), encoding="utf-8")
            check = verify_strong_snapshot(manifest_path)
            self.assertEqual(check["status"], "invalid")
            self.assertIn("snapshot_identity_mismatch", [item["reason"] for item in check["failures"]])

    def test_source_check_reuse_token_is_one_time_bound_and_avoids_second_full_hash(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "trend_cache").mkdir()
            (root / "trend_cache" / "000001.parquet").write_bytes(b"frozen-content")
            manifest_path = root / "snapshot.json"
            source = LocalParquetMarketData(root)
            build_strong_snapshot(source, ["000001"], manifest_path)
            with patch("packages.market_data.snapshot._file_sha256", wraps=_file_sha256) as hashed:
                check = verify_source_against_strong_snapshot(source, manifest_path, issue_reuse_token=True)
                self.assertEqual(check["status"], "valid")
                after_full_check = hashed.call_count
                self.assertGreater(after_full_check, 0)
                self.assertEqual(consume_source_snapshot_reuse_token(source, manifest_path, check)["status"], "valid")
                self.assertEqual(hashed.call_count, after_full_check)
                self.assertEqual(consume_source_snapshot_reuse_token(source, manifest_path, check)["status"], "invalid")

            fresh = verify_source_against_strong_snapshot(source, manifest_path, issue_reuse_token=True)
            other_source = LocalParquetMarketData(root / "other")
            forged = consume_source_snapshot_reuse_token(other_source, manifest_path, fresh)
            self.assertEqual(forged["status"], "invalid")
            self.assertTrue(any("source_root" in item["reason"] for item in forged["failures"]))


if __name__ == "__main__":
    unittest.main()
