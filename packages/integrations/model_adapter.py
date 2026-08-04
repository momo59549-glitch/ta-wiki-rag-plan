"""Import the legacy ``Model/data/experiments`` JSON artifacts without executing them.

The adapter deliberately treats the legacy repository as evidence, not as a
runtime dependency.  It reads JSON only, keeps the original payload intact,
and attaches a content hash so a Research Case can cite exactly what was used.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
from typing import Any


class ModelExperimentImportError(ValueError):
    """Raised when a requested legacy experiment is not a safe JSON artifact."""


@dataclass(frozen=True, slots=True)
class ImportedModelExperiment:
    """A normalized, auditable view of one legacy experiment JSON file."""

    source_path: str
    source_sha256: str
    imported_at: str
    artifact_type: str
    experiment_name: str
    configuration: dict[str, Any]
    metrics: dict[str, Any]
    raw_payload: dict[str, Any]

    def manifest(self) -> dict[str, Any]:
        """Return a JSON-serializable provenance record for case artifacts."""
        return {
            "schema_version": "model-experiment-import/v1",
            "source_path": self.source_path,
            "source_sha256": self.source_sha256,
            "imported_at": self.imported_at,
            "artifact_type": self.artifact_type,
            "experiment_name": self.experiment_name,
            "configuration": self.configuration,
            "metrics": self.metrics,
        }


class ModelExperimentAdapter:
    """Safe, read-only importer for ``Model/data/experiments`` JSON files."""

    _CONFIG_KEYS = frozenset(
        {
            "start_date", "end_date", "pool_size", "top_n", "rebalance_freq",
            "init_cash", "take_profit", "stop_loss", "max_hold_days",
            "single_position", "t_plus_1", "next_open", "limit_check", "factors",
        }
    )
    _METRIC_KEYS = frozenset(
        {
            "capital", "start", "end", "years", "final_value", "total_return",
            "annual_return", "max_drawdown", "total_trades", "win_rate", "avg_win",
            "avg_loss", "annual_returns", "sharpe", "sortino", "calmar",
        }
    )

    def __init__(self, experiments_root: str | Path) -> None:
        root = Path(experiments_root).expanduser().resolve()
        if not root.is_dir():
            raise ModelExperimentImportError(f"experiments root does not exist: {root}")
        self._root = root

    def import_file(self, relative_path: str | Path) -> ImportedModelExperiment:
        """Read one JSON object below the configured root.

        Absolute paths and traversal outside the root are rejected.  The method
        never imports Python modules, runs a strategy, or mutates the source.
        """
        candidate = Path(relative_path)
        if candidate.is_absolute():
            raise ModelExperimentImportError("relative_path must be below experiments_root")
        path = (self._root / candidate).resolve()
        try:
            path.relative_to(self._root)
        except ValueError as exc:
            raise ModelExperimentImportError("path escapes experiments_root") from exc
        if path.suffix.lower() != ".json" or not path.is_file():
            raise ModelExperimentImportError("only existing .json experiment artifacts are supported")

        raw = path.read_bytes()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelExperimentImportError(f"invalid JSON artifact: {path.name}") from exc
        if not isinstance(payload, dict):
            raise ModelExperimentImportError("experiment artifact must contain a JSON object")

        configuration = {key: payload[key] for key in self._CONFIG_KEYS if key in payload}
        metrics = {key: payload[key] for key in self._METRIC_KEYS if key in payload}
        artifact_type = self._classify(configuration, metrics)
        name = str(payload.get("name") or path.stem)
        return ImportedModelExperiment(
            source_path=path.relative_to(self._root).as_posix(),
            source_sha256=sha256(raw).hexdigest(),
            imported_at=datetime.now(UTC).isoformat(),
            artifact_type=artifact_type,
            experiment_name=name,
            configuration=configuration,
            metrics=metrics,
            raw_payload=payload,
        )

    @staticmethod
    def _classify(configuration: dict[str, Any], metrics: dict[str, Any]) -> str:
        if configuration and metrics:
            return "experiment_with_metrics"
        if configuration:
            return "experiment_configuration"
        if metrics:
            return "backtest_result"
        return "unknown_json_artifact"
