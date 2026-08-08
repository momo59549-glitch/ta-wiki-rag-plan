"""Auditable time-series validation built on skfolio.

The project owns the experiment registry and evidence gates; it deliberately
does not reimplement financial cross-validation splitters.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Iterable, Literal

import pandas as pd


@dataclass(frozen=True, slots=True)
class WalkForwardConfig:
    train_size: int
    test_size: int
    purge_size: int
    expanding: bool = True

    def __post_init__(self) -> None:
        if self.train_size < 2 or self.test_size < 1 or self.purge_size < 1:
            raise ValueError("train_size>=2、test_size>=1 且 purge_size>=1")


def build_walk_forward_folds(
    observation_dates: Iterable[date | datetime | pd.Timestamp], config: WalkForwardConfig
) -> dict:
    """Return chronological folds with a mandatory leakage gap.

    ``purge_size`` must be at least the maximum label/holding horizon.  The
    resulting dates and indices are persisted with a research case, so a
    future rerun can reproduce exactly which observations were eligible.
    """
    try:
        from skfolio.model_selection import WalkForward
    except ImportError as exc:  # pragma: no cover - exercised in deployment
        raise RuntimeError("缺少研究依赖 skfolio；请安装项目的 research extra") from exc

    index = pd.DatetimeIndex(pd.to_datetime(list(observation_dates))).sort_values().unique()
    if len(index) < config.train_size + config.purge_size + config.test_size:
        return {"config": asdict(config), "folds": [], "reason": "insufficient_observation_dates"}
    splitter = WalkForward(
        train_size=config.train_size,
        test_size=config.test_size,
        purged_size=config.purge_size,
        expand_train=config.expanding,
        reduce_test=True,
    )
    # skfolio's splitter accepts a DataFrame and preserves its DatetimeIndex.
    frame = pd.DataFrame({"placeholder": range(len(index))}, index=index)
    folds = []
    for fold_id, (train_indices, test_indices) in enumerate(splitter.split(frame)):
        folds.append({
            "fold_id": fold_id,
            "train_indices": train_indices.tolist(),
            "test_indices": test_indices.tolist(),
            "train_start": index[train_indices[0]].date().isoformat(),
            "train_end": index[train_indices[-1]].date().isoformat(),
            "test_start": index[test_indices[0]].date().isoformat(),
            "test_end": index[test_indices[-1]].date().isoformat(),
        })
    return {"config": asdict(config), "folds": folds, "engine": "skfolio.WalkForward"}


def validation_status(*, all_dates: Iterable[date | datetime | pd.Timestamp], lockbox_start: date | None) -> Literal["exploratory", "sealed", "contaminated"]:
    """A viewed holdout cannot be presented as a pristine lockbox again."""
    if lockbox_start is None:
        return "exploratory"
    return "contaminated" if any(pd.Timestamp(item).date() >= lockbox_start for item in all_dates) else "sealed"
