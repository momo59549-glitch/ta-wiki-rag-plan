"""Prefect 3 flows around the file-backed domain control plane."""
from __future__ import annotations

from datetime import date, timedelta
import json
import os
from pathlib import Path
from typing import Any

from prefect import flow, task

from packages.market_data import audit_universe_price_coverage, sync_tushare_incremental
from packages.research.case_report import render_case_report
from packages.research.market_summary import aggregate_market_cases, render_market_summary

from .file_runtime import FileControlPlane, authorize_job_paths


@task(retries=2, retry_delay_seconds=[2, 10])
def audit_universe_task(manifest: str, dataset_dirs: list[str], as_of: str, output: str) -> dict[str, Any]:
    result = audit_universe_price_coverage(Path(manifest), tuple(Path(item) for item in dataset_dirs), date.fromisoformat(as_of))
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    return result


@task(retries=1, retry_delay_seconds=2)
def aggregate_cases_task(cases_root: str, output_dir: str) -> dict[str, Any]:
    summary = aggregate_market_cases(Path(cases_root))
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "REPORT.md").write_text(render_market_summary(summary), encoding="utf-8")
    return {"case_count": summary["case_count"], "outcomes_out_of_sample": summary["outcomes_out_of_sample"], "output": str(output)}


@task(retries=1, retry_delay_seconds=2)
def render_case_report_task(case_dir: str) -> dict[str, Any]:
    path = render_case_report(Path(case_dir))
    return {"report": str(path)}


@task(retries=2, retry_delay_seconds=[10, 60])
def sync_market_incremental_task(manifest: str, model_data_root: str, start: str, end: str, project_root: str) -> dict[str, Any]:
    project = Path(project_root)
    return sync_tushare_incremental(
        manifest_path=Path(manifest), output_dataset_dir=Path(model_data_root) / "tushare_incremental_cache",
        checkpoint_path=project / "data" / "tushare_sync" / "incremental.checkpoint.json",
        progress_path=project / "data" / "tushare_sync" / "incremental.progress.json",
        start=date.fromisoformat(start), end=date.fromisoformat(end),
    )


@flow(name="file-control-job", retries=1, retry_delay_seconds=5, log_prints=True)
def run_control_job_flow(job_id: str, control_root: str) -> dict[str, Any]:
    """Execute only allowlisted job kinds; arbitrary commands are forbidden."""
    return execute_control_job(job_id, control_root)


def execute_control_job(job_id: str, control_root: str) -> dict[str, Any]:
    """Execute an allowlisted Job without requiring a Prefect server."""
    control = FileControlPlane(Path(control_root))
    job = control.get_job(job_id)
    if job["status"] == "cancelled":
        return job
    if job["status"] == "queued":
        control.transition_job(job_id, "running")
    elif job["status"] != "running":
        raise ValueError(f"Job 状态不可执行: {job['status']}")
    control.update_progress(job_id, 0, 1, "started")
    try:
        kind = job["kind"]
        control_path = Path(control_root).resolve()
        default_project = control_path.parent.parent if control_path.name == "control" and control_path.parent.name == "data" else control_path.parent
        project_root = Path(os.environ.get("TA_PROJECT_ROOT", str(default_project)))
        model_value = os.environ.get("TA_MODEL_DATA_ROOT")
        payload = authorize_job_paths(kind, job["payload"], project_root=project_root, model_data_root=Path(model_value) if model_value else None)
        if kind == "universe_coverage":
            result = audit_universe_task.fn(payload["manifest"], payload["dataset_dirs"], payload["as_of"], payload["output"])
        elif kind == "aggregate_market_research":
            result = aggregate_cases_task.fn(payload["cases_root"], payload["output_dir"])
        elif kind == "render_case_report":
            result = render_case_report_task.fn(payload["case_dir"])
        elif kind == "sync_market_incremental":
            result = sync_market_incremental_task.fn(payload["manifest"], payload["model_data_root"], payload["start"], payload["end"], payload["project_root"])
        else:
            raise ValueError(f"不支持的 Job kind: {kind}")
        latest = control.get_job(job_id)
        if latest.get("cancel_requested"):
            return control.transition_job(job_id, "cancelled")
        control.update_progress(job_id, 1, 1, "completed")
        return control.transition_job(job_id, "succeeded", result=result)
    except Exception as exc:
        control.transition_job(job_id, "failed", error={"type": type(exc).__name__, "message": str(exc)[:1000]})
        raise


@flow(name="daily-research-operations", log_prints=True)
def daily_operations_flow(project_root: str, model_data_root: str, as_of: str, refresh_market_data: bool = True) -> dict[str, Any]:
    """Refresh the incremental overlay when credentialed, then audit coverage."""
    project = Path(project_root)
    model = Path(model_data_root)
    as_of_date = date.fromisoformat(as_of)
    refresh: dict[str, Any] = {"status": "skipped", "reason": "refresh disabled"}
    if refresh_market_data:
        if os.environ.get("TUSHARE_TOKEN"):
            refresh = sync_market_incremental_task(
                str(project / "data" / "universes" / "a_share_history.jsonl"), str(model),
                (as_of_date - timedelta(days=7)).isoformat(), as_of, str(project),
            )
        else:
            refresh = {"status": "skipped", "reason": "TUSHARE_TOKEN is not set"}
    output = project / "data" / "universes" / f"coverage_{as_of.replace('-', '')}.json"
    coverage = audit_universe_task(
        str(project / "data" / "universes" / "a_share_history.jsonl"),
        [str(model / "trend_cache"), str(model / "tushare_daily_cache"), str(model / "tushare_incremental_cache")],
        as_of, str(output),
    )
    return {"refresh": refresh, "coverage": coverage}
