from datetime import datetime, timedelta, timezone
import unittest

from packages.contracts import Candle
from packages.rule_dsl import compile_rule
from packages.rule_engine import evaluate
from packages.rules import HAMMER_V1


class AvailableAtTests(unittest.TestCase):
    def test_rule_rejects_a_bar_not_available_at_decision_time(self):
        base = datetime(2024, 1, 1, 15, tzinfo=timezone.utc)
        series = [
            Candle(base + timedelta(days=index), 10, 10.5, 9, 10, available_at=base + timedelta(days=index))
            for index in range(7)
        ]
        delayed = series[-1]
        series[-1] = Candle(delayed.timestamp, delayed.open, delayed.high, delayed.low, delayed.close, available_at=delayed.timestamp + timedelta(minutes=1))
        result = evaluate(series, 6, compile_rule(HAMMER_V1))
        self.assertEqual(result.status, "data_error")
        self.assertIn("未来数据不可见", result.warnings[0])


if __name__ == "__main__":
    unittest.main()
