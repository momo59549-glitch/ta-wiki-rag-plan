from packages.contracts import RuleDefinition


HAMMER_V1 = RuleDefinition(
    id="hammer",
    version="1.0.0",
    name_zh="锤子线（工程近似）",
    warmup_bars=5,
    parameters={"min_lower_shadow_body": 2.0, "max_upper_shadow_range": 0.15},
    expression={"all": [
        {"gte": [
            {"safe_div": [
                {"metric": {"name": "lower_shadow", "offset": 0}},
                {"max": [{"metric": {"name": "body", "offset": 0}}, 0.01]},
            ]},
            {"param": "min_lower_shadow_body"},
        ]},
        {"lte": [
            {"safe_div": [
                {"metric": {"name": "upper_shadow", "offset": 0}},
                {"max": [{"metric": {"name": "range", "offset": 0}}, 0.01]},
            ]},
            {"param": "max_upper_shadow_range"},
        ]},
        {"context": {"name": "lower_close_count", "window": 5, "min_count": 3}},
    ]},
)

RSI_OVERSOLD_V1 = RuleDefinition(
    id="rsi_oversold",
    version="1.0.0",
    name_zh="RSI(14) 超卖（自动搜索候选）",
    warmup_bars=20,
    parameters={"threshold": 30.0},
    expression={"lt": [
        {"metric": {"name": "rsi", "offset": 0, "window": 14}},
        {"param": "threshold"},
    ]},
)

BREAKOUT_60D_V1 = RuleDefinition(
    id="breakout_60d",
    version="1.0.0",
    name_zh="60 日新高突破（自动搜索候选）",
    warmup_bars=70,
    parameters={"window": 60.0},
    expression={"gt": [
        {"metric": {"name": "close", "offset": 0}},
        {"metric": {"name": "max_high", "offset": -1, "window": 60}},
    ]},
)

_RULES = {
    HAMMER_V1.id: HAMMER_V1,
    RSI_OVERSOLD_V1.id: RSI_OVERSOLD_V1,
    BREAKOUT_60D_V1.id: BREAKOUT_60D_V1,
}


def get_rule(rule_id: str) -> RuleDefinition:
    try:
        return _RULES[rule_id]
    except KeyError as exc:
        raise KeyError(f"未知规则: {rule_id}; 可用: {sorted(_RULES)}") from exc
