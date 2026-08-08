"""Run one queued file-control job under Prefect."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.orchestration.prefect_flows import run_control_job_flow
from packages.orchestration.prefect_runtime import ensure_local_prefect_no_proxy


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    parser.add_argument("--control-root", default="data/control")
    args = parser.parse_args()
    ensure_local_prefect_no_proxy()
    run_control_job_flow(args.job_id, args.control_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
