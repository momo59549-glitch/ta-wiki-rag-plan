from datetime import date
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from packages.research.gen3_acquisition import ACQUISITION_SCHEMA_VERSION, AcquisitionPlan, SourceAcquisitionSpec, build_dry_run_plan, make_spec, readiness
from packages.research.gen3_policy import DataClass
from scripts.run_gen3_acquisition import main


def _spec(domain: DataClass = DataClass.MARKET, **changes: object):
    fields = dict(domain=domain, provider="local_parquet", endpoint_or_dataset_id="fixture", credential_env_name=None, start=date(2020, 1, 1), end=date(2020, 1, 2), expected_mapping_hash="sha256:" + "a" * 64, supports_historical_revisions=True, supports_published_at=True, supports_available_at=True, supports_effective_session=True, supports_content_hash=True, write_mode="write_once_raw_snapshot", max_records=10, max_bytes=100, license_note="fixture", terms_url="https://example.com/terms", schema_version=ACQUISITION_SCHEMA_VERSION)
    fields.update(changes); return make_spec(**fields)


class AcquisitionTests(unittest.TestCase):
    def test_hash_tamper_pit_readiness_and_no_directory_write(self) -> None:
        spec = _spec()
        self.assertEqual(readiness(spec), (True, None))
        with self.assertRaisesRegex(ValueError, "spec_hash"):
            type(spec)(**{**spec.__dict__, "max_bytes": 101}).verify()
        blocked = _spec(DataClass.NEWS, supports_content_hash=False)
        self.assertFalse(readiness(blocked)[0])
        with tempfile.TemporaryDirectory() as temporary:
            allowed, target = Path(temporary), Path(temporary) / "dry-run-target"
            plan = build_dry_run_plan((spec,), target_root=target, allowed_root=allowed, max_records=10, max_bytes=100)
            self.assertFalse(target.exists())
            self.assertEqual(plan.status, "dry_run_only")

    def test_secret_path_escape_and_budget_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "dataset identifier"):
            _spec(endpoint_or_dataset_id="https://x/?token=secret")
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "escapes"):
                build_dry_run_plan((_spec(),), target_root=Path(temporary).parent, allowed_root=temporary, max_records=10, max_bytes=100)
            with self.assertRaisesRegex(ValueError, "budget"):
                build_dry_run_plan((_spec(),), target_root=Path(temporary) / "x", allowed_root=temporary, max_records=9, max_bytes=100)

    def test_identifier_terms_credential_and_make_spec_guards(self) -> None:
        for identifier in ("https://x", "name@host", "x=secret", "has space"):
            with self.assertRaisesRegex(ValueError, "dataset identifier"):
                _spec(endpoint_or_dataset_id=identifier)
        for terms in ("http://example.com", "https://u:p@example.com", "https://example.com/?q=x", "https://example.com/#x"):
            with self.assertRaisesRegex(ValueError, "terms_url"):
                _spec(terms_url=terms)
        self.assertEqual(_spec(credential_env_name="TUSHARE_TOKEN").credential_env_name, "TUSHARE_TOKEN")
        with self.assertRaisesRegex(ValueError, "credential_env_name"):
            _spec(credential_env_name="A" * 65)
        with self.assertRaisesRegex(ValueError, "must not supply"):
            make_spec(**{**_spec().__dict__})

    def test_direct_plan_construction_and_cli_are_fail_closed_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); target = root / "target"; plan = build_dry_run_plan((_spec(),), target_root=target, allowed_root=root, max_records=10, max_bytes=100)
            with self.assertRaisesRegex(ValueError, "escapes"):
                AcquisitionPlan(**{**plan.__dict__, "allowed_root": root / "other"}).verify()
            with self.assertRaisesRegex(ValueError, "budget"):
                AcquisitionPlan(**{**plan.__dict__, "caller_max_records": 9}).verify()
            with self.assertRaisesRegex(ValueError, "duplicate"):
                AcquisitionPlan(**{**plan.__dict__, "specs": (plan.specs[0], plan.specs[0])}).verify()
            spec_json = root / "spec.json"
            payload = {**_spec().__dict__, "domain": "market", "start": "2020-01-01", "end": "2020-01-02"}
            spec_json.write_text(json.dumps({"specs": [payload]}), encoding="utf-8")
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main([str(spec_json), "--target-root", str(target), "--allowed-root", str(root), "--max-records", "10", "--max-bytes", "100"])
            self.assertEqual(code, 0); self.assertEqual(json.loads(stdout.getvalue())["status"], "dry_run_only"); self.assertFalse(target.exists()); self.assertEqual(stderr.getvalue(), "")
            spec_json.write_text(json.dumps({"specs": [], "extra": True}), encoding="utf-8")
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main([str(spec_json), "--target-root", str(target), "--allowed-root", str(root), "--max-records", "10", "--max-bytes", "100"])
            self.assertEqual(code, 2); self.assertEqual(json.loads(stderr.getvalue())["status"], "blocked"); self.assertNotIn("Traceback", stderr.getvalue())
