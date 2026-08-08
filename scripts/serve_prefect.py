"""Serve the daily file-MVP health deployment with Prefect 3."""
from __future__ import annotations

from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.orchestration.prefect_flows import daily_operations_flow
from packages.orchestration.prefect_runtime import ensure_local_prefect_no_proxy


if __name__ == "__main__":
    ensure_local_prefect_no_proxy()
    daily_operations_flow.serve(name="daily-research-operations", cron="0 18 * * 1-5")
