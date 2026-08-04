import unittest
from datetime import datetime, timedelta, timezone

from packages.backtest import BacktestManifest, run_single_bar_strategy
from packages.contracts import Candle, RuleDefinition
from packages.rule_dsl import RuleCompileError, compile_rule
from packages.rule_engine import evaluate


def candle(day, open_, high, low, close):
    return Candle(datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=day), open_, high, low, close)


HAMMER = RuleDefinition(
    id="hammer", version="1.0.0", name_zh="锤子线（工程近似）", warmup_bars=3,
    parameters={"min_lower_shadow_body": 2.0, "max_upper_shadow_range": 0.15},
    expression={"all": [
        {"gte": [{"safe_div": [{"metric": {"name": "lower_shadow", "offset": 0}}, {"max": [{"metric": {"name": "body", "offset": 0}}, 0.01]}]}, {"param": "min_lower_shadow_body"}]},
        {"lte": [{"safe_div": [{"metric": {"name": "upper_shadow", "offset": 0}}, {"max": [{"metric": {"name": "range", "offset": 0}}, 0.01]}]}, {"param": "max_upper_shadow_range"}]},
        {"context": {"name": "lower_close_count", "window": 3, "min_count": 2}},
    ]},
)


class RuleSteelThreadTests(unittest.TestCase):
    def setUp(self):
        self.series = [candle(0, 11, 11.2, 10.8, 11), candle(1, 11, 11.1, 10.4, 10.6), candle(2, 10.6, 10.7, 9.9, 10.1), candle(3, 10.2, 10.3, 9.0, 10.3), candle(4, 10.4, 10.8, 10.3, 10.7)]

    def test_hammer_and_next_open_execution(self):
        compiled = compile_rule(HAMMER)
        result = evaluate(self.series, 3, compiled)
        self.assertTrue(result.matched)
        self.assertEqual(result.executable_from, self.series[4].timestamp)
        backtest = run_single_bar_strategy(self.series, compiled, BacktestManifest(compiled.semantic_hash))
        self.assertEqual(backtest.trades, 1)

    def test_future_offset_is_rejected(self):
        invalid = RuleDefinition("bad", "1", "错误", {"gt": [{"metric": {"name": "body", "offset": 1}}, 0]})
        with self.assertRaises(RuleCompileError):
            compile_rule(invalid)

    def test_future_mutation_does_not_change_past_signal(self):
        compiled = compile_rule(HAMMER)
        before = evaluate(self.series, 3, compiled).matched
        altered = self.series[:-1] + [candle(4, 99, 100, 98, 99)]
        self.assertEqual(before, evaluate(altered, 3, compiled).matched)


if __name__ == "__main__":
    unittest.main()
