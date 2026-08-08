from datetime import date, timedelta
import unittest

from packages.research.validation import WalkForwardConfig, build_walk_forward_folds, validation_status


class WalkForwardValidationTests(unittest.TestCase):
    def test_uses_skfolio_and_keeps_a_purge_gap(self):
        dates = [date(2020, 1, 1) + timedelta(days=index) for index in range(20)]
        result = build_walk_forward_folds(dates, WalkForwardConfig(8, 3, 2))
        self.assertEqual(result["engine"], "skfolio.WalkForward")
        self.assertTrue(result["folds"])
        first = result["folds"][0]
        self.assertGreater(min(first["test_indices"]) - max(first["train_indices"]), 2)

    def test_short_series_is_explicitly_not_validated(self):
        result = build_walk_forward_folds([date(2020, 1, 1)], WalkForwardConfig(8, 3, 2))
        self.assertEqual(result["reason"], "insufficient_observation_dates")

    def test_lockbox_is_sealed_until_a_date_inside_it_is_viewed(self):
        self.assertEqual(validation_status(all_dates=[date(2025, 1, 1)], lockbox_start=date(2026, 1, 1)), "sealed")
        self.assertEqual(validation_status(all_dates=[date(2026, 1, 1)], lockbox_start=date(2026, 1, 1)), "contaminated")


if __name__ == "__main__":
    unittest.main()
