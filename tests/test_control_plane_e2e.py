import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

import apps.api.main as api
from packages.orchestration.worker import run_worker_once


class ControlPlaneEndToEndTests(unittest.TestCase):
    def test_api_queue_worker_artifact_and_events(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = root / "universe.jsonl"
            manifest.write_text(json.dumps({"symbol": "000001", "active_from": "2020-01-01", "active_to": None}) + "\n", encoding="utf-8")
            prices = root / "prices"
            prices.mkdir()
            (prices / "000001.parquet").touch()
            output = root / "coverage.json"
            api.CONTROL_ROOT = root / "control"
            api.AUDIT_PATH = root / "audit.jsonl"
            previous = os.environ.pop("TA_API_KEY", None)
            try:
                client = TestClient(api.app)
                response = client.post("/api/v1/jobs", json={
                    "kind": "universe_coverage",
                    "payload": {"manifest": str(manifest), "dataset_dirs": [str(prices)], "as_of": "2026-08-05", "output": str(output)},
                    "idempotency_key": "e2e:coverage:20260805",
                }, headers={"X-TA-Actor": "operator-e2e", "X-TA-Role": "operator"})
                self.assertEqual(response.status_code, 201)
                job_id = response.json()["job_id"]
                result = run_worker_once(api.CONTROL_ROOT, "e2e-worker")
                self.assertEqual(result["status"], "succeeded")
                self.assertTrue(output.is_file())
                fetched = client.get(f"/api/v1/jobs/{job_id}").json()
                self.assertEqual(fetched["result"]["status"], "complete")
                event_types = [event["event_type"] for event in client.get(f"/api/v1/jobs/{job_id}/events").json()]
                self.assertEqual(event_types[0], "job.queued")
                self.assertIn("job.running", event_types)
                self.assertIn("job.progress", event_types)
                self.assertEqual(event_types[-1], "job.succeeded")
                self.assertTrue(api.AUDIT_PATH.is_file())
            finally:
                if previous is not None:
                    os.environ["TA_API_KEY"] = previous


if __name__ == "__main__":
    unittest.main()
