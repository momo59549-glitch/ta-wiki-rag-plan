from datetime import date
from math import nan
import unittest

from packages.research.gen3_policy import (
    Availability,
    DataAvailabilityReport,
    DataClass,
    DataSourceAuditRecord,
    Gen3PolicyDraft,
    default_gen3_policy_draft,
)


class Gen3PolicyTests(unittest.TestCase):
    def test_default_policy_is_a_valid_non_binding_draft(self) -> None:
        draft = default_gen3_policy_draft()

        self.assertIs(draft.is_formal_contract, False)
        self.assertIs(draft.is_immutable, False)
        self.assertEqual(draft.candidate_trial_count, 36)
        self.assertEqual(sum(draft.allocation.values()), 42)
        self.assertEqual(draft.as_dict()["forward_validation_start"], "2026-09-01")


    def test_policy_rejects_an_immutable_or_misallocated_draft(self) -> None:
        with self.assertRaisesRegex(ValueError, "formal immutable"):
            Gen3PolicyDraft(is_immutable=True).validate()

        with self.assertRaisesRegex(ValueError, "allocation"):
            Gen3PolicyDraft(allocation={"technical": 42}).validate()

        with self.assertRaisesRegex(ValueError, "finite"):
            Gen3PolicyDraft(net_annual_return_minimum=nan).validate()

        with self.assertRaisesRegex(ValueError, "trial counts"):
            Gen3PolicyDraft(ledger_used_trials=True).validate()

        with self.assertRaisesRegex(ValueError, "214-used"):
            Gen3PolicyDraft(ledger_remaining_trials=43).validate()


    def test_event_source_needs_point_in_time_timestamps_before_available(self) -> None:
        record = DataSourceAuditRecord(
            source_id="cninfo",
            data_class=DataClass.ANNOUNCEMENTS,
            availability=Availability.AVAILABLE,
            local_path="data/announcements",
            file_format="parquet",
            coverage_start=date(2021, 1, 1),
            coverage_end=date(2021, 12, 31),
            observed_fields=("published_at",),
        )

        with self.assertRaisesRegex(ValueError, "PIT fields"):
            record.validate()


    def test_metadata_report_requires_all_domains_and_has_complete_unverified_scaffold(self) -> None:
        report = DataAvailabilityReport(
            records=(
                DataSourceAuditRecord(
                    source_id="local-news-unverified",
                    data_class=DataClass.NEWS,
                    availability=Availability.UNVERIFIED,
                ),
            )
        )

        with self.assertRaisesRegex(ValueError, "missing required data domains"):
            report.validate()

        scaffold = DataAvailabilityReport.unverified_scaffold()
        scaffold.validate()
        self.assertEqual(set(scaffold.by_class()), set(DataClass))
        self.assertEqual({item.availability for item in scaffold.records}, {Availability.UNVERIFIED})
