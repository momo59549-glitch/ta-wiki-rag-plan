from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from packages.orchestration.file_runtime import JobError, authorize_job_paths


class JobPathPolicyTests(unittest.TestCase):
    def test_allows_project_outputs_and_read_only_model_inputs(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            project, model = root / "project", root / "model"
            project.mkdir(); model.mkdir()
            payload = {"manifest": "data/universe.jsonl", "dataset_dirs": [str(model / "trend_cache")], "as_of": "2026-08-05", "output": "data/coverage.json"}
            normalized = authorize_job_paths("universe_coverage", payload, project_root=project, model_data_root=model)
            self.assertEqual(Path(normalized["output"]), project / "data" / "coverage.json")
            self.assertEqual(Path(normalized["dataset_dirs"][0]), model / "trend_cache")

    def test_rejects_path_escape_and_unconfigured_model_root(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            project = root / "project"; project.mkdir()
            with self.assertRaises(JobError):
                authorize_job_paths("render_case_report", {"case_dir": str(root / "outside")}, project_root=project)
            payload = {"manifest": "data/universe.jsonl", "model_data_root": str(root / "model"), "start": "2026-08-01", "end": "2026-08-05", "project_root": "."}
            with self.assertRaises(JobError):
                authorize_job_paths("sync_market_incremental", payload, project_root=project)


if __name__ == "__main__":
    unittest.main()
