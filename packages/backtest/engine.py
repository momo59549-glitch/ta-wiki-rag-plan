from __future__ import annotations

from dataclasses import dataclass
from packages.contracts import Candle
from packages.rule_dsl import CompiledRule
from packages.rule_engine import evaluate


@dataclass(frozen=True, slots=True)
class BacktestManifest:
    rule_semantic_hash: str
    decision_time: str = "bar_close"
    execution_time: str = "next_bar_open"
    commission_bps: float = 3.0
    slippage_bps: float = 5.0


@dataclass(frozen=True, slots=True)
class BacktestResult:
    trades: int
    gross_return: float
    net_return: float
    warnings: tuple[str, ...] = ()


def run_single_bar_strategy(series: list[Candle], rule: CompiledRule, manifest: BacktestManifest) -> BacktestResult:
    """最小可审计执行模型：收盘观察，下一根开盘买入、同根收盘卖出。"""
    if manifest.rule_semantic_hash != rule.semantic_hash:
        raise ValueError("manifest 与规则 semantic_hash 不匹配")
    returns: list[float] = []
    cost = (manifest.commission_bps + manifest.slippage_bps) / 10_000
    for index in range(len(series) - 1):
        signal = evaluate(series, index, rule)
        if signal.status != "matched":
            continue
        fill = series[index + 1]
        if fill.open <= 0:
            continue
        returns.append((fill.close / fill.open - 1) - 2 * cost)
    gross = sum(value + 2 * cost for value in returns)
    return BacktestResult(len(returns), gross, sum(returns))
