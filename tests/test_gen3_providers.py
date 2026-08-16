import tempfile
import unittest
from pathlib import Path

from packages.research.gen3_policy import DataClass
from packages.research.gen3_providers import (
    CANONICAL_FIELDS,
    PROVIDER_SCHEMA_VERSION,
    SCHEMA_COMPATIBLE_NOT_PIT_VERIFIED,
    SourceFieldMapping,
    inspect_parquet_schema,
    legacy_fin_cache_mapping,
    validate_observed_schema,
)


def _mapping(domain: DataClass, **changes: str) -> SourceFieldMapping:
    values = {field: f"raw_{field}" for field in CANONICAL_FIELDS[domain]}
    values.update(changes)
    return SourceFieldMapping.from_mapping(
        source_id="fixture_source", domain=domain, schema_version=PROVIDER_SCHEMA_VERSION,
        root="fixture-root", file_format="parquet", mapping=values,
    )


class Gen3ProviderTests(unittest.TestCase):
    def test_complete_explicit_mapping_is_schema_only_not_pit_available(self) -> None:
        mapping = _mapping(DataClass.ANNOUNCEMENTS)
        admission = validate_observed_schema(mapping, tuple(mapping.mapping.values()))

        self.assertEqual(admission.status, SCHEMA_COMPATIBLE_NOT_PIT_VERIFIED)
        self.assertTrue(admission.row_validation_required)
        self.assertTrue(admission.pit_row_validation_required)

    def test_mapping_rejects_missing_extra_and_duplicate_source_columns(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing"):
            SourceFieldMapping.from_mapping(
                source_id="x", domain=DataClass.MARKET, schema_version=PROVIDER_SCHEMA_VERSION, root="r", file_format="parquet",
                mapping={"symbol": "symbol"},
            )
        values = {field: field for field in CANONICAL_FIELDS[DataClass.MARKET]}
        values["extra"] = "extra"
        with self.assertRaisesRegex(ValueError, "extra"):
            SourceFieldMapping.from_mapping(source_id="x", domain=DataClass.MARKET, schema_version=PROVIDER_SCHEMA_VERSION, root="r", file_format="parquet", mapping=values)
        values = {field: field for field in CANONICAL_FIELDS[DataClass.MARKET]}
        values["high"] = values["open"]
        with self.assertRaisesRegex(ValueError, "multiple canonical"):
            SourceFieldMapping.from_mapping(source_id="x", domain=DataClass.MARKET, schema_version=PROVIDER_SCHEMA_VERSION, root="r", file_format="parquet", mapping=values)

    def test_version_and_format_are_exact_and_mapping_hash_binds_mapping(self) -> None:
        fields = {field: field for field in CANONICAL_FIELDS[DataClass.MARKET]}
        with self.assertRaisesRegex(ValueError, "schema_version"):
            SourceFieldMapping.from_mapping(source_id="x", domain=DataClass.MARKET, schema_version="other/v1", root="r", file_format="parquet", mapping=fields)
        with self.assertRaisesRegex(ValueError, "file_format"):
            SourceFieldMapping.from_mapping(source_id="x", domain=DataClass.MARKET, schema_version=PROVIDER_SCHEMA_VERSION, root="r", file_format="Parquet", mapping=fields)
        first = _mapping(DataClass.MARKET)
        second = _mapping(DataClass.MARKET, close="different_close")
        self.assertNotEqual(first.mapping_hash, second.mapping_hash)

    def test_missing_observed_column_fails_closed(self) -> None:
        mapping = _mapping(DataClass.MARKET)
        with self.assertRaisesRegex(ValueError, "missing mapped"):
            validate_observed_schema(mapping, tuple(mapping.mapping.values())[:-1])

    def test_legacy_finance_mapping_cannot_fabricate_pit_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing"):
            legacy_fin_cache_mapping(root="fixture-root")

    def test_all_pit_domain_mappings_cover_pit_record_fields(self) -> None:
        required = {"source_record_id", "published_at", "available_at", "effective_session", "revision_id", "content_hash", "ingested_at", "symbol_mapping_version"}
        for domain in (DataClass.FUNDAMENTALS, DataClass.ANNOUNCEMENTS, DataClass.NEWS):
            self.assertTrue(required <= set(_mapping(domain).mapping))

    def test_every_domain_requires_row_validation_after_schema_admission(self) -> None:
        for domain in DataClass:
            mapping = _mapping(domain)
            admission = validate_observed_schema(mapping, tuple(mapping.mapping.values()))
            self.assertTrue(admission.row_validation_required)

    def test_parquet_inspector_rejects_directory_nonparquet_and_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "suffix"):
                inspect_parquet_schema(root / "not-parquet.csv")
            with self.assertRaisesRegex(ValueError, "existing file"):
                inspect_parquet_schema(root / "missing.parquet")
            with self.assertRaisesRegex(ValueError, "existing file"):
                inspect_parquet_schema(root)

    def test_parquet_inspector_reads_schema_only_from_explicit_fixture(self) -> None:
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:  # pragma: no cover - project dependency expected in CI
            self.skipTest("pyarrow unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fixture.parquet"
            pq.write_table(pa.table({"raw_symbol": ["000001"], "raw_session": ["2026-01-05"]}), path)
            observation = inspect_parquet_schema(path)

            self.assertEqual(observation.path, path)
            self.assertGreater(observation.size_bytes, 0)
            self.assertEqual(observation.observed_columns, ("raw_symbol", "raw_session"))
