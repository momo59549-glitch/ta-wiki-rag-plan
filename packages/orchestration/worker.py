"""Polling worker for the file control plane; no database or broker required."""
from __future__ import annotations

from pathlib import Path
from threading import Event, Thread
import time
from typing import Any, Callable

from .file_runtime import FileControlPlane
from .prefect_flows import execute_control_job


def run_worker_once(control_root: Path, worker_id: str, *, lease_seconds: int = 300, executor: Callable[[str, str], dict[str, Any]] = execute_control_job) -> dict[str, Any] | None:
    control = FileControlPlane(control_root)
    job = control.claim_next_job(worker_id, lease_seconds=lease_seconds)
    if job is None:
        return None
    stop_heartbeat = Event()

    def heartbeat() -> None:
        interval = max(0.25, lease_seconds / 3)
        while not stop_heartbeat.wait(interval):
            try:
                control.renew_claim(job["job_id"], worker_id, lease_seconds=lease_seconds)
            except (FileNotFoundError, ValueError):
                return

    thread = Thread(target=heartbeat, name=f"lease-{job['job_id']}", daemon=True)
    thread.start()
    try:
        return executor(job["job_id"], str(control_root))
    finally:
        stop_heartbeat.set()
        thread.join(timeout=1)
        control.release_claim(job["job_id"], worker_id)


def run_worker_forever(control_root: Path, worker_id: str, *, poll_seconds: float = 2.0, lease_seconds: int = 300) -> None:
    if poll_seconds <= 0:
        raise ValueError("poll_seconds 必须大于 0")
    while True:
        result = run_worker_once(control_root, worker_id, lease_seconds=lease_seconds)
        if result is None:
            time.sleep(poll_seconds)
