import unittest

from packages.research.hypotheses import build_hypothesis_draft


class HypothesisTests(unittest.TestCase):
    def test_requires_positive_confidence_interval_and_sample_size(self):
        stats = {"groups": [{"horizon_bars": 3, "market_regime": "bearish", "sample_size": 300, "mean_return": 0.01, "confidence_interval": {"lower": 0.001, "upper": 0.02}, "t_statistic": 2.1, "multiple_testing_reject": True, "adjusted_p_value": 0.04}, {"horizon_bars": 5, "market_regime": "bullish", "sample_size": 50, "mean_return": 0.02, "confidence_interval": {"lower": -0.01, "upper": 0.05}, "multiple_testing_reject": False}]}
        result = build_hypothesis_draft(stats, 300)
        self.assertEqual(len(result["candidate_hypotheses"]), 1)
        self.assertIn("insufficient_sample", result["rejected_groups"][0]["rejection_reasons"])

    def test_significantly_negative_groups_are_recorded_as_negative_evidence(self):
        stats = {"groups": [
            {
                "horizon_bars": 10,
                "market_regime": "bullish",
                "sample_size": 500,
                "mean_return": -0.01,
                "confidence_interval": {"lower": -0.02, "upper": -0.004},
                "t_statistic": -3.0,
                "multiple_testing_reject": True,
                "adjusted_p_value": 0.01,
            },
            {
                "horizon_bars": 5,
                "market_regime": "bearish",
                "sample_size": 500,
                "mean_return": -0.001,
                "confidence_interval": {"lower": -0.003, "upper": 0.001},
                "t_statistic": -1.0,
                "multiple_testing_reject": False,
                "adjusted_p_value": 0.3,
            },
        ]}
        result = build_hypothesis_draft(stats, 300)
        self.assertTrue(result["has_negative_evidence"])
        self.assertEqual(len(result["negative_evidence"]), 1)
        self.assertEqual(result["negative_evidence"][0]["horizon_bars"], 10)
        self.assertIn("显著为负", result["negative_evidence"][0]["claim"])
        self.assertIn("negative_evidence", result["limitations"][-1])


if __name__ == "__main__":
    unittest.main()
