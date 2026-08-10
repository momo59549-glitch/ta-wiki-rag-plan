import contextlib
from datetime import datetime, timezone
import importlib.util
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from packages.research.candidate_comparison import FIXED_RULES


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_candidate_comparison.py"


def _load_cli():
    spec = importlib.util.spec_from_file_location("candidate_comparison_cli_smoke", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CandidateComparisonCliTests(unittest.TestCase):
    def test_module_import_and_help(self):
        module = _load_cli()
        with patch.object(sys, "argv", [str(SCRIPT), "--help"]), contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as raised:
                module.main()
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("build-panel", output.getvalue())

    def test_build_panel_uses_fixed_rule_slots_without_fixed_case_ids(self):
        module = _load_cli()
        with TemporaryDirectory() as temp:
            root = Path(temp); cases = {}
            for name in FIXED_RULES:
                cases[name] = root / name; cases[name].mkdir()
                (cases[name] / "dataset_snapshot_manifest.json").write_text(json.dumps({"symbols": ["000001"]}), encoding="utf-8")
            panel_dir = root / "panel"; manifest = panel_dir / "panel_manifest.json"
            identities = {name: {**FIXED_RULES[name], "case_id": f"new_case_{name}", "protocol_id": f"new_protocol_{name}",
                          "dataset_snapshot_id": "sha256:data", "oos_start": "2020-01-01", "oos_end": "2020-12-31", "lockbox_start": "2021-01-01"} for name in FIXED_RULES}
            def validate(path): return identities[path.name]
            def build(source, snapshot, symbols, **kwargs):
                panel_dir.mkdir(); manifest.write_text(json.dumps({"panel_id": "sha256:panel"}), encoding="utf-8"); return manifest
            argv = [str(SCRIPT), "build-panel", "--rsi-case", str(cases["rsi"]), "--roc-case", str(cases["roc"]),
                    "--breakdown-case", str(cases["breakdown"]), "--model-data", str(root / "model"), "--panel-dir", str(panel_dir)]
            with patch.object(sys, "argv", argv), patch.object(module, "validate_completed_case", side_effect=validate), \
                    patch.object(module, "CompositeParquetMarketData", return_value=object()), patch.object(module, "build_comparison_panel", side_effect=build), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(module.main(), 0)
            self.assertTrue(manifest.is_file())

    def test_run_prints_datetime_result_and_exits_zero(self):
        module = _load_cli(); completed = datetime(2026, 8, 9, 12, 34, 56, tzinfo=timezone.utc)
        output = io.StringIO()
        argv = [str(SCRIPT), "run", "--protocol", "protocol.json", "--output", "result.json"]
        with patch.object(sys, "argv", argv), patch.object(module, "run_comparison", return_value={"status": "completed", "completed_at": completed}), contextlib.redirect_stdout(output):
            self.assertEqual(module.main(), 0)
        rendered = json.loads(output.getvalue())
        self.assertEqual(rendered, {"status": "completed", "completed_at": str(completed)})


if __name__ == "__main__":
    unittest.main()
