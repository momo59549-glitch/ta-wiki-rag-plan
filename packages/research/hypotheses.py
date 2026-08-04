"""从统计摘要创建保守的、待审阅条件性假设。"""
from __future__ import annotations

from typing import Any


def build_hypothesis_draft(statistics: dict[str, Any], min_samples: int = 300) -> dict[str, Any]:
    if min_samples < 2:
        raise ValueError("min_samples 至少为 2")
    candidates, rejected = [], []
    for group in statistics.get("groups", []):
        ci = group.get("confidence_interval")
        reasons = []
        if group.get("sample_size", 0) < min_samples:
            reasons.append("insufficient_sample")
        if group.get("mean_return") is None or group["mean_return"] <= 0:
            reasons.append("non_positive_mean")
        if not ci or ci.get("lower") is None or ci["lower"] <= 0:
            reasons.append("confidence_interval_not_above_zero")
        record = {"horizon_bars": group.get("horizon_bars"), "market_regime": group.get("market_regime"), "sample_size": group.get("sample_size"), "mean_net_excess_return": group.get("mean_return"), "confidence_interval": ci, "t_statistic": group.get("t_statistic")}
        if reasons:
            record["rejection_reasons"] = reasons
            rejected.append(record)
        else:
            record["claim"] = f"在 {record['market_regime']} 状态下，信号后 {record['horizon_bars']} 个交易日的净超额收益为正（待人工复核）。"
            candidates.append(record)
    return {"status": "draft", "summary": f"统计候选 {len(candidates)} 个；最小样本门槛 {min_samples}", "minimum_samples": min_samples, "candidate_hypotheses": candidates, "candidate_horizons": candidates, "rejected_groups": rejected, "limitations": ["统计为正态近似，未做多重检验", "日线可成交性为保守近似", "候选仍需人工审批"], "publication": "blocked_until_human_approval", "disclaimer": "该草稿只整理统计证据；不是投资建议或自动发布指令。"}
