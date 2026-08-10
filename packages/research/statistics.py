"""Small, dependency-free statistical summaries for research evidence gates.

This module intentionally reports descriptive uncertainty only.  It does not
produce trading signals, recommendations, or publication decisions.
"""

from __future__ import annotations

from math import isfinite, sqrt
from statistics import NormalDist
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

    # Welford accumulators keep memory proportional to the number of reporting
    # groups rather than the number of full-market outcomes.
    grouped: dict[tuple[int, str], dict[str, float]] = {}
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
        accumulator = grouped.setdefault((horizon, regime), {"count": 0.0, "mean": 0.0, "m2": 0.0})
        accumulator["count"] += 1
        delta = value - accumulator["mean"]
        accumulator["mean"] += delta / accumulator["count"]
        accumulator["m2"] += delta * (value - accumulator["mean"])

    groups = [
        _group_summary(horizon, regime, accumulator, confidence_level)
        for (horizon, regime), accumulator in sorted(grouped.items())
    ]
    _apply_multiple_testing(groups)
    return {
        "return_field": return_field,
        "confidence_level": confidence_level,
        "outcomes_received": received,
        "outcomes_excluded": excluded,
        "groups": groups,
        "multiple_testing": {
            "engine": "statsmodels.stats.multitest.multipletests",
            "method": "fdr_bh",
            "alpha": 0.05,
            "note": "对同一份统计摘要中所有有效分组统一校正；这不是独立性假设的替代品。",
        },
        "disclaimer": NOT_INVESTMENT_ADVICE,
    }


def _group_summary(
    horizon_bars: int, market_regime: str, accumulator: Mapping[str, float], confidence_level: float
) -> dict[str, Any]:
    count = int(accumulator["count"])
    average = float(accumulator["mean"])
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

    sample_stddev = sqrt(float(accumulator["m2"]) / (count - 1))
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


def _apply_multiple_testing(groups: list[dict[str, Any]]) -> None:
    """Attach two-sided raw/adjusted p-values without selecting on them."""
    eligible = [group for group in groups if group.get("t_statistic") is not None]
    for group in groups:
        group["raw_p_value"] = None
        group["adjusted_p_value"] = None
        group["multiple_testing_reject"] = False
    if not eligible:
        return
    raw = [2 * (1 - NormalDist().cdf(abs(float(group["t_statistic"])))) for group in eligible]
    try:
        from statsmodels.stats.multitest import multipletests
    except ImportError as exc:  # pragma: no cover - exercised in deployment
        raise RuntimeError("缺少研究依赖 statsmodels；请安装项目的 research extra") from exc
    rejected, adjusted, _, _ = multipletests(raw, alpha=0.05, method="fdr_bh")
    for group, raw_value, adjusted_value, reject in zip(eligible, raw, adjusted, rejected):
        group["raw_p_value"] = float(raw_value)
        group["adjusted_p_value"] = float(adjusted_value)
        group["multiple_testing_reject"] = bool(reject)
