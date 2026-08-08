import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from packages.orchestration.prefect_flows import audit_universe_task, daily_operations_flow


class PrefectFlowTests(unittest.TestCase):
    def test_daily_health_flow_writes_coverage_artifact(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            model = root / "model"
            manifest = project / "data" / "universes" / "a_share_history.jsonl"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"symbol": "000001", "active_from": "2020-01-01", "active_to": None}), encoding="utf-8")
            (model / "trend_cache").mkdir(parents=True)
            (model / "tushare_daily_cache").mkdir(parents=True)
            (model / "trend_cache" / "000001.parquet").touch()
            # Exercise the task body without Prefect's ephemeral server.  The
            # deployment/server lifecycle is verified by the deployment
            # runbook; unit tests must remain offline and deterministic.
            output = project / "data" / "universes" / "coverage_20260805.json"
            result = audit_universe_task.fn(str(manifest), [str(model / "trend_cache"), str(model / "tushare_daily_cache")], "2026-08-05", str(output))
            self.assertEqual(result["status"], "complete")
            self.assertTrue(output.is_file())
            self.assertEqual(daily_operations_flow.name, "daily-research-operations")


if __name__ == "__main__":
    unittest.main()
