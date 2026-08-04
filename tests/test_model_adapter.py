import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from packages.integrations import ModelExperimentAdapter, ModelExperimentImportError


class ModelExperimentAdapterTests(unittest.TestCase):
    def test_imports_legacy_configuration_with_audit_manifest(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            payload = {
                "name": "Baseline-等权5因子", "start_date": "20190101",
                "end_date": "20251231", "top_n": 5, "factors": {"momentum": {"weight": 0.2}},
            }
            (root / "baseline.json").write_text(json.dumps(payload), encoding="utf-8")
            imported = ModelExperimentAdapter(root).import_file("baseline.json")
            self.assertEqual(imported.artifact_type, "experiment_configuration")
            self.assertEqual(imported.configuration["top_n"], 5)
            manifest = imported.manifest()
            self.assertEqual(manifest["source_path"], "baseline.json")
            self.assertEqual(len(manifest["source_sha256"]), 64)
            self.assertNotIn("raw_payload", manifest)

    def test_imports_result_and_rejects_path_escape(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "result.json").write_text('{"total_return": -12.0, "max_drawdown": -21.3}', encoding="utf-8")
            adapter = ModelExperimentAdapter(root)
            self.assertEqual(adapter.import_file("result.json").artifact_type, "backtest_result")
            with self.assertRaises(ModelExperimentImportError):
                adapter.import_file("../outside.json")


if __name__ == "__main__":
    unittest.main()
