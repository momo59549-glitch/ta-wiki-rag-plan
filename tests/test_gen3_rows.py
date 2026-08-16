from datetime import date, datetime
from math import nan
import unittest
from zoneinfo import ZoneInfo

from packages.research.gen3_policy import DataClass
from packages.research.gen3_providers import CANONICAL_FIELDS, PROVIDER_SCHEMA_VERSION, SourceFieldMapping
from packages.research.gen3_rows import CanonicalRow, DraftSourceManifest, build_draft_source_manifest, canonicalize_and_validate_row


def _mapping(domain: DataClass, source: str = "fixture") -> SourceFieldMapping:
    return SourceFieldMapping.from_mapping(source_id=source, domain=domain, schema_version=PROVIDER_SCHEMA_VERSION, root="fixture", file_format="parquet", mapping={key: f"raw_{key}" for key in CANONICAL_FIELDS[domain]})


def _row(domain: DataClass) -> dict[str, object]:
    tz = ZoneInfo("Asia/Shanghai")
    values: dict[str, object] = {
        "symbol": "000001", "session": date(2026, 1, 6), "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100,
        "is_st": False, "is_suspended": False, "is_limit_up": False, "is_limit_down": False, "can_buy": True, "can_sell": True,
        "index_symbol": "000300", "constituent_symbol": "000001", "effective_from": date(2026, 1, 6), "effective_to": None,
        "source_record_id": "rec-1", "published_at": datetime(2026, 1, 5, 10, tzinfo=tz), "available_at": datetime(2026, 1, 5, 10, tzinfo=tz), "effective_session": date(2026, 1, 6), "revision_id": "v1", "content_hash": "sha256:" + "a" * 64, "ingested_at": datetime(2026, 1, 5, 11, tzinfo=tz), "symbol_mapping_version": "v1", "value_name": "roe", "value": 1.2, "event_type": "repurchase", "title": "title", "content": "content",
    }
    return {f"raw_{key}": values[key] for key in CANONICAL_FIELDS[domain]}


class Gen3RowTests(unittest.TestCase):
    def test_all_domains_validate_and_pit_binds_record_hash(self) -> None:
        for domain in DataClass:
            row = canonicalize_and_validate_row(_mapping(domain), _row(domain))
            row.verify()
            self.assertEqual(row.domain, domain)
            self.assertEqual(row.pit_record_hash is not None, domain in {DataClass.FUNDAMENTALS, DataClass.ANNOUNCEMENTS, DataClass.NEWS})

    def test_market_and_tradability_constraints_fail_closed(self) -> None:
        market = _row(DataClass.MARKET); market["raw_high"] = 8.0
        with self.assertRaisesRegex(ValueError, "OHLC"):
            canonicalize_and_validate_row(_mapping(DataClass.MARKET), market)
        market = _row(DataClass.MARKET); market["raw_open"] = nan
        with self.assertRaisesRegex(ValueError, "finite"):
            canonicalize_and_validate_row(_mapping(DataClass.MARKET), market)
        market = _row(DataClass.MARKET); market["raw_volume"] = True
        with self.assertRaisesRegex(ValueError, "finite"):
            canonicalize_and_validate_row(_mapping(DataClass.MARKET), market)
        trade = _row(DataClass.TRADABILITY); trade["raw_is_suspended"] = True
        with self.assertRaisesRegex(ValueError, "suspended"):
            canonicalize_and_validate_row(_mapping(DataClass.TRADABILITY), trade)
        trade = _row(DataClass.TRADABILITY); trade["raw_is_limit_up"] = True
        with self.assertRaisesRegex(ValueError, "limit-up"):
            canonicalize_and_validate_row(_mapping(DataClass.TRADABILITY), trade)
        trade = _row(DataClass.TRADABILITY); trade["raw_is_limit_up"] = True; trade["raw_is_limit_down"] = True; trade["raw_can_buy"] = False; trade["raw_can_sell"] = False
        with self.assertRaisesRegex(ValueError, "both limit"):
            canonicalize_and_validate_row(_mapping(DataClass.TRADABILITY), trade)

    def test_index_pit_payload_and_extra_source_columns(self) -> None:
        index = _row(DataClass.INDEX_CONSTITUENTS); index["raw_effective_to"] = date(2026, 1, 5)
        with self.assertRaisesRegex(ValueError, "effective_to"):
            canonicalize_and_validate_row(_mapping(DataClass.INDEX_CONSTITUENTS), index)
        news = _row(DataClass.NEWS); news["raw_title"] = " "
        with self.assertRaisesRegex(ValueError, "title"):
            canonicalize_and_validate_row(_mapping(DataClass.NEWS), news)
        base = _row(DataClass.FUNDAMENTALS); base["ignored"] = "one"
        first = canonicalize_and_validate_row(_mapping(DataClass.FUNDAMENTALS), base)
        base["ignored"] = "two"
        self.assertEqual(first.row_hash, canonicalize_and_validate_row(_mapping(DataClass.FUNDAMENTALS), base).row_hash)
        base["raw_value"] = 2.0
        self.assertNotEqual(first.row_hash, canonicalize_and_validate_row(_mapping(DataClass.FUNDAMENTALS), base).row_hash)

    def test_manifest_is_order_independent_and_direct_tampering_fails(self) -> None:
        mapping = _mapping(DataClass.MARKET)
        first = canonicalize_and_validate_row(mapping, _row(DataClass.MARKET))
        raw = _row(DataClass.MARKET); raw["raw_session"] = date(2026, 1, 7)
        second = canonicalize_and_validate_row(mapping, raw)
        manifest = build_draft_source_manifest(mapping, (first, second))
        self.assertEqual(manifest.manifest_hash, build_draft_source_manifest(mapping, (second, first)).manifest_hash)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            build_draft_source_manifest(mapping, (first, first))
        with self.assertRaisesRegex(ValueError, "empty"):
            build_draft_source_manifest(mapping, ())
        with self.assertRaisesRegex(ValueError, "count"):
            DraftSourceManifest(**{**manifest.__dict__, "record_count": 3}).verify()
        with self.assertRaisesRegex(ValueError, "row_hash"):
            CanonicalRow(**{**first.__dict__, "row_hash": "sha256:" + "0" * 64}).verify()
        with self.assertRaisesRegex(ValueError, "mix source"):
            build_draft_source_manifest(mapping, (first, canonicalize_and_validate_row(_mapping(DataClass.TRADABILITY), _row(DataClass.TRADABILITY))))
