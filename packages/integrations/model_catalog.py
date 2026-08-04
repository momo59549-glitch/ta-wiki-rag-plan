"""只读目录化历史 Model 实验 JSON。"""
from __future__ import annotations

from pathlib import Path

from .model_adapter import ModelExperimentAdapter


def catalog_experiments(experiments_root: Path) -> dict:
    adapter = ModelExperimentAdapter(experiments_root)
    entries, failures = [], []
    for path in sorted(experiments_root.rglob("*.json")):
        relative = path.relative_to(experiments_root)
        try:
            imported = adapter.import_file(relative)
            entries.append(imported.manifest())
        except Exception as exc:
            failures.append({"source_path": relative.as_posix(), "error": f"{type(exc).__name__}: {exc}"})
    return {"schema_version": "model-experiment-catalog/v1", "root": str(experiments_root), "entries": entries, "failures": failures, "note": "只读目录；未执行任何历史策略。"}
