import unittest
import tempfile
from pathlib import Path
import pandas as pd

from scripts.run_qlib_small_account_top5 import ACCOUNT, PER_POSITION_CAP, RESERVE_CASH, affordable_lot, first_observed_session_of_week, official_trade_count, weekly_top5_strategy
from scripts.run_qlib_topk_approx import quote_frame, write_topk_provider


BASE = {"open_cost": .0005, "close_cost": .0015, "min_cost": 5.0, "impact_cost": 0.0}


class SmallAccountTop5Tests(unittest.TestCase):
    def test_initial_five_lots_cannot_breach_risk_or_cash_reserve(self):
        cash = ACCOUNT
        total = 0.0
        for _ in range(5):
            amount, _ = affordable_lot(10.0, cash, BASE)
            value = amount * 10.0
            cost = max(value * BASE["open_cost"], BASE["min_cost"])
            self.assertLessEqual(value + cost, PER_POSITION_CAP)
            cash -= value + cost
            total += value + cost
        self.assertLessEqual(total, 25_000.0)
        self.assertGreaterEqual(cash, RESERVE_CASH)

    def test_high_price_and_min_cost_can_make_lot_unaffordable(self):
        self.assertEqual(affordable_lot(100.0, 10_000.0, BASE)[0], 0.0)
        self.assertEqual(affordable_lot(49.96, 10_000.0, BASE)[0], 0.0)

    def test_week_first_session_handles_holiday_and_other_days_are_empty(self):
        friday = pd.Timestamp("2021-01-08")
        tuesday_after_holiday = pd.Timestamp("2021-01-12")
        wednesday = pd.Timestamp("2021-01-13")
        self.assertTrue(first_observed_session_of_week(tuesday_after_holiday, friday))
        self.assertFalse(first_observed_session_of_week(wednesday, tuesday_after_holiday))

    def test_timing_is_previous_close_to_next_week_open(self):
        friday = pd.Timestamp("2021-01-08")
        monday = pd.Timestamp("2021-01-11")
        self.assertTrue(first_observed_session_of_week(monday, friday))

    def test_official_indicator_count_accepts_column_or_index(self):
        self.assertEqual(official_trade_count(pd.DataFrame({"count": [1, 2]})), 3.0)
        self.assertEqual(official_trade_count(pd.DataFrame([[1, 2]], index=["count"])), 3.0)

    def test_official_qlib_initial_build_and_later_single_replacement(self):
        import qlib
        from qlib.backtest import backtest
        from qlib.config import REG_CN

        dates = pd.bdate_range("2019-01-04", "2019-01-25")
        symbols = [f"{number:06d}" for number in range(1, 8)]
        raw = {field: {} for field in ("open", "high", "low", "close", "volume", "is_st")}
        for symbol in symbols:
            price = 100.0 if symbol == "000007" else 10.0
            for field, multiplier in (("open", 1), ("high", 1.01), ("low", .99), ("close", 1)):
                raw[field][symbol] = pd.Series(price * multiplier, index=dates)
            raw["volume"][symbol] = pd.Series(1000.0, index=dates)
            raw["is_st"][symbol] = pd.Series(False, index=dates)
        wide = {field: pd.DataFrame(values) for field, values in raw.items()}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_topk_provider(root / "provider", {symbol: quote_frame(wide, symbol) for symbol in symbols})
            qlib.init(provider_uri=root / "provider", region=REG_CN)
            index = pd.MultiIndex.from_product([dates, symbols], names=["datetime", "instrument"])
            score = pd.Series(0.0, index=index)
            # Friday score is consumed on the following Monday open.  The
            # expensive first-ranked name is skipped and five affordable names
            # may be built in the first weekly decision.
            score.loc[(pd.Timestamp("2019-01-04"), "000007")] = 10
            for number in range(1, 6):
                score.loc[(pd.Timestamp("2019-01-04"), f"{number:06d}")] = 9 - number
            score.loc[(pd.Timestamp("2019-01-11"), "000006")] = 20
            strategy = weekly_top5_strategy(score, BASE)
            port, _ = backtest(start_time="2019-01-07", end_time="2019-01-24", strategy=strategy, executor={"class": "SimulatorExecutor", "module_path": "qlib.backtest.executor", "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True}}, benchmark=pd.Series(0.0, index=dates), account=ACCOUNT, exchange_kwargs={"codes": symbols, "deal_price": "$open", "trade_unit": 100, "limit_threshold": ("$limit_buy", "$limit_sell"), **BASE})
            weekly = [event for event in strategy.weekly_events if event["kind"] in {"initial", "weekly"}]
            self.assertEqual(weekly[0]["session"], "2019-01-07")
            self.assertEqual(weekly[0]["buys"], 5)
            self.assertTrue(any(event["kind"] == "weekly" and event["sells"] <= 1 and event["buys"] <= 1 for event in weekly[1:]))
            self.assertGreaterEqual(port["1day"][0]["cash"].min(), 5_000.0 - 1e-8)
            self.assertGreater(strategy.high_price_skips, 0)
