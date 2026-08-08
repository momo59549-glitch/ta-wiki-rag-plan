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

DONCHIAN_MAIN_V1 = RuleDefinition(
    id="donchian_main",
    version="1.0.0",
    name_zh="Donchian 趋势跟踪入场（Model 主策略）",
    warmup_bars=210,
    parameters={"breakout_threshold": 0.97},
    expression={"all": [
        {"gte": [
            {"metric": {"name": "close", "offset": 0}},
            {"mul": [
                {"metric": {"name": "max_high", "offset": 0, "window": 20}},
                {"param": "breakout_threshold"},
            ]},
        ]},
        {"gt": [
            {"metric": {"name": "sma", "offset": 0, "window": 20}},
            {"metric": {"name": "sma", "offset": 0, "window": 60}},
        ]},
        {"gt": [
            {"metric": {"name": "close", "offset": 0}},
            {"metric": {"name": "sma", "offset": 0, "window": 20}},
        ]},
        {"gt": [
            {"metric": {"name": "close", "offset": 0}},
            {"metric": {"name": "sma", "offset": 0, "window": 200}},
        ]},
    ]},
)

MOMENTUM_BREAKOUT_V1 = RuleDefinition(
    id="momentum_breakout",
    version="1.0.0",
    name_zh="动量突破入场（Model momentum_config）",
    warmup_bars=40,
    parameters={"breakout_threshold": 0.95},
    expression={"all": [
        {"gt": [
            {"metric": {"name": "close", "offset": 0}},
            {"mul": [
                {"metric": {"name": "max_high", "offset": 0, "window": 20}},
                {"param": "breakout_threshold"},
            ]},
        ]},
        {"gt": [
            {"metric": {"name": "sma", "offset": 0, "window": 5}},
            {"metric": {"name": "sma", "offset": 0, "window": 20}},
        ]},
        {"lt": [
            {"metric": {"name": "rsi", "offset": 0, "window": 14}},
            70.0,
        ]},
        {"gt": [
            {"metric": {"name": "volume_ratio", "offset": 0, "window": 20}},
            1.2,
        ]},
    ]},
)

MEANREV_RSI_V1 = RuleDefinition(
    id="meanrev_rsi",
    version="1.0.0",
    name_zh="RSI 超卖反弹入场（Model meanrev_config）",
    warmup_bars=30,
    parameters={},
    expression={"all": [
        {"lt": [
            {"metric": {"name": "rsi", "offset": 0, "window": 14}},
            30.0,
        ]},
        {"lt": [
            {"metric": {"name": "close", "offset": 0}},
            {"metric": {"name": "sma", "offset": 0, "window": 20}},
        ]},
    ]},
)

_RULES = {
    HAMMER_V1.id: HAMMER_V1,
    RSI_OVERSOLD_V1.id: RSI_OVERSOLD_V1,
    BREAKOUT_60D_V1.id: BREAKOUT_60D_V1,
    DONCHIAN_MAIN_V1.id: DONCHIAN_MAIN_V1,
    MOMENTUM_BREAKOUT_V1.id: MOMENTUM_BREAKOUT_V1,
    MEANREV_RSI_V1.id: MEANREV_RSI_V1,
}


def get_rule(rule_id: str) -> RuleDefinition:
    try:
        return _RULES[rule_id]
    except KeyError as exc:
        raise KeyError(f"未知规则: {rule_id}; 可用: {sorted(_RULES)}") from exc
