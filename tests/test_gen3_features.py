import unittest

from packages.research.gen3_features import (
    FEATURE_SCHEMA_VERSION, FeatureDependency, FeatureRegistry, FeatureSpec, assert_no_future_dependencies,
    build_registry, make_feature_spec,
)
from packages.research.gen3_policy import DataClass


def _dependency(domain: DataClass, fields: tuple[str, ...], index: str = "a") -> FeatureDependency:
    availability = "session" if domain in (DataClass.MARKET, DataClass.TRADABILITY) else "effective_from" if domain == DataClass.INDEX_CONSTITUENTS else "effective_session"
    required = set(fields) | {availability}
    if domain in (DataClass.FUNDAMENTALS, DataClass.ANNOUNCEMENTS, DataClass.NEWS):
        required |= {"source_record_id", "effective_session", "content_hash", "revision_id"}
    return FeatureDependency(domain, "sha256:" + index * 64, tuple(sorted(required)), availability)


def _spec(feature_id: str, family: str, dependencies: tuple[FeatureDependency, ...]) -> FeatureSpec:
    return make_feature_spec(feature_id=feature_id, version="v1", family=family, dependencies=dependencies, lookback_sessions=20, min_observations=10, output_dtype="float", null_policy="reject", transform_id="identity", transform_version="v1")


class Gen3FeatureTests(unittest.TestCase):
    def test_valid_families_and_control_are_pure_memory_contracts(self) -> None:
        specs = (
            _spec("technical_one", "technical", (_dependency(DataClass.MARKET, ("close", "session", "symbol")),)),
            _spec("factor_one", "single_factor", (_dependency(DataClass.FUNDAMENTALS, ("symbol", "value")),)),
            _spec("event_one", "announcement_event", (_dependency(DataClass.ANNOUNCEMENTS, ("event_type", "symbol")),)),
            _spec("news_one", "news", (_dependency(DataClass.NEWS, ("content", "symbol", "title")),)),
            _spec("control_one", "control", (_dependency(DataClass.TRADABILITY, ("can_buy", "session", "symbol")),)),
        )
        registry = build_registry(specs)
        self.assertEqual(len(registry.specs), 5)
        self.assertTrue(registry.registry_hash.startswith("sha256:"))

    def test_dependency_field_availability_and_lag_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside"):
            _dependency(DataClass.MARKET, ("published_at",)).validate()
        with self.assertRaisesRegex(ValueError, "availability"):
            FeatureDependency(DataClass.MARKET, "sha256:" + "a" * 64, ("close",), "effective_session").validate()
        with self.assertRaisesRegex(ValueError, "lag_sessions"):
            FeatureDependency(DataClass.MARKET, "sha256:" + "a" * 64, ("close", "session"), "session", -1).validate()
        with self.assertRaisesRegex(ValueError, "lag_sessions"):
            FeatureDependency(DataClass.MARKET, "sha256:" + "a" * 64, ("close", "session"), "session", True).validate()

    def test_dependency_requires_availability_and_pit_provenance(self) -> None:
        with self.assertRaisesRegex(ValueError, "availability_field"):
            FeatureDependency(DataClass.MARKET, "sha256:" + "a" * 64, ("close",), "session").validate()
        with self.assertRaisesRegex(ValueError, "provenance"):
            FeatureDependency(DataClass.NEWS, "sha256:" + "a" * 64, ("effective_session", "title"), "effective_session").validate()

    def test_family_and_observation_constraints_fail_closed(self) -> None:
        market = _dependency(DataClass.MARKET, ("close",))
        with self.assertRaisesRegex(ValueError, "must depend on fundamentals"):
            _spec("bad_factor", "single_factor", (market,))
        announcement = _dependency(DataClass.ANNOUNCEMENTS, ("event_type",))
        with self.assertRaisesRegex(ValueError, "market or tradability"):
            _spec("bad_control", "control", (announcement,))
        index = _dependency(DataClass.INDEX_CONSTITUENTS, ("effective_from", "index_symbol"))
        with self.assertRaisesRegex(ValueError, "require market or tradability"):
            _spec("bad_index_only", "technical", (index,))
        news = _dependency(DataClass.NEWS, ("title",))
        with self.assertRaisesRegex(ValueError, "forbidden"):
            _spec("bad_event_mixed", "announcement_event", (announcement, news))
        fundamental = _dependency(DataClass.FUNDAMENTALS, ("value",))
        with self.assertRaisesRegex(ValueError, "forbidden"):
            _spec("bad_news_mixed", "news", (news, fundamental))
        with self.assertRaisesRegex(ValueError, "min_observations"):
            make_feature_spec(feature_id="bad_observations", version="v1", family="technical", dependencies=(market,), lookback_sessions=5, min_observations=6, output_dtype="float", null_policy="reject", transform_id="identity", transform_version="v1")

    def test_hash_whitelist_and_registry_duplicates_fail_closed(self) -> None:
        first = _spec("same_identity", "technical", (_dependency(DataClass.MARKET, ("close",)),))
        tampered = FeatureSpec(**{**first.__dict__, "transform_id": "other"})
        with self.assertRaisesRegex(ValueError, "feature_hash"):
            tampered.verify()
        with self.assertRaisesRegex(ValueError, "whitelist"):
            FeatureSpec.from_mapping({**first.__dict__, "extra": "no"})
        with self.assertRaisesRegex(ValueError, "duplicate feature identity"):
            build_registry((first, first))
        conflict = _spec("same_identity", "technical", (_dependency(DataClass.MARKET, ("close",), "b"),))
        with self.assertRaisesRegex(ValueError, "conflicting feature identity"):
            build_registry((first, conflict))

    def test_registry_hash_is_input_order_independent_and_no_future_hook_verifies(self) -> None:
        first = _spec("a_feature", "technical", (_dependency(DataClass.MARKET, ("close",)),))
        second = _spec("b_feature", "technical", (_dependency(DataClass.MARKET, ("close",), "b"),))
        self.assertEqual(build_registry((first, second)).registry_hash, build_registry((second, first)).registry_hash)
        assert_no_future_dependencies(first)
        self.assertEqual(first.schema_version, FEATURE_SCHEMA_VERSION)

    def test_direct_registry_construction_fails_closed_for_empty_unsorted_and_duplicate(self) -> None:
        first = _spec("a_feature", "technical", (_dependency(DataClass.MARKET, ("close",)),))
        second = _spec("b_feature", "technical", (_dependency(DataClass.MARKET, ("close",), "b"),))
        valid = build_registry((first, second))
        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            FeatureRegistry((), valid.registry_hash).verify()
        with self.assertRaisesRegex(ValueError, "canonically sorted"):
            FeatureRegistry((second, first), valid.registry_hash).verify()
        with self.assertRaisesRegex(ValueError, "duplicate feature identity"):
            FeatureRegistry((first, first), valid.registry_hash).verify()
        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            build_registry(())
