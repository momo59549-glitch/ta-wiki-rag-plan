from datetime import date
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from packages.market_data import active_on, load_point_in_time_universe, load_universe_memberships


class PointInTimeUniverseTests(unittest.TestCase):
    def test_selects_only_symbols_active_on_requested_date(self):
        with TemporaryDirectory() as temp:
            manifest = Path(temp) / "universe.jsonl"
            manifest.write_text("\n".join([
                json.dumps({"symbol": "000001", "active_from": "2020-01-01", "source": "test"}),
                json.dumps({"symbol": "000002", "active_from": "2020-01-01", "active_to": "2021-01-01", "source": "test"}),
            ]), encoding="utf-8")
            symbols, metadata = load_point_in_time_universe(manifest, date(2022, 1, 1))
            self.assertEqual(symbols, ["000001"])
            self.assertEqual(metadata["status"], "point_in_time")

    def test_membership_lookup_is_evaluated_for_each_observation_date(self):
        with TemporaryDirectory() as temp:
            manifest = Path(temp) / "universe.jsonl"
            manifest.write_text(json.dumps({"symbol": "000002", "active_from": "2020-01-01", "active_to": "2021-01-01", "source": "test"}), encoding="utf-8")
            memberships = load_universe_memberships(manifest)
            self.assertTrue(active_on(memberships, "000002", date(2021, 1, 1)))
            self.assertFalse(active_on(memberships, "000002", date(2021, 1, 2)))


if __name__ == "__main__":
    unittest.main()
