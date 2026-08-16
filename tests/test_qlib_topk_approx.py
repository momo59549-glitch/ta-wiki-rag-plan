"""Small integration tests for the fixed Qlib Topk adapter, not a backtest."""
from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from qlib.contrib.strategy.signal_strategy import TopkDropoutStrategy
from scripts.run_qlib_topk_approx import IMPLEMENTATION_TRIALS, TOPK, DROP, TEST, equal_weight_close_to_close_benchmark, implementation_trial_table, quote_frame, write_topk_provider


class QlibTopkApproxTests(unittest.TestCase):
    def _raw(self):
        index = pd.to_datetime(["2019-01-02", "2019-01-03", "2019-01-04", "2019-01-07"])
        return {
            "open": pd.DataFrame({"000001": [10., 10., 10., 10.]}, index=index),
            "high": pd.DataFrame({"000001": [11., 10., 10., 11.]}, index=index),
            "low": pd.DataFrame({"000001": [9., 10., 10., 9.]}, index=index),
            "close": pd.DataFrame({"000001": [10.5, 10., 10., 10.5]}, index=index),
            "volume": pd.DataFrame({"000001": [100., 100., 0., 100.]}, index=index),
            "is_st": pd.DataFrame({"000001": [False, True, False, False]}, index=index),
        }

    def test_observed_gates_and_adjacent_real_change(self):
        frame = quote_frame(self._raw(), "000001")
        self.assertTrue(np.isnan(frame.iloc[0]["change"]))
        self.assertFalse(bool(frame.iloc[0]["limit_buy"]))
        self.assertTrue(bool(frame.iloc[1]["limit_buy"]))  # local ST
        self.assertTrue(bool(frame.iloc[2]["limit_buy"]))  # zero volume and one-price
        self.assertFalse(bool(frame.iloc[3]["limit_buy"]))
        self.assertEqual(frame["factor"].tolist(), [1.0] * 4)

    def test_minimal_provider_has_exchange_fields_and_topk_shift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "provider"
            digest = write_topk_provider(root, {"000001": quote_frame(self._raw(), "000001")})
            self.assertTrue(digest.startswith("sha256:"))
            self.assertTrue((root / "features" / "000001" / "factor.day.bin").is_file())
            self.assertTrue((root / "features" / "000001" / "limit_buy.day.bin").is_file())
        source = inspect.getsource(TopkDropoutStrategy.generate_trade_decision)
        self.assertIn("get_step_time(trade_step, shift=1)", source)
        self.assertEqual((TOPK, DROP, TEST), (30, 3, ("2019-01-01", "2021-12-31")))

    def test_invalid_symbol_is_rejected(self):
        with self.assertRaises(ValueError):
            quote_frame(self._raw(), "30001")
        with self.assertRaises(ValueError):
            quote_frame(self._raw(), "abc")

    def test_equal_weight_benchmark_uses_only_adjacent_raw_closes(self):
        index = pd.to_datetime(["2019-01-02", "2019-01-03", "2019-01-04"])
        raw = self._raw()
        raw["close"] = pd.DataFrame({"000001": [10.0, 11.0, np.nan], "000002": [20.0, 22.0, 24.2]}, index=index)
        series, identity = equal_weight_close_to_close_benchmark(raw)
        self.assertAlmostEqual(series.loc[pd.Timestamp("2019-01-03")], .1)
        # 000001's missing close yields no synthetic return on 2019-01-04.
        self.assertAlmostEqual(series.loc[pd.Timestamp("2019-01-04")], .1)
        self.assertTrue(identity["not_index"] and identity["not_buy_and_hold"])

    def test_exact_five_implementation_trials_are_frozen_not_generated(self):
        self.assertEqual(IMPLEMENTATION_TRIALS, (("A_control_topk30_drop3", 30, 3), ("B_topk30_drop1", 30, 1), ("C_topk50_drop1", 50, 1), ("D_topk50_drop3", 50, 3), ("E_topk100_drop3", 100, 3)))
        table = implementation_trial_table()
        self.assertEqual(len(table), 5)
        self.assertEqual([(row["topk"], row["n_drop"]) for row in table], [(30, 3), (30, 1), (50, 1), (50, 3), (100, 3)])
