from pathlib import Path
from tempfile import TemporaryDirectory
import os
import unittest

from fastapi.testclient import TestClient

import apps.api.main as api


class ApiControlPlaneTests(unittest.TestCase):
    def test_job_lifecycle_endpoints_and_sse(self):
        with TemporaryDirectory() as temp:
            api.CONTROL_ROOT = Path(temp) / "control"
            api.AUDIT_PATH = Path(temp) / "audit.jsonl"
            client = TestClient(api.app)
            payload = {"manifest": "universe.jsonl", "dataset_dirs": ["prices"], "as_of": "2026-08-05", "output": "coverage.json"}
            created = client.post("/api/v1/jobs", json={"kind": "universe_coverage", "payload": payload, "idempotency_key": "sync:1"})
            self.assertEqual(created.status_code, 201)
            job_id = created.json()["job_id"]
            self.assertEqual(client.get(f"/api/v1/jobs/{job_id}").json()["status"], "queued")
            self.assertEqual(client.post(f"/api/v1/jobs/{job_id}/cancel").json()["status"], "cancelled")
            events = client.get(f"/api/v1/jobs/{job_id}/events").json()
            self.assertGreaterEqual(len(events), 2)
            stream = client.get(f"/api/v1/jobs/{job_id}/events/stream")
            self.assertIn("event: job.queued", stream.text)
            self.assertTrue(api.AUDIT_PATH.is_file())

    def test_api_key_and_role_are_enforced(self):
        with TemporaryDirectory() as temp:
            api.CONTROL_ROOT = Path(temp) / "control"
            api.AUDIT_PATH = Path(temp) / "audit.jsonl"
            previous = os.environ.get("TA_API_KEY")
            os.environ["TA_API_KEY"] = "secret-test-key"
            try:
                client = TestClient(api.app)
                self.assertEqual(client.get("/healthz").status_code, 401)
                headers = {"X-TA-API-Key": "secret-test-key", "X-TA-Actor": "viewer", "X-TA-Role": "viewer"}
                payload = {"manifest": "universe.jsonl", "dataset_dirs": ["prices"], "as_of": "2026-08-05", "output": "coverage.json"}
                self.assertEqual(client.post("/api/v1/jobs", json={"kind": "universe_coverage", "payload": payload}, headers=headers).status_code, 403)
                headers.update({"X-TA-Actor": "operator-1", "X-TA-Role": "operator"})
                self.assertEqual(client.post("/api/v1/jobs", json={"kind": "universe_coverage", "payload": payload}, headers=headers).status_code, 201)
                self.assertEqual(client.post("/api/v1/jobs", json={"kind": "arbitrary_command", "payload": {}}, headers=headers).status_code, 422)
            finally:
                if previous is None:
                    os.environ.pop("TA_API_KEY", None)
                else:
                    os.environ["TA_API_KEY"] = previous


if __name__ == "__main__":
    unittest.main()
