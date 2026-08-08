from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from packages.orchestration import FileControlPlane, JobError


class FileControlPlaneTests(unittest.TestCase):
    def test_job_progress_cancel_and_event_idempotency(self):
        with TemporaryDirectory() as temp:
            control = FileControlPlane(Path(temp))
            first = control.create_job("research", {"rule": "hammer"}, idempotency_key="run:1")
            duplicate = control.create_job("research", {"rule": "hammer"}, idempotency_key="run:1")
            self.assertEqual(first["job_id"], duplicate["job_id"])
            control.transition_job(first["job_id"], "running")
            control.update_progress(first["job_id"], 2, 10, "scanner")
            cancelling = control.request_cancel(first["job_id"])
            self.assertEqual(cancelling["status"], "cancelling")
            cancelled = control.transition_job(first["job_id"], "cancelled")
            self.assertEqual(cancelled["status"], "cancelled")
            ids = [item["event_id"] for item in control.job_events(first["job_id"])]
            self.assertEqual(len(ids), len(set(ids)))

    def test_illegal_transition_and_dead_letter(self):
        with TemporaryDirectory() as temp:
            control = FileControlPlane(Path(temp))
            job = control.create_job("sync", {})
            with self.assertRaises(JobError):
                control.transition_job(job["job_id"], "succeeded")
            event = control.publish_event("data.ready", {"snapshot": "x"}, idempotency_key="data:x")
            self.assertEqual(control.publish_event("data.ready", {"snapshot": "x"}, idempotency_key="data:x")["event_id"], event["event_id"])
            self.assertEqual(control.dead_letter(event["event_id"], "boom")["error"], "boom")


if __name__ == "__main__":
    unittest.main()
