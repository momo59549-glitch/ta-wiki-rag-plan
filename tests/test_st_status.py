import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from packages.market_data.st_status import audit_is_st
from packages.market_data.tushare_daily import _merge_incremental_row
from packages.market_data.universe import load_universe_memberships


def _timeline(root: Path) -> Path:
    manifest = root / "st_timeline.jsonl"
    manifest.write_text(
        json.dumps({"symbol": "000001", "active_from": "2024-01-01", "active_to": "2024-01-31", "source": "test"}) + "\n",
        encoding="utf-8",
    )
    return manifest


class STTimelineTests(unittest.TestCase):
    def test_merge_incremental_row_uses_timeline(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            timeline = load_universe_memberships(_timeline(root))
            record = {"symbol": "000001", "ts_code": "000001.SZ", "name": "测试"}

            def row(trade_date: str) -> Path:
                path = root / f"{trade_date}.parquet"
                _merge_incremental_row(
                    path,
                    record,
                    {
                        "trade_date": trade_date,
                        "open": 10.0,
                        "high": 10.5,
                        "low": 9.8,
                        "close": 10.2,
                        "pre_close": 10.0,
                        "vol": 1000,
                        "amount": 100.0,
                    },
                    1.0,
                    st_timeline=timeline,
                )
                return path

            inside = pd.read_parquet(row("20240115"))
            outside = pd.read_parquet(row("20240301"))
            self.assertTrue(bool(inside["is_st"].iloc[0]))
            self.assertFalse(bool(outside["is_st"].iloc[0]))

    def test_audit_detects_mismatch(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            trend = root / "trend_cache"
            trend.mkdir()
            index = pd.date_range("2024-01-01", periods=60)
            frame = pd.DataFrame(
                {
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.2,
                    "volume": 1000,
                    "amount": 1_000_000,
                    "prev_close": 10.0,
                    "is_st": [day.date() <= pd.Timestamp("2024-01-31").date() for day in index],
                },
                index=index,
            )
            frame.to_parquet(trend / "000001.parquet")
            manifest = _timeline(root)
            result = audit_is_st(model_data=root, datasets=("trend_cache",), symbols=["000001"], st_manifest=manifest)
            self.assertEqual(result["status"], "validated")
            self.assertEqual(result["mismatch_bars"], 0)
            frame["is_st"] = False
            frame.to_parquet(trend / "000001.parquet")
            result = audit_is_st(model_data=root, datasets=("trend_cache",), symbols=["000001"], st_manifest=manifest)
            self.assertEqual(result["status"], "mismatch")
            self.assertGreater(result["mismatch_bars"], 0)
            result = audit_is_st(model_data=root, datasets=("trend_cache",), symbols=["000001"])
            self.assertEqual(result["status"], "unvalidated")


if __name__ == "__main__":
    unittest.main()
