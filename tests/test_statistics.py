import unittest

from packages.research.statistics import NOT_INVESTMENT_ADVICE, summarize_outcomes


class OutcomeStatisticsTests(unittest.TestCase):
    def test_groups_by_horizon_and_regime_with_uncertainty(self):
        summary = summarize_outcomes(
            [
                {"horizon_bars": 3, "market_regime": "bullish", "net_excess_return": 0.01},
                {"horizon_bars": 3, "market_regime": "bullish", "net_excess_return": 0.03},
                {"horizon_bars": 3, "market_regime": "bearish", "net_excess_return": -0.02},
                {"horizon_bars": 3, "market_regime": "bearish", "net_excess_return": 0.00},
            ]
        )
        self.assertEqual(summary["outcomes_excluded"], 0)
        self.assertEqual(len(summary["groups"]), 2)
        bullish = summary["groups"][1]
        self.assertEqual(bullish["market_regime"], "bullish")
        self.assertEqual(bullish["sample_size"], 2)
        self.assertAlmostEqual(bullish["mean_return"], 0.02)
        self.assertIsNotNone(bullish["standard_error"])
        self.assertLess(bullish["confidence_interval"]["lower"], 0.02)
        self.assertGreater(bullish["confidence_interval"]["upper"], 0.02)
        self.assertEqual(summary["disclaimer"], NOT_INVESTMENT_ADVICE)

    def test_excludes_invalid_returns_and_marks_singleton_as_insufficient(self):
        summary = summarize_outcomes(
            [
                {"horizon_bars": 1, "market_regime": "unknown", "net_excess_return": 0.01},
                {"horizon_bars": 1, "net_excess_return": None},
                {"horizon_bars": 0, "net_excess_return": 0.03},
                {"horizon_bars": 1, "net_excess_return": float("nan")},
            ]
        )
        self.assertEqual(summary["outcomes_excluded"], 3)
        group = summary["groups"][0]
        self.assertEqual(group["evidence_status"], "insufficient_sample")
        self.assertIsNone(group["t_statistic"])
        self.assertIsNone(group["confidence_interval"])

    def test_rejects_invalid_confidence_level(self):
        with self.assertRaises(ValueError):
            summarize_outcomes([], confidence_level=1.0)


if __name__ == "__main__":
    unittest.main()
