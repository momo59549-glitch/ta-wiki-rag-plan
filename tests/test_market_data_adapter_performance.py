from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
import unittest

import pandas as pd

from packages.market_data import LocalParquetMarketData


class MarketDataAdapterPerformanceTests(unittest.TestCase):
    def test_large_series_loads_linearly_and_preserves_previous_close(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            dataset = root / "trend_cache"
            dataset.mkdir()
            rows = 5000
            frame = pd.DataFrame(
                {"open": [10.0] * rows, "high": [10.2] * rows, "low": [9.8] * rows, "close": [10.1] * rows},
                index=pd.date_range("2000-01-01", periods=rows),
            )
            frame.to_parquet(dataset / "000001.parquet")
            started = perf_counter()
            candles = LocalParquetMarketData(root).load("000001")
            duration = perf_counter() - started
            self.assertEqual(candles[1].prev_close, candles[0].close)
            self.assertEqual(len(candles), rows)
            self.assertLess(duration, 5.0)


if __name__ == "__main__":
    unittest.main()
