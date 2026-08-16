from types import SimpleNamespace
import unittest

from scripts.run_qlib_cross_section_holdout import HOLDOUT_N, ordered_holdout_selection


class CrossSectionHoldoutSelectionTests(unittest.TestCase):
    def test_next_coverage_eligible_symbols_are_disjoint_and_not_outcome_selected(self):
        members = tuple(f"{number:06d}" for number in range(405))
        entries = tuple(SimpleNamespace(symbol=symbol, selected_rows=1, min_session=__import__("datetime").date(2015, 1, 5), max_session=__import__("datetime").date(2021, 12, 31)) for symbol in members)
        frozen = SimpleNamespace(members=members, trend_entries=entries)
        # The production selector delegates the original first-200 selection to
        # fixed_symbols; inject its expected shape by monkeypatching locally.
        import scripts.run_qlib_cross_section_holdout as module
        previous = module.fixed_symbols
        module.fixed_symbols = lambda _: members[:HOLDOUT_N]
        try:
            selection = ordered_holdout_selection(frozen)
        finally:
            module.fixed_symbols = previous
        self.assertEqual(selection["holdout_symbols"], list(members[HOLDOUT_N:HOLDOUT_N * 2]))
        self.assertFalse(set(selection["original_symbols"]) & set(selection["holdout_symbols"]))
        self.assertEqual(selection["coverage_exclusions"], [])
