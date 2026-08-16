import unittest
from datetime import date

from scripts.run_survivorship_gap_recovery import compatible_price_basis
from scripts.run_survivorship_coverage_audit import RetrospectiveMember, classify_member_year


class GapRecoveryTests(unittest.TestCase):
    def test_constant_ohlc_scale_is_compatible(self):
        trend = {date(2019, 1, 2): (1, 2, 1, 2, 10), date(2019, 1, 3): (2, 3, 1, 2, 10)}
        candidate = {stamp: tuple(value * 10 if index < 4 else value for index, value in enumerate(row)) for stamp, row in trend.items()}
        # Minimum overlap is deliberately production-sized; repeat deterministic dates here.
        trend = {date(2019, 1, 2).fromordinal(date(2019, 1, 2).toordinal() + index): row for index, row in enumerate([trend[date(2019, 1, 2)]] * 20)}
        candidate = {stamp: tuple(value * 10 if index < 4 else value for index, value in enumerate(row)) for stamp, row in trend.items()}
        self.assertTrue(compatible_price_basis(trend, candidate)["accepted"])

    def test_time_varying_scale_is_rejected(self):
        trend = {date(2019, 1, 2).fromordinal(date(2019, 1, 2).toordinal() + index): (1, 2, 1, 2, 1) for index in range(20)}
        candidate = {stamp: tuple(value * (2 if index else 3) if field < 4 else value for field, value in enumerate(row)) for index, (stamp, row) in enumerate(trend.items())}
        self.assertFalse(compatible_price_basis(trend, candidate)["accepted"])

    def test_ipo_and_delist_expected_intervals_are_not_gap_rows(self):
        calendar = {date(2019, 1, 2), date(2019, 1, 3), date(2019, 1, 4)}
        ipo = RetrospectiveMember("000001", date(2019, 1, 3), None, "L")
        delist = RetrospectiveMember("000002", date(2010, 1, 1), date(2019, 1, 3), "D")
        self.assertTrue(classify_member_year(ipo, 2019, {date(2019, 1, 3), date(2019, 1, 4)}, calendar)["adequate"])
        self.assertTrue(classify_member_year(delist, 2019, {date(2019, 1, 2), date(2019, 1, 3)}, calendar)["adequate"])
