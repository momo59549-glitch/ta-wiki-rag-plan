import unittest

from packages.research.hypotheses import build_hypothesis_draft


class HypothesisTests(unittest.TestCase):
    def test_requires_positive_confidence_interval_and_sample_size(self):
        stats = {"groups": [{"horizon_bars": 3, "market_regime": "bearish", "sample_size": 300, "mean_return": 0.01, "confidence_interval": {"lower": 0.001, "upper": 0.02}, "t_statistic": 2.1}, {"horizon_bars": 5, "market_regime": "bullish", "sample_size": 50, "mean_return": 0.02, "confidence_interval": {"lower": -0.01, "upper": 0.05}}]}
        result = build_hypothesis_draft(stats, 300)
        self.assertEqual(len(result["candidate_hypotheses"]), 1)
        self.assertIn("insufficient_sample", result["rejected_groups"][0]["rejection_reasons"])


if __name__ == "__main__":
    unittest.main()
