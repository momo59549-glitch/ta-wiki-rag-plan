"""Small, dependency-free statistical summaries for research evidence gates.

This module intentionally reports descriptive uncertainty only.  It does not
produce trading signals, recommendations, or publication decisions.
"""

from __future__ import annotations

from collections import defaultdict
from math import isfinite, sqrt
from statistics import NormalDist, mean, stdev
from typing import Any, Iterable, Mapping


NOT_INVESTMENT_ADVICE = (
    "统计摘要仅用于研究证据评估，不构成投资建议、交易信号或规则发布审批。"
)


def summarize_outcomes(
    outcomes: Iterable[Mapping[str, Any]],
    *,
    return_field: str = "net_excess_return",
    confidence_level: float = 0.95,
) -> dict[str, Any]:
    """Group valid outcome returns by holding horizon and market regime.

    ``return_field`` defaults to net excess return so costs and the selected
    benchmark are already reflected. Invalid/missing observations are counted
    as excluded rather than silently converted to zero.
    """
    if not 0.0 < confidence_level < 1.0:
        raise ValueError("confidence_level must be between 0 and 1")

    grouped: dict[tuple[int, str], list[float]] = defaultdict(list)
    received = excluded = 0
    for outcome in outcomes:
        received += 1
        horizon = outcome.get("horizon_bars")
        value = outcome.get(return_field)
        try:
            horizon = int(horizon)
            value = float(value)
        except (TypeError, ValueError):
            excluded += 1
            continue
        if horizon <= 0 or not isfinite(value):
            excluded += 1
            continue
        regime = str(outcome.get("market_regime") or "unknown")
        grouped[(horizon, regime)].append(value)

    groups = [
        _group_summary(horizon, regime, values, confidence_level)
        for (horizon, regime), values in sorted(grouped.items())
    ]
    return {
        "return_field": return_field,
        "confidence_level": confidence_level,
        "outcomes_received": received,
        "outcomes_excluded": excluded,
        "groups": groups,
        "disclaimer": NOT_INVESTMENT_ADVICE,
    }


def _group_summary(
    horizon_bars: int, market_regime: str, values: list[float], confidence_level: float
) -> dict[str, Any]:
    count = len(values)
    average = mean(values)
    if count < 2:
        return {
            "horizon_bars": horizon_bars,
            "market_regime": market_regime,
            "sample_size": count,
            "mean_return": average,
            "sample_stddev": None,
            "standard_error": None,
            "t_statistic": None,
            "confidence_interval": None,
            "evidence_status": "insufficient_sample",
        }

    sample_stddev = stdev(values)
    standard_error = sample_stddev / sqrt(count)
    # A zero-variance group is exactly estimated under this simple model.
    t_statistic = average / standard_error if standard_error else None
    z_critical = NormalDist().inv_cdf((1.0 + confidence_level) / 2.0)
    margin = z_critical * standard_error
    return {
        "horizon_bars": horizon_bars,
        "market_regime": market_regime,
        "sample_size": count,
        "mean_return": average,
        "sample_stddev": sample_stddev,
        "standard_error": standard_error,
        "t_statistic": t_statistic,
        "confidence_interval": {"lower": average - margin, "upper": average + margin},
        "evidence_status": "descriptive_only",
    }
