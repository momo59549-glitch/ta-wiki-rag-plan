from datetime import date
import unittest

from packages.research.execution import ExecutionConfig, assess_execution, limit_pct_for


def bar(**overrides):
    values = {"date": "2024-01-02", "open": 10.0, "close": 10.0, "pre_close": 10.0, "volume": 1.0, "amount": 10.0}
    values.update(overrides)
    return values


class ExecutionConstraintTests(unittest.TestCase):
    def test_marks_suspension_and_invalid_execution_price(self):
        result = assess_execution(bar(open=0, volume=0), symbol="000001", side="buy", price_at="open")
        self.assertFalse(result.executable)
        self.assertIn("missing_or_invalid_price", result.reason_codes)
        self.assertIn("zero_or_invalid_volume", result.reason_codes)

    def test_main_board_limit_up_cannot_be_bought_and_limit_down_cannot_be_sold(self):
        up = assess_execution(bar(close=11.0), symbol="000001", side="buy")
        down = assess_execution(bar(close=9.0), symbol="000001", side="sell")
        self.assertIn("limit_up_buy_unavailable", up.reason_codes)
        self.assertIn("limit_down_sell_unavailable", down.reason_codes)

    def test_open_execution_never_uses_the_future_close_or_session_liquidity(self):
        result = assess_execution(
            bar(open=10.0, close=11.0, volume=0, amount=0),
            symbol="000001", side="buy", price_at="open", require_session_liquidity=False,
        )
        self.assertTrue(result.executable)
        self.assertEqual(result.metadata["execution_price"], 10.0)

    def test_chinext_and_star_limit_rules(self):
        self.assertEqual(limit_pct_for("300001", date(2024, 1, 1)), 0.20)
        self.assertEqual(limit_pct_for("300001", date(2020, 8, 20)), 0.10)
        self.assertEqual(limit_pct_for("688001", date(2020, 1, 1)), 0.20)
        self.assertFalse(assess_execution(bar(close=12.0), symbol="300001", side="buy").executable)

    def test_valid_bar_is_executable_and_side_is_validated(self):
        self.assertTrue(assess_execution(bar(), symbol="600000", side="sell").executable)
        with self.assertRaises(ValueError):
            assess_execution(bar(), symbol="600000", side="hold")
