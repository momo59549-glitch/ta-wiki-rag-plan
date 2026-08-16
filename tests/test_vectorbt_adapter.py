import unittest

import pandas as pd

from packages.research.vectorbt_adapter import verify_fixed_horizon_portfolio, fixed_close_signals, run_fixed_wide_spike, _fixed_shared_portfolio, SPIKE_ORDER_VALUE
from scripts.run_vectorbt_spike import main as spike_cli


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

    def test_fixed_three_strategy_wide_spike_uses_actual_vectorbt_shared_cash(self):
        index=pd.date_range("2020-01-01",periods=80)
        base=pd.Series(range(100,180),index=index,dtype=float)
        close=pd.DataFrame({"000001":base+((base.index.day%5)-2),"000002":base*1.01},index=index)
        opens=close*0.999
        signals=fixed_close_signals(close)
        self.assertEqual(set(signals),{"sma_10_30_cross","rsi_14_30_55","momentum_20_sign"})
        metrics=run_fixed_wide_spike(opens=opens,closes=close,fees=0.0003,slippage=0.0005)
        self.assertEqual([x.strategy for x in metrics],["sma_10_30_cross","rsi_14_30_55","momentum_20_sign"])
        self.assertTrue(all(x.cash_sharing and x.nonadjudicable and x.engine.startswith("vectorbt.") for x in metrics))

    def test_wide_spike_rejects_axis_or_cost_mismatch(self):
        index=pd.date_range("2020-01-01",periods=40);close=pd.DataFrame({"000001":range(10,50)},index=index)
        with self.assertRaises(ValueError): run_fixed_wide_spike(opens=close.iloc[1:],closes=close)
        with self.assertRaises(ValueError): run_fixed_wide_spike(opens=close,closes=close,fees=-0.1)
        invalid=close.astype(float);invalid.iloc[0,0]=float("inf")
        with self.assertRaises(ValueError): run_fixed_wide_spike(opens=invalid,closes=close)

    def test_shared_fixed_value_orders_fill_entry_and_exit_at_next_open(self):
        import vectorbt as vbt
        index=pd.date_range("2020-01-01",periods=5)
        opens=pd.DataFrame({"000001":[10.,11.,12.,13.,14.],"000002":[20.,21.,22.,23.,24.]},index=index)
        closes=opens+0.5
        entries=pd.DataFrame(False,index=index,columns=opens.columns);exits=entries.copy()
        entries.loc[index[0],:]=True;exits.loc[index[2],:]=True
        portfolio=_fixed_shared_portfolio(vbt=vbt,opens=opens,closes=closes,entries=entries,exits=exits,fees=0.,slippage=0.)
        orders=portfolio.orders.records_readable
        buys=orders[orders["Side"]=="Buy"];sells=orders[orders["Side"]=="Sell"]
        self.assertEqual(len(buys),2);self.assertEqual(len(sells),2)
        self.assertEqual(set(buys["Timestamp"]),{index[1]});self.assertEqual(set(sells["Timestamp"]),{index[3]})
        self.assertEqual(set(buys["Price"]),{11.,21.});self.assertEqual(set(sells["Price"]),{13.,23.})
        self.assertTrue(all(abs(float(value)*float(price)-SPIKE_ORDER_VALUE)<1e-6 for value,price in zip(buys["Size"],buys["Price"])))

    def test_spike_cli_requires_explicit_source_confirmation_before_loading(self):
        self.assertEqual(spike_cli([]), 2)


if __name__ == "__main__":
    unittest.main()
