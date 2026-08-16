import tempfile
import unittest
from pathlib import Path

from packages.research.gen3_inventory import InventoryLimits, inspect_domains, inspect_root
from packages.research.gen3_policy import Availability, DataClass


class Gen3InventoryTests(unittest.TestCase):
    def test_missing_root_is_missing_and_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "does-not-exist"
            observation = inspect_root(DataClass.NEWS, root)

            self.assertFalse(root.exists())
            self.assertEqual(observation.availability, Availability.MISSING)
            self.assertEqual(observation.sample_paths, ())

    def test_listing_stops_at_configured_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index in range(3):
                (root / f"sample-{index}.csv").touch()

            observation = inspect_root(
                DataClass.MARKET,
                root,
                limits=InventoryLimits(max_entries_per_root=2, max_sample_files=1),
            )

            self.assertEqual(observation.entry_count_observed, 2)
            self.assertTrue(observation.entry_limit_reached)
            self.assertEqual(len(observation.sample_paths), 1)
            self.assertEqual(observation.availability, Availability.PARTIAL)

    def test_domain_inventory_includes_all_six_without_promoting_sources(self) -> None:
        domains = inspect_domains({DataClass.ANNOUNCEMENTS: "not-a-real-root"})

        self.assertEqual(set(domains), set(DataClass))
        self.assertEqual(domains[DataClass.ANNOUNCEMENTS].availability, Availability.MISSING)
        self.assertEqual(domains[DataClass.FUNDAMENTALS].availability, Availability.MISSING)

    def test_local_sample_is_only_partial_and_policy_record_stays_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "event.csv").touch()

            observation = inspect_root(DataClass.ANNOUNCEMENTS, root)
            record = observation.as_audit_record()

            self.assertEqual(observation.availability, Availability.PARTIAL)
            self.assertEqual(record.availability, Availability.UNVERIFIED)
            record.validate()

    def test_invalid_limit_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive non-boolean"):
            InventoryLimits(max_entries_per_root=True).validate()

    def test_recursive_inventory_is_prohibited(self) -> None:
        with self.assertRaisesRegex(ValueError, "recursive inventory is prohibited"):
            InventoryLimits(recursive=True).validate()
