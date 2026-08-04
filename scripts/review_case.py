"""由人显式触发的文件型审批命令。"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.governance import ApprovalError, approve_case, request_approval


def main() -> int:
    parser = argparse.ArgumentParser(description="请求或执行人工规则审批")
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--request", action="store_true", help="创建待人工审批清单")
    parser.add_argument("--decision", choices=["approve", "reject", "request_changes"])
    parser.add_argument("--approver")
    parser.add_argument("--comment")
    parser.add_argument("--registry", type=Path, default=Path("data/rule_registry"))
    args = parser.parse_args()
    try:
        if args.request:
            print(request_approval(args.case_dir))
            return 0
        if not all((args.decision, args.approver, args.comment)):
            parser.error("审批时必须提供 --decision、--approver 和 --comment")
        print(approve_case(args.case_dir, args.approver, args.decision, args.comment, args.registry))
        return 0
    except ApprovalError as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
