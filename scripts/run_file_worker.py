"""Run the local file-backed Job worker."""
from __future__ import annotations

import argparse
from pathlib import Path
import socket

from packages.orchestration.worker import run_worker_forever, run_worker_once


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control-root", default="data/control")
    parser.add_argument("--worker-id", default=f"{socket.gethostname()}-worker")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--lease-seconds", type=int, default=300)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    root = Path(args.control_root).resolve()
    if args.once:
        print(run_worker_once(root, args.worker_id, lease_seconds=args.lease_seconds))
    else:
        run_worker_forever(root, args.worker_id, poll_seconds=args.poll_seconds, lease_seconds=args.lease_seconds)


if __name__ == "__main__":
    main()
