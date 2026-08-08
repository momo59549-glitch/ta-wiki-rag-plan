import unittest

import pandas as pd

from packages.research.vectorbt_adapter import verify_fixed_horizon_portfolio


class VectorbtAdapterTests(unittest.TestCase):
    def test_signal_is_shifted_to_next_open(self):
        index = pd.date_range("2024-01-01", periods=6)
        opens = pd.Series([10, 10.2, 10.4, 10.6, 10.8, 11.0], index=index)
        closes = pd.Series([10.1, 10.3, 10.5, 10.7, 10.9, 11.1], index=index)
        signal = pd.Series([False, True, False, False, False, False], index=index)
        portfolio = verify_fixed_horizon_portfolio(opens=opens, closes=closes, signal_at_close=signal, horizon_bars=2, slippage=0)
        orders = portfolio.orders.records_readable
        self.assertEqual(orders.iloc[0]["Timestamp"], index[2])
        self.assertAlmostEqual(float(orders.iloc[0]["Price"]), 10.4)


if __name__ == "__main__":
    unittest.main()
