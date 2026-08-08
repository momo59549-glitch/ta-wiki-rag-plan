from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from packages.orchestration.file_runtime import FileControlPlane
from packages.orchestration.worker import run_worker_once


class FileWorkerTests(unittest.TestCase):
    def test_claims_and_executes_oldest_job_once(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            control = FileControlPlane(root)
            first = control.create_job("test", {})
            control.create_job("test", {})

            def executor(job_id, control_root):
                plane = FileControlPlane(Path(control_root))
                plane.update_progress(job_id, 1, 1, "done")
                return plane.transition_job(job_id, "succeeded", result={"ok": True})

            result = run_worker_once(root, "worker-a", executor=executor)
            self.assertEqual(result["job_id"], first["job_id"])
            self.assertEqual(result["status"], "succeeded")
            self.assertFalse((root / "claims" / f"{first['job_id']}.json").exists())

    def test_expired_claim_is_requeued_and_reclaimed(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            control = FileControlPlane(root)
            job = control.create_job("test", {})
            control.claim_next_job("dead-worker", lease_seconds=300)
            claim_path = root / "claims" / f"{job['job_id']}.json"
            claim = json.loads(claim_path.read_text(encoding="utf-8"))
            claim["lease_expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            claim_path.write_text(json.dumps(claim), encoding="utf-8")
            reclaimed = control.claim_next_job("new-worker", lease_seconds=300)
            self.assertEqual(reclaimed["job_id"], job["job_id"])
            self.assertEqual(reclaimed["attempt"], 2)


if __name__ == "__main__":
    unittest.main()
