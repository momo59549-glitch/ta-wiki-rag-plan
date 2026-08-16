from datetime import date, datetime, time, timezone
import unittest
from zoneinfo import ZoneInfo

from packages.research.gen3_pit import PITRecord, assert_unique_identities, compute_effective_session, make_pit_record


def _dt(day: int, hour: int, tz: object = ZoneInfo("Asia/Shanghai")) -> datetime:
    return datetime(2026, 1, day, hour, 0, tzinfo=tz)  # type: ignore[arg-type]


def _record(**changes: object) -> PITRecord:
    fields: dict[str, object] = {
        "source": "cninfo", "source_record_id": "notice-1", "published_at": _dt(5, 14),
        "available_at": _dt(5, 14), "effective_session": date(2026, 1, 6),
        "revision_id": "v1", "content_hash": "sha256:" + "a" * 64,
        "ingested_at": _dt(5, 15), "symbol_mapping_version": "symbols-v1",
    }
    fields.update(changes)
    return make_pit_record(**fields)  # type: ignore[arg-type]


class Gen3PitTests(unittest.TestCase):
    sessions = (date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 8))

    def test_before_and_after_cutoff_both_wait_until_next_session(self) -> None:
        self.assertEqual(compute_effective_session(self.sessions, _dt(5, 10), _dt(5, 10)), date(2026, 1, 6))
        self.assertEqual(compute_effective_session(self.sessions, _dt(5, 18), _dt(5, 18)), date(2026, 1, 6))

    def test_weekend_holiday_and_utc_convert_to_market_date(self) -> None:
        self.assertEqual(compute_effective_session((date(2026, 1, 5), date(2026, 1, 8)), _dt(3, 9), _dt(3, 9)), date(2026, 1, 5))
        utc_time = datetime(2026, 1, 5, 16, 30, tzinfo=timezone.utc)
        self.assertEqual(compute_effective_session(self.sessions, utc_time, utc_time), date(2026, 1, 7))

    def test_effective_session_must_be_after_available_local_knowledge_date(self) -> None:
        available_utc = datetime(2026, 1, 5, 16, 30, tzinfo=timezone.utc)  # Jan 6 Shanghai
        with self.assertRaisesRegex(ValueError, "strictly after"):
            _record(available_at=available_utc, ingested_at=datetime(2026, 1, 5, 17, 0, tzinfo=timezone.utc))
        record = _record(
            available_at=available_utc,
            ingested_at=datetime(2026, 1, 5, 17, 0, tzinfo=timezone.utc),
            effective_session=date(2026, 1, 7),
        )
        record.verify()

    def test_invalid_calendar_time_and_cutoff_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "no future"):
            compute_effective_session((date(2026, 1, 5),), _dt(5, 10), _dt(5, 10))
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            compute_effective_session((date(2026, 1, 6), date(2026, 1, 6)), _dt(5, 10), _dt(5, 10))
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            compute_effective_session(self.sessions, datetime(2026, 1, 5, 10), _dt(5, 10))
        with self.assertRaisesRegex(ValueError, "cutoff"):
            compute_effective_session(self.sessions, _dt(5, 10), _dt(5, 10), cutoff=time(15, tzinfo=timezone.utc))

    def test_time_order_hash_tamper_and_strict_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "timestamps"):
            _record(available_at=_dt(5, 13))
        with self.assertRaisesRegex(ValueError, "sha256"):
            _record(content_hash="bad")
        record = _record()
        tampered = PITRecord(**{**record.__dict__, "revision_id": "v2"})
        with self.assertRaisesRegex(ValueError, "record_hash"):
            tampered.verify()
        with self.assertRaisesRegex(ValueError, "whitelist"):
            PITRecord.from_mapping({**record.__dict__, "unexpected": "no"})

    def test_duplicate_identity_is_rejected(self) -> None:
        record = _record()
        with self.assertRaisesRegex(ValueError, "duplicate PIT identity"):
            assert_unique_identities((record, record))

    def test_conflicting_content_for_one_revision_is_rejected(self) -> None:
        first = _record()
        second = _record(content_hash="sha256:" + "b" * 64)
        with self.assertRaisesRegex(ValueError, "conflicting content for revision"):
            assert_unique_identities((first, second))

    def test_hash_uses_utc_for_equivalent_datetime_offsets(self) -> None:
        local = _record()
        utc = _record(
            published_at=datetime(2026, 1, 5, 6, 0, tzinfo=timezone.utc),
            available_at=datetime(2026, 1, 5, 6, 0, tzinfo=timezone.utc),
            ingested_at=datetime(2026, 1, 5, 7, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(local.record_hash, utc.record_hash)
