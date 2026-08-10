"""Self-contained, read-only copy of the verified Gen1 comparison closure."""
from __future__ import annotations

from pathlib import Path
import shutil

from packages.research.gen2_discovery import verify_parent_generation_closure


PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SOURCE_DIR = PROJECT_ROOT / "data" / "candidate_comparisons" / "g_20260809_01"


def closure_fixture(root: Path) -> tuple[Path, dict]:
    """Copy only result+its required sibling protocol into an isolated temp dir."""
    destination = root / "parent_closure"
    destination.mkdir(parents=True, exist_ok=False)
    for name in ("comparison_result.json", "comparison_protocol.json"):
        source = _SOURCE_DIR / name
        if not source.is_file():
            raise FileNotFoundError(f"verified closure source missing: {source}")
        shutil.copy2(source, destination / name)
    result = destination / "comparison_result.json"
    return result, verify_parent_generation_closure(result)
