"""Source-level audit of installed official Qlib Topk timing; no backtest."""
import inspect
import unittest

from qlib.backtest.exchange import Exchange
from qlib.backtest.utils import TradeCalendarManager
from qlib.contrib.strategy.signal_strategy import TopkDropoutStrategy


class QlibTopkFeasibilityTests(unittest.TestCase):
    def test_official_topk_reads_previous_trade_step_signal(self):
        source = inspect.getsource(TopkDropoutStrategy.generate_trade_decision)
        self.assertIn("get_step_time(trade_step, shift=1)", source)
        self.assertIn("get_step_time(trade_step)", source)

    def test_official_calendar_positive_shift_is_earlier_bar(self):
        source = inspect.getsource(TradeCalendarManager.get_step_time)
        self.assertIn("calendar_index = self.start_index + trade_step - shift", source)
        self.assertIn("shift > 0, return the trading time range of the earlier shift bars", source)

    def test_official_exchange_requires_close_change_factor_and_volume(self):
        source = inspect.getsource(Exchange.__init__)
        self.assertIn('"$close", "$change", "$factor", "$volume"', source)
        self.assertIn("deal_price", source)

    def test_official_exchange_limit_uses_change_and_suspension(self):
        source = inspect.getsource(Exchange._update_limit)
        self.assertIn('self.quote_df["$change"].ge(limit_threshold)', source)
        self.assertIn("suspended", source)
