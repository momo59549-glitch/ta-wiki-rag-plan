import unittest

import pandas as pd

from packages.research.backtrader_adapter import verify_fixed_horizon_candidate


class BacktraderAdapterTests(unittest.TestCase):
    def test_event_engine_can_verify_next_open_candidate(self):
        index = pd.date_range("2024-01-01", periods=7)
        frame = pd.DataFrame({
            "open": [10, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6],
            "high": [10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8],
            "low": [9.9, 10.0, 10.1, 10.2, 10.3, 10.4, 10.5],
            "close": [10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7],
            "volume": [1000] * 7,
        }, index=index)
        signal = pd.Series([False, True, False, False, False, False, False], index=index)
        result = verify_fixed_horizon_candidate(frame, signal, horizon_bars=2)
        self.assertEqual(result.engine, "backtrader.Cerebro")
        self.assertGreaterEqual(result.end_value, 0)


if __name__ == "__main__":
    unittest.main()
