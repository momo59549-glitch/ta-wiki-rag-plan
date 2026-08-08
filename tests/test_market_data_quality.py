from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from packages.market_data import LocalParquetMarketData, audit_market_data_quality


class MarketDataQualityTests(unittest.TestCase):
    def test_current_valid_series_passes(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            dataset = root / "trend_cache"
            dataset.mkdir()
            frame = pd.DataFrame(
                {"open": [10.0, 10.1, 10.2], "high": [10.2, 10.3, 10.4], "low": [9.9, 10.0, 10.1], "close": [10.1, 10.2, 10.3]},
                index=pd.date_range("2026-08-02", periods=3),
            )
            frame.to_parquet(dataset / "000001.parquet")
            result = audit_market_data_quality(LocalParquetMarketData(root), ["000001"], root / "quality.json", as_of=date(2026, 8, 5), active_at_end=["000001"], minimum_bars=2)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["load_failure_count"], 0)

    def test_missing_symbol_fails(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "trend_cache").mkdir()
            result = audit_market_data_quality(LocalParquetMarketData(root), ["000001"], root / "quality.json", as_of=date(2026, 8, 5), active_at_end=["000001"])
            self.assertEqual(result["status"], "failed")


if __name__ == "__main__":
    unittest.main()
