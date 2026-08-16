from dataclasses import replace
from datetime import date
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from packages.research.gen3_local_market import make_local_market_contract
from packages.research.gen3_quality import audit_local_market_file
from packages.research.gen3_quality_campaign import build_corpus_snapshot, make_campaign_contract
from packages.research.gen3_quality_run import (
    execute_quality_run,
    _atomic_write_once,
    prepare_quality_run,
    quality_run_status,
    report_data,
    snapshot_data,
)
from scripts.run_gen3_quality_run import main


def _row(day: int, **changes: object) -> dict[str, object]:
    value: dict[str, object] = {"date": date(2026, 1, day), "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100.0}
    value.update(changes); return value


def _write(root: Path, symbol: str, rows: list[dict[str, object]]) -> Path:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover
        raise unittest.SkipTest("pyarrow unavailable") from exc
    path = root / f"{symbol}.parquet"; pq.write_table(pa.Table.from_pylist(rows), path); return path


def _contract(root: Path, source: str = "fixture_market"):
    return make_local_market_contract(source_id=source, root=str(root), date_column="date", open_column="open", high_column="high", low_column="low", close_column="close", volume_column="volume")


class QualityRunTests(unittest.TestCase):
    def _setup(self, root: Path, output: Path, *, rows: int = 2):
        _write(root, "000001", [_row(day) for day in range(2, 2 + rows)])
        _write(root, "000002", [_row(day) for day in range(2, 2 + rows)])
        contract = _contract(root); snapshot = build_corpus_snapshot(contract, max_files=10)
        campaign = make_campaign_contract(snapshot, max_rows_per_file=10, max_issues_per_file=10)
        run = prepare_quality_run(snapshot, campaign, contract, workspace_output_root=output, allowed_output_root=output.parent)
        return contract, snapshot, campaign, run

    def test_prepare_is_write_once_bound_to_allowed_workspace_and_waiting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary); source, output = base / "source", base / "workspace"; source.mkdir()
            contract, snapshot, campaign, run = self._setup(source, output)
            self.assertEqual(quality_run_status(run, contract, allowed_output_root=base).status, "waiting")
            self.assertEqual({item.name for item in run.iterdir()}, {"reports", "snapshot.json", "campaign.json", "run_manifest.json"})
            with self.assertRaisesRegex(ValueError, "write-once"):
                prepare_quality_run(snapshot, campaign, contract, workspace_output_root=output, allowed_output_root=base)
            with self.assertRaisesRegex(ValueError, "escapes"):
                prepare_quality_run(snapshot, campaign, contract, workspace_output_root=base.parent / "escape", allowed_output_root=base)
            with self.assertRaisesRegex(ValueError, "inside source"):
                prepare_quality_run(snapshot, campaign, contract, workspace_output_root=source / "out", allowed_output_root=base)

    def test_two_bounded_runs_rebuild_status_and_never_write_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary); source, output = base / "source", base / "workspace"; source.mkdir()
            contract, _, _, run = self._setup(source, output)
            before = {path.name: path.read_bytes() for path in source.iterdir()}
            first = execute_quality_run(run, contract, allowed_output_root=base, max_files_this_run=1)
            self.assertEqual((first.status, first.completed_files, first.total_files, first.aggregate_hash), ("accumulating", 1, 2, None))
            second = execute_quality_run(run, contract, allowed_output_root=base, max_files_this_run=1)
            self.assertEqual((second.status, second.completed_files, second.total_files), ("complete_admitted", 2, 2))
            self.assertIsNotNone(second.aggregate_hash)
            self.assertEqual({path.name: path.read_bytes() for path in source.iterdir()}, before)
            self.assertEqual([path.name for path in sorted((run / "reports").iterdir())], ["000001.json", "000002.json"])

    def test_complete_bad_or_truncated_run_is_blocked_not_admitted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary); source, output = base / "source", base / "workspace"; source.mkdir()
            _write(source, "000001", [_row(2, open=12.0, high=11.0), _row(3)])
            contract = _contract(source); snapshot = build_corpus_snapshot(contract, max_files=10)
            campaign = make_campaign_contract(snapshot, max_rows_per_file=10, max_issues_per_file=10)
            run = prepare_quality_run(snapshot, campaign, contract, workspace_output_root=output, allowed_output_root=base)
            self.assertEqual(execute_quality_run(run, contract, allowed_output_root=base, max_files_this_run=1).status, "complete_blocked")
            truncated_campaign = make_campaign_contract(snapshot, max_rows_per_file=1, max_issues_per_file=10)
            truncated_run = prepare_quality_run(snapshot, truncated_campaign, contract, workspace_output_root=output, allowed_output_root=base)
            self.assertEqual(execute_quality_run(truncated_run, contract, allowed_output_root=base, max_files_this_run=1).status, "complete_blocked")

    def test_tampered_metadata_or_report_and_wrong_symbol_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary); source, output = base / "source", base / "workspace"; source.mkdir()
            contract, snapshot, _, run = self._setup(source, output)
            snapshot_path = run / "snapshot.json"; raw = json.loads(snapshot_path.read_text(encoding="utf-8")); raw["source_id"] = "tampered"
            snapshot_path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "snapshot_hash"):
                quality_run_status(run, contract, allowed_output_root=base)
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary); source, output = base / "source", base / "workspace"; source.mkdir()
            contract, snapshot, _, run = self._setup(source, output)
            foreign = audit_local_market_file(snapshot.files[1].file_path, contract, 10, 10)
            (run / "reports" / "000001.json").write_text(json.dumps(report_data(foreign)), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "bind snapshot"):
                quality_run_status(run, contract, allowed_output_root=base)

    def test_orphan_tmp_residual_lock_and_run_path_escape_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary); source, output = base / "source", base / "workspace"; source.mkdir()
            contract, _, _, run = self._setup(source, output)
            (run / "reports" / "000001.json.tmp").write_text("orphan", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "orphan"):
                execute_quality_run(run, contract, allowed_output_root=base, max_files_this_run=1)
            (run / "reports" / "000001.json.tmp").unlink()
            (run / ".execute.lock").write_text("residual", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "residual"):
                quality_run_status(run, contract, allowed_output_root=base)
            with self.assertRaisesRegex(ValueError, "residual"):
                execute_quality_run(run, contract, allowed_output_root=base, max_files_this_run=1)
            (run / ".execute.lock").unlink()
            with self.assertRaisesRegex(ValueError, "escapes"):
                quality_run_status(run, contract, allowed_output_root=base / "other-workspace")

    def test_direct_object_schema_and_complete_report_hash_tamper_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary); source, output = base / "source", base / "workspace"; source.mkdir()
            contract, snapshot, campaign, run = self._setup(source, output)
            with self.assertRaisesRegex(ValueError, "snapshot_hash"):
                replace(snapshot, source_id="other").verify()
            execute_quality_run(run, contract, allowed_output_root=base, max_files_this_run=2)
            report_file = run / "reports" / "000001.json"; raw = json.loads(report_file.read_text(encoding="utf-8")); raw["report_hash"] = "sha256:" + "0" * 64
            report_file.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "report_hash"):
                quality_run_status(run, contract, allowed_output_root=base)
            with self.assertRaisesRegex(ValueError, "max_files_this_run"):
                execute_quality_run(run, contract, allowed_output_root=base, max_files_this_run=True)

    def test_cli_prepare_status_execute_confirmation_and_no_source_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary); source, output = base / "source", base / "workspace"; source.mkdir(); path = _write(source, "000001", [_row(2), _row(3)])
            config = base / "contract.json"; config.write_text(json.dumps({"source_id": "fixture_market", "root": str(source), "date_column": "date", "open_column": "open", "high_column": "high", "low_column": "low", "close_column": "close", "volume_column": "volume"}), encoding="utf-8")
            before = path.read_bytes(); stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(["prepare", str(config), "--workspace-output-root", str(output), "--allowed-output-root", str(base), "--max-files", "10", "--max-rows-per-file", "10", "--max-issues-per-file", "10"])
            self.assertEqual(code, 0); prepared = json.loads(stdout.getvalue()); run = prepared["run_dir"]
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(["execute", str(config), "--run-dir", run, "--allowed-output-root", str(base), "--max-files-this-run", "1"])
            self.assertEqual(code, 2); self.assertEqual(json.loads(stderr.getvalue())["status"], "blocked")
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(["execute", str(config), "--run-dir", run, "--allowed-output-root", str(base), "--max-files-this-run", "1", "--confirm-read-source"])
            self.assertEqual(code, 0); self.assertEqual(json.loads(stdout.getvalue())["status"], "complete_admitted")
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(["status", str(config), "--run-dir", run, "--allowed-output-root", str(base)])
            self.assertEqual(code, 0); self.assertEqual(json.loads(stdout.getvalue())["status"], "complete_admitted")
            self.assertEqual(path.read_bytes(), before); self.assertNotIn("Traceback", stderr.getvalue())

    def test_primary_atomic_publish_error_is_not_masked_by_cleanup_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "target.json"
            with patch("packages.research.gen3_quality_run.os.link", side_effect=RuntimeError("primary link failure")), patch("packages.research.gen3_quality_run.Path.unlink", side_effect=OSError("cleanup failure")):
                with self.assertRaisesRegex(RuntimeError, "primary link failure"):
                    _atomic_write_once(target, b"{}")
            temporary_path = target.with_name("target.json.tmp")
            if temporary_path.exists():
                temporary_path.unlink()

    def test_execute_rechecks_single_file_footer_before_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary); source, output = base / "source", base / "workspace"; source.mkdir()
            contract, _, _, run = self._setup(source, output)
            _write(source, "000001", [_row(2), _row(3), _row(4)])
            with self.assertRaisesRegex(ValueError, "footer no longer matches snapshot"):
                execute_quality_run(run, contract, allowed_output_root=base, max_files_this_run=1)
            self.assertEqual(list((run / "reports").iterdir()), [])
