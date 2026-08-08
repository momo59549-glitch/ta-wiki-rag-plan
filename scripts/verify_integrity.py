"""Verify append-only audit and backup hash manifests without changing data."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path

from packages.governance import FileAuditLog


def verify_backup(root: Path) -> dict:
    manifest_path = root / "backup-manifest.json"
    if not manifest_path.is_file():
        return {"valid": False, "error": "backup-manifest.json not found"}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    failures = []
    for item in manifest.get("files", []):
        path = (root / item["path"]).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError:
            failures.append({"path": item["path"], "error": "path escape"})
            continue
        if not path.is_file():
            failures.append({"path": item["path"], "error": "missing"})
            continue
        actual = sha256(path.read_bytes()).hexdigest().upper()
        if actual != str(item["sha256"]).upper() or path.stat().st_size != int(item["bytes"]):
            failures.append({"path": item["path"], "error": "hash or size mismatch"})
    return {"valid": not failures, "files": len(manifest.get("files", [])), "failures": failures}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path)
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    if not args.audit and not args.backup:
        parser.error("至少提供 --audit 或 --backup")
    result = {}
    if args.audit:
        result["audit"] = FileAuditLog(args.audit).verify()
    if args.backup:
        result["backup"] = verify_backup(args.backup.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if all(item.get("valid") for item in result.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
