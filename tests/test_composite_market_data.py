from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from packages.market_data import CompositeParquetMarketData


class CompositeMarketDataTests(unittest.TestCase):
    def test_prefers_canonical_cache_and_fills_missing_symbols(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "trend_cache").mkdir()
            (root / "tushare_daily_cache").mkdir()
            primary = pd.DataFrame([(10, 11, 9, 10.5)], columns=["open", "high", "low", "close"], index=pd.to_datetime(["2026-01-02"]))
            fallback = pd.DataFrame([(20, 21, 19, 20.5)], columns=["open", "high", "low", "close"], index=pd.to_datetime(["2026-01-02"]))
            primary.to_parquet(root / "trend_cache" / "000001.parquet")
            fallback.to_parquet(root / "tushare_daily_cache" / "000001.parquet")
            fallback.to_parquet(root / "tushare_daily_cache" / "000002.parquet")
            source = CompositeParquetMarketData(root)
            self.assertEqual(source.symbols(), ["000001", "000002"])
            self.assertEqual(source.load("000001")[0].open, 10)
            self.assertEqual(source.load("000002")[0].open, 20)

    def test_incremental_overlay_merges_and_replaces_same_trade_date(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "trend_cache").mkdir()
            (root / "tushare_incremental_cache").mkdir()
            first = pd.DataFrame({"raw_open": [10], "raw_high": [11], "raw_low": [9], "raw_close": [10], "adj_factor": [1], "open": [10], "high": [11], "low": [9], "close": [10]}, index=pd.to_datetime(["2026-08-04"]))
            overlay = pd.DataFrame({"raw_open": [20, 21], "raw_high": [21, 22], "raw_low": [19, 20], "raw_close": [20, 21], "adj_factor": [2, 2], "open": [20, 21], "high": [21, 22], "low": [19, 20], "close": [20, 21]}, index=pd.to_datetime(["2026-08-04", "2026-08-05"]))
            first.to_parquet(root / "trend_cache" / "000001.parquet")
            overlay.to_parquet(root / "tushare_incremental_cache" / "000001.parquet")
            candles = CompositeParquetMarketData(root).load("000001")
            self.assertEqual(len(candles), 2)
            self.assertEqual(candles[0].open, 20)
            self.assertEqual(candles[1].open, 21)


if __name__ == "__main__":
    unittest.main()
