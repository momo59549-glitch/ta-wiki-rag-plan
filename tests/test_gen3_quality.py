from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
import tempfile
import unittest

from packages.research.gen3_local_market import make_local_market_contract
from packages.research.gen3_policy import DataClass
from packages.research.gen3_providers import PROVIDER_SCHEMA_VERSION, SourceFieldMapping
from packages.research.gen3_quality import (
    DataQualityIssue,
    QualityAuditReport,
    _hash,
    _report_payload,
    audit_local_market_file,
    build_replacement_evidence,
    verify_replacement_evidence,
)
from packages.research.gen3_rows import canonicalize_and_validate_row


def _contract(root: Path, source: str = "primary_market"):
    return make_local_market_contract(
        source_id=source, root=str(root), date_column="date", open_column="open",
        high_column="high", low_column="low", close_column="close", volume_column="volume",
    )


def _row(session: object = date(2026, 1, 5), **changes: object) -> dict[str, object]:
    result: dict[str, object] = {
        "date": session, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100.0,
    }
    result.update(changes)
    return result


def _write(root: Path, rows: list[dict[str, object]], symbol: str = "000001") -> Path:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - project dependency expected in CI
        raise unittest.SkipTest("pyarrow unavailable") from exc
    path = root / f"{symbol}.parquet"
    pq.write_table(pa.Table.from_pylist(rows), path)
    return path


def _market_mapping(source: str) -> SourceFieldMapping:
    return SourceFieldMapping.from_mapping(
        source_id=source, domain=DataClass.MARKET, schema_version=PROVIDER_SCHEMA_VERSION,
        root="replacement-fixture", file_format="parquet",
        mapping={"symbol": "symbol", "session": "session", "open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume"},
    )


def _replacement_row(source: str = "independent_market", **changes: object):
    values: dict[str, object] = {
        "symbol": "000001", "session": date(2026, 1, 5), "open": 10.0,
        "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100.0,
    }
    values.update(changes)
    return canonicalize_and_validate_row(_market_mapping(source), values)


class Gen3QualityTests(unittest.TestCase):
    def test_clean_naive_datetime_is_canonicalized_without_source_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = _write(root, [_row(datetime(2026, 1, 5, 9, 30)), _row(datetime(2026, 1, 6, 9, 30))])
            before = path.read_bytes()
            report = audit_local_market_file(path, _contract(root), max_rows=10, max_issues=10)
            report.verify()
            self.assertEqual((report.status, report.valid_rows, report.min_session, report.max_session), ("clean_sample", 2, date(2026, 1, 5), date(2026, 1, 6)))
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(tuple(root.iterdir()), (path,))

    def test_bad_ohlc_isolated_and_later_good_row_is_still_checked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = _write(root, [_row(date(2026, 1, 5), open=12.0, high=11.0), _row(date(2026, 1, 6))])
            report = audit_local_market_file(path, _contract(root), 10, 10)
            self.assertEqual([issue.issue_code for issue in report.issues], ["ohlc_bounds"])
            self.assertEqual(report.valid_rows, 1)
            self.assertEqual(report.min_session, date(2026, 1, 6))
            self.assertEqual(report.status, "quarantined_sample")

    def test_issue_priority_covers_null_nonfinite_bool_price_volume_and_ohlc(self) -> None:
        cases = [
            (_row(date(2026, 1, 5), open=None), "null"),
            (_row(date(2026, 1, 5), open=float("nan")), "non_finite"),
            (_row(date(2026, 1, 5), open=float("inf")), "non_finite"),
            (_row(date(2026, 1, 5), open=True), "non_finite"),
            (_row(date(2026, 1, 5), open=0.0), "non_positive_price"),
            (_row(date(2026, 1, 5), volume=-1.0), "negative_volume"),
            (_row(date(2026, 1, 5), close=12.0), "ohlc_bounds"),
        ]
        for raw, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                report = audit_local_market_file(_write(root, [raw]), _contract(root), 10, 10)
                self.assertEqual(report.issues[0].issue_code, expected)

    def test_duplicate_nonmonotonic_aware_and_string_sessions_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = _write(root, [
                _row(date(2026, 1, 5)), _row(date(2026, 1, 5)), _row(date(2026, 1, 4)),
            ])
            report = audit_local_market_file(path, _contract(root), 10, 10)
            self.assertEqual({issue.issue_code for issue in report.issues}, {"duplicate_session", "non_monotonic_session"})
        for raw in (
            _row(datetime(2026, 1, 5, tzinfo=timezone.utc)),
            _row("2026-01-05"),
        ):
            with self.subTest(raw=raw["date"]), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                report = audit_local_market_file(_write(root, [raw]), _contract(root), 10, 10)
                self.assertEqual(report.issues[0].issue_code, "invalid_session_type")

    def test_session_and_order_errors_beat_numeric_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = _write(root, [
                _row(None, open=None), _row(date(2026, 1, 5), open=None), _row(date(2026, 1, 5), open=None),
            ])
            report = audit_local_market_file(path, _contract(root), 10, 10)
            codes = {issue.row_number: issue.issue_code for issue in report.issues}
            self.assertEqual(codes[1], "null")
            self.assertEqual(codes[2], "null")
            self.assertEqual(codes[3], "duplicate_session")

    def test_extra_source_columns_do_not_change_evidence_or_issue_hash(self) -> None:
        with tempfile.TemporaryDirectory() as first_temp, tempfile.TemporaryDirectory() as second_temp:
            first, second = Path(first_temp), Path(second_temp)
            first_report = audit_local_market_file(_write(first, [_row(open=0.0, extra="one")]), _contract(first), 10, 10)
            second_report = audit_local_market_file(_write(second, [_row(open=0.0, extra="two")]), _contract(second), 10, 10)
            self.assertEqual(first_report.issues[0].evidence_hash, second_report.issues[0].evidence_hash)
            self.assertEqual(first_report.issues[0].issue_hash, second_report.issues[0].issue_hash)

    def test_limits_only_set_flags_on_actual_plus_one_observation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = _write(root, [_row(date(2026, 1, 5), open=0.0), _row(date(2026, 1, 6), open=0.0), _row(date(2026, 1, 7))])
            one = audit_local_market_file(path, _contract(root), max_rows=2, max_issues=1)
            self.assertTrue(one.truncated)
            self.assertTrue(one.truncated_issues)
            self.assertEqual((len(one.issues), one.issues_encountered), (1, 2))
            exact = audit_local_market_file(path, _contract(root), max_rows=3, max_issues=2)
            self.assertFalse(exact.truncated_issues)
            self.assertEqual((len(exact.issues), exact.issues_encountered), (2, 2))
            self.assertFalse(exact.truncated)
            self.assertEqual(exact.status, "quarantined_sample")

    def test_issue_and_report_tampering_or_mixed_attribution_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as other_temporary:
            root, other = Path(temporary), Path(other_temporary)
            report = audit_local_market_file(_write(root, [_row(open=0.0)]), _contract(root), 10, 10)
            other_report = audit_local_market_file(_write(other, [_row(open=0.0)]), _contract(other, "other_market"), 10, 10)
            with self.assertRaisesRegex(ValueError, "details_code|issue_hash"):
                replace(report.issues[0], issue_code="negative_volume").verify()
            with self.assertRaisesRegex(ValueError, "status"):
                replace(report, status="clean_sample").verify()
            mixed = replace(report, rows_scanned=2, issues=tuple(sorted((report.issues[0], other_report.issues[0]), key=lambda issue: issue.issue_hash)), issues_encountered=2)
            with self.assertRaisesRegex(ValueError, "mixed attribution"):
                mixed.verify()

    def test_replacement_evidence_success_and_rejections(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = audit_local_market_file(_write(root, [_row(open=0.0)]), _contract(root), 10, 10)
            issue = report.issues[0]
            replacement = _replacement_row()
            evidence = build_replacement_evidence(
                issue=issue, replacement_source_id="independent_market",
                replacement_content_hash="sha256:" + "b" * 64, replacement_row=replacement,
                observed_at=datetime(2026, 1, 8, 8, tzinfo=timezone.utc),
            )
            verified = verify_replacement_evidence(evidence, issue=issue, replacement_row=replacement)
            verified.verify(issue=issue, replacement_row=replacement)
            self.assertEqual(verified.issue_hash, issue.issue_hash)
            self.assertEqual(report.status, "quarantined_sample")

            cases = [
                ("same source", dict(replacement_source_id="primary_market"), replacement),
                ("wrong session", {}, _replacement_row(session=date(2026, 1, 6))),
                ("wrong symbol", {}, _replacement_row(symbol="000002")),
                ("bad hash", dict(replacement_content_hash="bad"), replacement),
                ("naive observed", dict(observed_at=datetime(2026, 1, 8, 8)), replacement),
            ]
            for label, changes, candidate in cases:
                with self.subTest(label=label):
                    fields = dict(
                        issue=issue, replacement_source_id="independent_market",
                        replacement_content_hash="sha256:" + "b" * 64, replacement_row=candidate,
                        observed_at=datetime(2026, 1, 8, 8, tzinfo=timezone.utc),
                    )
                    fields.update(changes)
                    with self.assertRaises(ValueError):
                        build_replacement_evidence(**fields)

    def test_replacement_rejects_non_market_row_and_direct_evidence_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            issue = audit_local_market_file(_write(root, [_row(open=0.0)]), _contract(root), 10, 10).issues[0]
            tradability_mapping = SourceFieldMapping.from_mapping(
                source_id="independent_market", domain=DataClass.TRADABILITY, schema_version=PROVIDER_SCHEMA_VERSION,
                root="replacement-fixture", file_format="parquet",
                mapping={"symbol": "symbol", "session": "session", "is_st": "is_st", "is_suspended": "is_suspended", "is_limit_up": "is_limit_up", "is_limit_down": "is_limit_down", "can_buy": "can_buy", "can_sell": "can_sell"},
            )
            non_market = canonicalize_and_validate_row(tradability_mapping, {"symbol": "000001", "session": date(2026, 1, 5), "is_st": False, "is_suspended": False, "is_limit_up": False, "is_limit_down": False, "can_buy": True, "can_sell": True})
            with self.assertRaisesRegex(ValueError, "market"):
                build_replacement_evidence(issue=issue, replacement_source_id="independent_market", replacement_content_hash="sha256:" + "b" * 64, replacement_row=non_market, observed_at=datetime(2026, 1, 8, tzinfo=timezone.utc))
            replacement = _replacement_row()
            evidence = build_replacement_evidence(issue=issue, replacement_source_id="independent_market", replacement_content_hash="sha256:" + "b" * 64, replacement_row=replacement, observed_at=datetime(2026, 1, 8, tzinfo=timezone.utc))
            with self.assertRaisesRegex(ValueError, "evidence_hash"):
                replace(evidence, replacement_content_hash="sha256:" + "c" * 64).verify(issue=issue, replacement_row=replacement)

    def test_quality_objects_have_strict_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            issue = audit_local_market_file(_write(root, [_row(open=0.0)]), _contract(root), 10, 10).issues[0]
            values = dict(issue.__dict__)
            with self.assertRaisesRegex(ValueError, "strict schema"):
                DataQualityIssue.from_mapping({**values, "extra": "no"})
            report = audit_local_market_file(_write(root, [_row(date(2026, 1, 6))], "000002"), _contract(root), 10, 10)
            with self.assertRaisesRegex(ValueError, "valid_rows|report_hash"):
                replace(report, valid_rows=2).verify()

    def test_rehashed_row_partition_attack_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = audit_local_market_file(_write(root, [_row(date(2026, 1, 5)), _row(date(2026, 1, 6))]), _contract(root), 10, 10)
            forged_without_hash = replace(report, valid_rows=1, report_hash="sha256:" + "0" * 64)
            forged = replace(forged_without_hash, report_hash=_hash(_report_payload(forged_without_hash)))
            with self.assertRaisesRegex(ValueError, "exactly one valid row"):
                forged.verify()
