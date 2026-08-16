import unittest
from datetime import date

from scripts.run_survivorship_coverage_audit import RetrospectiveMember, classify_member_year, classify_year


CALENDAR = {date(2019, 1, 2), date(2019, 1, 3), date(2019, 1, 4), date(2019, 1, 7)}


class SurvivorshipCoverageAuditTests(unittest.TestCase):
    def test_ipo_partial_year_is_structural_when_expected_interval_is_covered(self):
        member = RetrospectiveMember("000001", date(2019, 1, 4), None, "L")
        row = classify_member_year(member, 2019, {date(2019, 1, 4), date(2019, 1, 7)}, CALENDAR)
        self.assertEqual(row["expected_sessions"], 2)
        self.assertTrue(row["adequate"])
        self.assertEqual(row["category"], "structural_partial_due_listing_or_delisting")

    def test_delist_partial_year_is_structural_when_expected_interval_is_covered(self):
        member = RetrospectiveMember("000002", date(2010, 1, 1), date(2019, 1, 3), "D")
        row = classify_member_year(member, 2019, {date(2019, 1, 2), date(2019, 1, 3)}, CALENDAR)
        self.assertTrue(row["adequate"])
        self.assertEqual(row["category"], "structural_partial_due_listing_or_delisting")

    def test_internal_gap_is_not_misclassified_as_listing_or_delisting(self):
        member = RetrospectiveMember("000003", date(2010, 1, 1), None, "L")
        row = classify_member_year(member, 2019, {date(2019, 1, 2), date(2019, 1, 7)}, CALENDAR)
        self.assertEqual(row["internal_missing_sessions"], 2)
        self.assertEqual(row["category"], "unexplained_internal_gaps")
        self.assertFalse(row["adequate"])

    def test_zero_coverage_is_explicit_and_blocks_threshold(self):
        members = (
            RetrospectiveMember("000001", date(2010, 1, 1), None, "L"),
            RetrospectiveMember("000002", date(2019, 1, 4), None, "L"),
        )
        report = classify_year(members, 2019, {"000001": CALENDAR}, CALENDAR)
        self.assertEqual(report["zero_coverage_members"], 1)
        self.assertEqual(report["structural_partial_due_listing_or_delisting"], [])
        self.assertEqual(report["nonactive_or_delisted_members"], 0)

    def test_first_and_last_gaps_are_reported_separately(self):
        member = RetrospectiveMember("000004", date(2010, 1, 1), None, "L")
        row = classify_member_year(member, 2019, {date(2019, 1, 3)}, CALENDAR)
        self.assertEqual((row["first_gap_sessions"], row["last_gap_sessions"], row["internal_missing_sessions"]), (1, 2, 0))
        self.assertEqual(row["category"], "unexplained_edge_gaps")
