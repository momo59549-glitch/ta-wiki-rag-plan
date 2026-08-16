from dataclasses import replace
from datetime import date
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from packages.research.gen3_local_market import make_local_market_contract
from packages.research.gen3_quality import _hash, _report_payload, audit_local_market_file
from packages.research.gen3_quality_campaign import (
    AggregatedCampaignReport,
    CampaignContract,
    CorpusSnapshot,
    aggregate_campaign_reports,
    audit_snapshot_file,
    build_corpus_snapshot,
    make_campaign_contract,
    run_campaign,
    verify_aggregated_campaign_report,
)
from scripts.run_gen3_quality_campaign import main


def _row(day: int, **changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "date": date(2026, 1, day), "open": 10.0, "high": 11.0,
        "low": 9.0, "close": 10.5, "volume": 100.0,
    }
    values.update(changes)
    return values


def _write(root: Path, symbol: str, rows: list[dict[str, object]]) -> Path:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover
        raise unittest.SkipTest("pyarrow unavailable") from exc
    path = root / f"{symbol}.parquet"
    pq.write_table(pa.Table.from_pylist(rows), path)
    return path


def _contract(root: Path, source_id: str = "fixture_market"):
    return make_local_market_contract(
        source_id=source_id, root=str(root), date_column="date", open_column="open",
        high_column="high", low_column="low", close_column="close", volume_column="volume",
    )


class QualityCampaignTests(unittest.TestCase):
    def _snapshot_campaign(self, root: Path, *, rows: int = 2):
        _write(root, "000001", [_row(day) for day in range(2, 2 + rows)])
        _write(root, "000002", [_row(day) for day in range(2, 2 + rows)])
        contract = _contract(root)
        snapshot = build_corpus_snapshot(contract, max_files=10)
        return contract, snapshot, make_campaign_contract(snapshot, max_rows_per_file=10, max_issues_per_file=10)

    def test_non_recursive_strict_files_limit_and_footer_snapshot_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write(root, "000001", [_row(2)])
            nested = root / "nested"; nested.mkdir(); _write(nested, "000002", [_row(2)])
            before = {path: path.read_bytes() for path in root.glob("*.parquet")}
            snapshot = build_corpus_snapshot(_contract(root), max_files=1)
            self.assertEqual(len(snapshot.files), 1)
            self.assertEqual(snapshot.files[0].num_rows, 1)
            self.assertEqual({path: path.read_bytes() for path in root.glob("*.parquet")}, before)
            self.assertEqual(set(root.iterdir()), {root / "000001.parquet", nested})
            with self.assertRaisesRegex(ValueError, "max_files"):
                build_corpus_snapshot(_contract(root), max_files=True)
            with self.assertRaisesRegex(ValueError, "max_files"):
                build_corpus_snapshot(_contract(root), max_files=5_001)
            _write(root, "000003", [_row(2)])
            with self.assertRaisesRegex(ValueError, "exceeds"):
                build_corpus_snapshot(_contract(root), max_files=1)

    def test_bad_direct_parquet_name_and_snapshot_tamper_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write(root, "000001", [_row(2)])
            _write(root, "wrong", [_row(2)])
            with self.assertRaisesRegex(ValueError, "six-digit"):
                build_corpus_snapshot(_contract(root), max_files=10)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract, snapshot, _ = self._snapshot_campaign(root)
            with self.assertRaisesRegex(ValueError, "row group total|snapshot_hash"):
                replace(snapshot, files=(replace(snapshot.files[0], num_rows=3), snapshot.files[1])).verify()
            with self.assertRaisesRegex(ValueError, "row group total"):
                replace(snapshot.files[0], num_rows=3).verify(root=Path(contract.root).resolve())
            with self.assertRaisesRegex(ValueError, "campaign"):
                CampaignContract(**{**make_campaign_contract(snapshot, max_rows_per_file=10, max_issues_per_file=10).__dict__, "max_rows_per_file": 11}).verify()

    def test_clean_full_campaign_is_admitted_and_direct_verification_recomputes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            contract, snapshot, campaign = self._snapshot_campaign(Path(temporary))
            report = run_campaign(snapshot, campaign, contract)
            self.assertEqual(report.status, "admitted")
            self.assertEqual((report.total_rows_scanned, report.total_valid_rows, report.total_issues_encountered), (4, 4, 0))
            report.verify(snapshot=snapshot, campaign=campaign)
            verify_aggregated_campaign_report(report, snapshot=snapshot, campaign=campaign, contract=contract)
            with self.assertRaisesRegex(ValueError, "counts|aggregate_hash"):
                replace(report, total_valid_rows=3).verify(snapshot=snapshot, campaign=campaign)

    def test_aggregate_rejects_missing_duplicate_mixed_and_size_mismatch_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as other_temporary:
            contract, snapshot, campaign = self._snapshot_campaign(Path(temporary))
            reports = tuple(audit_local_market_file(entry.file_path, contract, 10, 10) for entry in snapshot.files)
            with self.assertRaisesRegex(ValueError, "complete"):
                aggregate_campaign_reports(snapshot, campaign, contract, reports[:1])
            with self.assertRaisesRegex(ValueError, "complete"):
                aggregate_campaign_reports(snapshot, campaign, contract, (reports[0], reports[0]))
            foreign = audit_local_market_file(snapshot.files[0].file_path, _contract(Path(temporary), "other_source"), 10, 10)
            with self.assertRaisesRegex(ValueError, "mixed"):
                aggregate_campaign_reports(snapshot, campaign, contract, (foreign, reports[1]))
            with self.assertRaisesRegex(ValueError, "size|report_hash"):
                aggregate_campaign_reports(snapshot, campaign, contract, (replace(reports[0], file_size=reports[0].file_size + 1), reports[1]))

    def test_bad_or_truncated_reports_are_blocked_with_quarantine(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write(root, "000001", [_row(2, open=12.0, high=11.0), _row(3)])
            contract = _contract(root)
            snapshot = build_corpus_snapshot(contract, max_files=10)
            campaign = make_campaign_contract(snapshot, max_rows_per_file=10, max_issues_per_file=10)
            bad = run_campaign(snapshot, campaign, contract)
            self.assertEqual(bad.status, "blocked_with_quarantine")
            self.assertEqual(bad.total_issues_encountered, 1)
            self.assertEqual(bad.retained_issue_code_counts, (("ohlc_bounds", 1),))
            truncation = make_campaign_contract(snapshot, max_rows_per_file=1, max_issues_per_file=10)
            truncated = run_campaign(snapshot, truncation, contract)
            self.assertEqual(truncated.status, "blocked_with_quarantine")
            self.assertTrue(truncated.reports[0].truncated)

    def test_aggregate_cannot_admit_a_rehashed_invalid_row_partition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            contract, snapshot, campaign = self._snapshot_campaign(Path(temporary))
            reports = tuple(audit_local_market_file(entry.file_path, contract, 10, 10) for entry in snapshot.files)
            forged_without_hash = replace(reports[0], valid_rows=1, report_hash="sha256:" + "0" * 64)
            forged = replace(forged_without_hash, report_hash=_hash(_report_payload(forged_without_hash)))
            with self.assertRaisesRegex(ValueError, "exactly one valid row"):
                aggregate_campaign_reports(snapshot, campaign, contract, (forged, reports[1]))

    def test_single_snapshot_file_is_explicit_and_cannot_expand_or_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            contract, snapshot, campaign = self._snapshot_campaign(Path(temporary))
            report = audit_snapshot_file(snapshot, campaign, contract, snapshot.files[0].file_path)
            self.assertEqual(report.symbol, "000001")
            with self.assertRaisesRegex(ValueError, "in the supplied corpus"):
                audit_snapshot_file(snapshot, campaign, contract, Path(temporary) / "not-in-snapshot.parquet")

    def test_cli_metadata_plan_and_explicit_one_file_audit_do_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = _write(root, "000001", [_row(2), _row(3)])
            config = root.parent / "contract.json"
            config.write_text(json.dumps({"source_id": "fixture_market", "root": str(root), "date_column": "date", "open_column": "open", "high_column": "high", "low_column": "low", "close_column": "close", "volume_column": "volume"}), encoding="utf-8")
            before = path.read_bytes(); children = set(root.iterdir())
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main([str(config), "--max-files", "10", "--max-rows-per-file", "10", "--max-issues-per-file", "10"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["status"], "metadata_plan")
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(path.read_bytes(), before); self.assertEqual(set(root.iterdir()), children)
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main([str(config), "--max-files", "10", "--max-rows-per-file", "10", "--max-issues-per-file", "10", "--audit-file", str(path)])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["status"], "single_file_audit")
            self.assertEqual(path.read_bytes(), before); self.assertEqual(set(root.iterdir()), children)
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main([str(config), "--max-files", "0", "--max-rows-per-file", "10", "--max-issues-per-file", "10"])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(stderr.getvalue())["status"], "blocked")
            self.assertNotIn("Traceback", stderr.getvalue())
