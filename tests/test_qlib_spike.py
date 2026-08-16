import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.run_qlib_spike import _daily_hash, _daily_summary, _series_hash, reduced_feature_config, write_provider


class QlibProviderConversionTests(unittest.TestCase):
    def test_strict_ohlcv_conversion_is_write_once_and_identity_bound(self):
        index = pd.date_range("2020-01-01", periods=4, freq="B")
        frame = pd.DataFrame({"open": [1., 2., 3., 4.], "high": [2., 3., 4., 5.], "low": [.5, 1.5, 2.5, 3.5], "close": [1.5, 2.5, 3.5, 4.5], "volume": [10., 10., 10., 10.]}, index=index)
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "provider"
            digest = write_provider(target, {"000001": frame})
            self.assertTrue(digest.startswith("sha256:"))
            self.assertTrue((target / "calendars" / "day.txt").is_file())
            self.assertTrue((target / "features" / "000001" / "volume.day.bin").is_file())
            with self.assertRaises(ValueError):
                write_provider(target, {"000001": frame})

    def test_vwap_is_absent_from_official_reduced_feature_config(self):
        fields, names, digest = reduced_feature_config()
        self.assertTrue(digest.startswith("sha256:"))
        self.assertEqual(len(fields), len(names))
        self.assertFalse(any("vwap" in x.lower() for x in fields))
        self.assertFalse(any("vwap" in x.lower() for x in names))

    def test_extra_vwap_is_rejected_not_mixed_into_reduced_subset(self):
        index = pd.date_range("2020-01-01", periods=2, freq="B")
        frame = pd.DataFrame({"open": [1., 2.], "high": [2., 3.], "low": [.5, 1.5], "close": [1.5, 2.5], "volume": [10., 10.], "vwap": [1.5, 2.5]}, index=index)
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                write_provider(Path(temp) / "provider", {"000001": frame})

    def test_diagnostic_identity_and_yearly_summary_are_deterministic(self):
        days = pd.to_datetime(["2019-01-02", "2020-01-02", "2021-01-04"])
        series = pd.Series([.1, -.2, .3], index=days)
        summary = _daily_summary(series)
        self.assertEqual(summary["count"], 3)
        self.assertEqual(summary["years"]["2020"]["count"], 1)
        self.assertAlmostEqual(summary["positive_day_ratio"], 2 / 3)
        self.assertTrue(_daily_hash(series).startswith("sha256:"))
        index = pd.MultiIndex.from_product([days[:1], ["000001", "000002"]], names=["datetime", "instrument"])
        self.assertEqual(_series_hash(pd.Series([1., float("nan")], index=index)), _series_hash(pd.Series([1., float("nan")], index=index)))
