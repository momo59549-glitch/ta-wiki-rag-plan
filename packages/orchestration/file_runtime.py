"""File-backed job and event control plane for the no-Postgres MVP."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import time
from threading import Lock
from typing import Any
from uuid import uuid4

from packages.research.json_store import write_json, write_jsonl


class JobError(ValueError):
    pass


ALLOWED_JOB_KINDS = frozenset({"universe_coverage", "aggregate_market_research", "render_case_report", "sync_market_incremental"})
JOB_REQUIRED_FIELDS = {
    "universe_coverage": {"manifest": str, "dataset_dirs": list, "as_of": str, "output": str},
    "aggregate_market_research": {"cases_root": str, "output_dir": str},
    "render_case_report": {"case_dir": str},
    "sync_market_incremental": {"manifest": str, "model_data_root": str, "start": str, "end": str, "project_root": str},
}


def validate_job_payload(kind: str, payload: dict[str, Any]) -> None:
    if kind not in ALLOWED_JOB_KINDS:
        raise JobError(f"不支持的 Job kind: {kind}")
    if not isinstance(payload, dict):
        raise JobError("Job payload 必须是对象")
    for field, expected in JOB_REQUIRED_FIELDS[kind].items():
        if field not in payload:
            raise JobError(f"{kind} 缺少 payload.{field}")
        if not isinstance(payload[field], expected):
            raise JobError(f"{kind} 的 payload.{field} 类型必须是 {expected.__name__}")
        if expected is str and not payload[field].strip():
            raise JobError(f"{kind} 的 payload.{field} 不能为空")
    if kind == "universe_coverage" and not all(isinstance(item, str) and item.strip() for item in payload["dataset_dirs"]):
        raise JobError("universe_coverage 的 payload.dataset_dirs 必须是非空字符串数组")
    if kind in {"universe_coverage", "sync_market_incremental"}:
        try:
            date_fields = ["as_of"] if kind == "universe_coverage" else ["start", "end"]
            for field in date_fields:
                datetime.fromisoformat(payload[field])
        except ValueError as exc:
            raise JobError(f"{kind} 的日期必须是 ISO 格式") from exc


def authorize_job_paths(kind: str, payload: dict[str, Any], *, project_root: Path, model_data_root: Path | None = None) -> dict[str, Any]:
    """Resolve Job paths and enforce project/model-data filesystem boundaries."""
    validate_job_payload(kind, payload)
    project = project_root.resolve()
    model = model_data_root.resolve() if model_data_root else None
    normalized = dict(payload)

    def resolve(value: str) -> Path:
        path = Path(value)
        return path.resolve() if path.is_absolute() else (project / path).resolve()

    def require_under(path: Path, roots: tuple[Path, ...], field: str) -> str:
        if not any(_is_relative_to(path, root) for root in roots):
            allowed = ", ".join(str(root) for root in roots)
            raise JobError(f"payload.{field} 越出授权目录；允许范围: {allowed}")
        return str(path)

    project_fields = {
        "universe_coverage": ("manifest", "output"),
        "aggregate_market_research": ("cases_root", "output_dir"),
        "render_case_report": ("case_dir",),
        "sync_market_incremental": ("manifest", "project_root"),
    }[kind]
    for field in project_fields:
        normalized[field] = require_under(resolve(payload[field]), (project,), field)
    if kind == "universe_coverage":
        allowed_inputs = (project,) + ((model,) if model else ())
        normalized["dataset_dirs"] = [require_under(resolve(item), allowed_inputs, "dataset_dirs") for item in payload["dataset_dirs"]]
    if kind == "sync_market_incremental":
        if model is None:
            raise JobError("sync_market_incremental 需要配置 TA_MODEL_DATA_ROOT")
        requested_model = resolve(payload["model_data_root"])
        normalized["model_data_root"] = require_under(requested_model, (model,), "model_data_root")
        if requested_model != model:
            raise JobError("payload.model_data_root 必须等于已配置的 TA_MODEL_DATA_ROOT")
    return normalized


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


_JOB_TRANSITIONS = {
    "queued": {"running", "cancelled", "failed"},
    "running": {"queued", "succeeded", "failed", "cancelling"},
    "cancelling": {"cancelled", "failed"},
}


class FileControlPlane:
    _create_lock = Lock()
    _last_created_ns = 0

    def __init__(self, root: Path):
        self.root = root
        self.jobs_dir = root / "jobs"
        self.events_dir = root / "events"
        self.claims_dir = root / "claims"
        self.outbox_path = root / "outbox.jsonl"
        self.dead_letter_path = root / "dead_letter.jsonl"

    def create_job(self, kind: str, payload: dict[str, Any], *, idempotency_key: str | None = None, correlation_id: str | None = None) -> dict[str, Any]:
        if not kind.strip():
            raise JobError("job kind 不能为空")
        if idempotency_key:
            for job in self.list_jobs():
                if job.get("idempotency_key") == idempotency_key:
                    return job
        job_id = "job_" + uuid4().hex
        with self._create_lock:
            created_ns = max(time.time_ns(), self._last_created_ns + 1)
            type(self)._last_created_ns = created_ns
        now = datetime.fromtimestamp(created_ns / 1_000_000_000, timezone.utc).isoformat()
        job = {
            "schema_version": "job/v1", "job_id": job_id, "kind": kind,
            "status": "queued", "progress": {"completed": 0, "total": None, "message": "queued"},
            "payload": payload, "result": None, "error": None,
            "idempotency_key": idempotency_key, "correlation_id": correlation_id or job_id,
            "cancel_requested": False, "created_at": now, "created_ns": created_ns, "updated_at": now,
        }
        self._write_job(job)
        self.publish_event("job.queued", {"kind": kind}, job_id=job_id, correlation_id=job["correlation_id"], idempotency_key=f"{job_id}:queued")
        return job

    def get_job(self, job_id: str) -> dict[str, Any]:
        self._validate_id(job_id, "job_")
        path = self.jobs_dir / f"{job_id}.json"
        if not path.is_file():
            raise FileNotFoundError(job_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def list_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        if limit < 1:
            raise JobError("limit 必须为正整数")
        if not self.jobs_dir.is_dir():
            return []
        jobs = [json.loads(path.read_text(encoding="utf-8")) for path in self.jobs_dir.glob("job_*.json")]
        return sorted(jobs, key=lambda item: item["created_at"], reverse=True)[:limit]

    def claim_next_job(self, worker_id: str, *, lease_seconds: int = 300) -> dict[str, Any] | None:
        """Atomically claim the oldest queued job for one file worker."""
        if not worker_id.strip() or lease_seconds < 1:
            raise JobError("worker_id 不能为空且 lease_seconds 必须为正整数")
        self.requeue_expired_claims()
        self.claims_dir.mkdir(parents=True, exist_ok=True)
        candidates = sorted(self.list_jobs(limit=100_000), key=lambda item: (item["created_at"], item.get("created_ns", 0), item["job_id"]))
        for candidate in candidates:
            if candidate["status"] != "queued":
                continue
            claim_path = self.claims_dir / f"{candidate['job_id']}.json"
            try:
                with claim_path.open("x", encoding="utf-8") as stream:
                    json.dump({"worker_id": worker_id, "claimed_at": _now(), "lease_expires_at": (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()}, stream, ensure_ascii=False, indent=2)
            except FileExistsError:
                continue
            latest = self.get_job(candidate["job_id"])
            if latest["status"] != "queued":
                claim_path.unlink(missing_ok=True)
                continue
            latest["claimed_by"] = worker_id
            latest["lease_expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()
            latest["attempt"] = int(latest.get("attempt", 0)) + 1
            self._write_job(latest)
            return self.transition_job(latest["job_id"], "running")
        return None

    def release_claim(self, job_id: str, worker_id: str) -> None:
        self._validate_id(job_id, "job_")
        claim_path = self.claims_dir / f"{job_id}.json"
        if not claim_path.is_file():
            return
        claim = json.loads(claim_path.read_text(encoding="utf-8"))
        if claim.get("worker_id") != worker_id:
            raise JobError("不能释放其他 worker 的 Job claim")
        claim_path.unlink()

    def renew_claim(self, job_id: str, worker_id: str, *, lease_seconds: int = 300) -> None:
        self._validate_id(job_id, "job_")
        if lease_seconds < 1:
            raise JobError("lease_seconds 必须为正整数")
        claim_path = self.claims_dir / f"{job_id}.json"
        claim = json.loads(claim_path.read_text(encoding="utf-8"))
        if claim.get("worker_id") != worker_id:
            raise JobError("不能续租其他 worker 的 Job claim")
        claim["lease_expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()
        temporary = claim_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(claim, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(claim_path)

    def requeue_expired_claims(self) -> list[str]:
        """Return abandoned running jobs to the queue after their lease expires."""
        if not self.claims_dir.is_dir():
            return []
        now = datetime.now(timezone.utc)
        requeued: list[str] = []
        for claim_path in self.claims_dir.glob("job_*.json"):
            try:
                claim = json.loads(claim_path.read_text(encoding="utf-8"))
                expires = datetime.fromisoformat(claim["lease_expires_at"])
                job = self.get_job(claim_path.stem)
            except (KeyError, ValueError, FileNotFoundError, json.JSONDecodeError):
                continue
            if expires > now:
                continue
            if job["status"] == "running":
                job["claimed_by"] = None
                job["lease_expires_at"] = None
                self._write_job(job)
                self.transition_job(job["job_id"], "queued", error={"type": "WorkerLeaseExpired", "message": "worker lease expired; job requeued"})
                requeued.append(job["job_id"])
            claim_path.unlink(missing_ok=True)
        return requeued

    def transition_job(self, job_id: str, target: str, *, result: dict[str, Any] | None = None, error: dict[str, Any] | None = None) -> dict[str, Any]:
        job = self.get_job(job_id)
        current = job["status"]
        if target == current:
            return job
        if target not in _JOB_TRANSITIONS.get(current, set()):
            raise JobError(f"非法 Job 状态转移: {current} -> {target}")
        job["status"] = target
        job["updated_at"] = datetime.now(timezone.utc).isoformat()
        if result is not None:
            job["result"] = result
        if error is not None:
            job["error"] = error
        self._write_job(job)
        self.publish_event(f"job.{target}", {"result": result, "error": error}, job_id=job_id, correlation_id=job["correlation_id"], idempotency_key=f"{job_id}:{target}")
        return job

    def update_progress(self, job_id: str, completed: int, total: int | None, message: str) -> dict[str, Any]:
        job = self.get_job(job_id)
        if job["status"] not in {"running", "cancelling"}:
            raise JobError("只有运行中的 Job 可以更新进度")
        if completed < 0 or (total is not None and (total < 0 or completed > total)):
            raise JobError("Job 进度非法")
        job["progress"] = {"completed": completed, "total": total, "message": message}
        job["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write_job(job)
        self.publish_event("job.progress", job["progress"], job_id=job_id, correlation_id=job["correlation_id"], idempotency_key=f"{job_id}:progress:{completed}:{total}:{message}")
        return job

    def request_cancel(self, job_id: str) -> dict[str, Any]:
        job = self.get_job(job_id)
        if job["status"] == "queued":
            job["cancel_requested"] = True
            self._write_job(job)
            return self.transition_job(job_id, "cancelled")
        if job["status"] == "running":
            job["cancel_requested"] = True
            self._write_job(job)
            return self.transition_job(job_id, "cancelling")
        if job["status"] in {"cancelling", "cancelled"}:
            return job
        raise JobError(f"Job 已结束，不能取消: {job['status']}")

    def publish_event(self, event_type: str, payload: dict[str, Any], *, job_id: str | None = None, case_id: str | None = None, correlation_id: str | None = None, causation_id: str | None = None, idempotency_key: str | None = None) -> dict[str, Any]:
        existing = self._load_jsonl(self.outbox_path)
        if idempotency_key:
            duplicate = next((item for item in existing if item.get("idempotency_key") == idempotency_key), None)
            if duplicate:
                return duplicate
        occurred_at = datetime.now(timezone.utc).isoformat()
        identity = f"{event_type}|{job_id}|{case_id}|{idempotency_key}|{occurred_at}|{uuid4().hex}"
        event = {
            "schema_version": "event-envelope/v1", "event_id": "evt_" + sha256(identity.encode()).hexdigest()[:24],
            "event_type": event_type, "occurred_at": occurred_at, "job_id": job_id, "case_id": case_id,
            "correlation_id": correlation_id, "causation_id": causation_id, "idempotency_key": idempotency_key,
            "payload": payload, "payload_sha256": "sha256:" + sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "delivery_status": "pending", "delivery_attempts": 0,
        }
        existing.append(event)
        write_jsonl(self.outbox_path, existing)
        if job_id:
            job_events = self._load_jsonl(self.events_dir / f"{job_id}.jsonl")
            job_events.append(event)
            write_jsonl(self.events_dir / f"{job_id}.jsonl", job_events)
        return event

    def job_events(self, job_id: str, after_event_id: str | None = None) -> list[dict[str, Any]]:
        self._validate_id(job_id, "job_")
        events = self._load_jsonl(self.events_dir / f"{job_id}.jsonl")
        if not after_event_id:
            return events
        for index, event in enumerate(events):
            if event["event_id"] == after_event_id:
                return events[index + 1:]
        return events

    def mark_event_delivered(self, event_id: str) -> dict[str, Any]:
        events = self._load_jsonl(self.outbox_path)
        for event in events:
            if event["event_id"] == event_id:
                event["delivery_status"] = "delivered"
                event["delivery_attempts"] += 1
                event["delivered_at"] = datetime.now(timezone.utc).isoformat()
                write_jsonl(self.outbox_path, events)
                return event
        raise FileNotFoundError(event_id)

    def dead_letter(self, event_id: str, error: str) -> dict[str, Any]:
        events = self._load_jsonl(self.outbox_path)
        source = next((item for item in events if item["event_id"] == event_id), None)
        if not source:
            raise FileNotFoundError(event_id)
        letters = self._load_jsonl(self.dead_letter_path)
        letter = {"event": source, "error": error, "failed_at": datetime.now(timezone.utc).isoformat()}
        letters.append(letter)
        write_jsonl(self.dead_letter_path, letters)
        return letter

    def _write_job(self, job: dict[str, Any]) -> None:
        write_json(self.jobs_dir / f"{job['job_id']}.json", job)

    @staticmethod
    def _validate_id(value: str, prefix: str) -> None:
        if not value.startswith(prefix) or not value.removeprefix(prefix).isalnum():
            raise JobError("资源 ID 非法")

    @staticmethod
    def _load_jsonl(path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
