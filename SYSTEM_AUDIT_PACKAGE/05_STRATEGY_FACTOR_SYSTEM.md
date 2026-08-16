# 05 · 策略与因子系统

## 已显式注册的规则

来源：`packages/rules/catalog.py`。

| ID | 逻辑/参数 | 备注 |
|---|---|---|
| `hammer@1.0.0` | 下影/实体 ≥2， 上影/全幅 ≤0.15，5 bar 下跌语境 | 工程近似锤子线。 |
| `rsi_oversold@1.0.0` | RSI(14) <30 | 已参与历史研究，未获发布。 |
| `breakout_60d@1.0.0` | close > 前 60 日 max high | 突破。 |
| `donchian_main@1.0.0` | 20 日近高×0.97、MA20>MA60、close>MA20/MA200 | 从 Model 主入场逻辑迁移。 |
| `momentum_breakout@1.0.0` | 近高×0.95、MA5>MA20、RSI<70、20 日量比>1.2 | 动量/量能突破。 |
| `meanrev_rsi@1.0.0` | RSI(14)<30 且 close<MA20 | 均值回归入场。 |

## 可计算指标

来源：`packages/research/indicators.py`、`packages/rule_dsl/compiler.py`。

- 基础 K 线：`open/high/low/close/body/range/upper_shadow/lower_shadow/is_bullish/is_bearish`。
- 趋势：SMA、EMA、max high、min low、Donchian 类突破。
- 动量：RSI、ROC、20 日动量。
- 波动/通道：Bollinger upper/lower；指标计算代码还支持 MACD DIF/DEA/hist。
- 成交：volume、volume ratio；amount 作为最小流动性过滤，可选。
- 上下文：连续上涨/下跌 close count、市场基准 SMA regime、历史波动/相对强弱/成交量 wrapper（Gen2）。

## 自动发现/组合

`packages/research/auto_discovery.py` 的 Gen1 是**有限、outcome-blind** grammar，预算最多 256（当前 v1 独立逻辑容量注释为 100）。可生成：价格相对/斜率、MA 交叉、RSI overbought/oversold、ROC、突破/跌破、放量阳/阴、锤子/流星、双 K 反转语境、doji、以及 RSI+趋势、锤子+趋势、突破+放量组合。生成后用 semantic/logic hash 去重，不能仅改名称重复试验。

`packages/research/gen2_discovery.py` 只完成下一代预注册骨架：RSI/ROC/breakdown base rule 加冻结 benchmark regime、相对强弱、波动与量能 context wrapper；文档和代码均指出尚未开始真实 Gen2 收益筛选。

## 参数如何选择/优化

- 显式 catalog 参数写死在 `RuleDefinition.parameters`；没有在线自适应或自动优化器。
- 自动发现的离散窗口/阈值在**生成 grammar 前**固定，例如 RSI window 7/14/21 与阈值 25/30/35，MA 5/10/20/60，量比 1.5/2/3。
- `SearchConfig` 要求固定 `start/end/oos_start/lockbox_start`、成本、horizon、候选预算；`build_search_protocol()` 记录这些 hash。
- 筛选以 FDR-BH、最小样本、正均值/CI、2x/3x 成本压力、至少多个 horizon、Jaccard 去重、可选 bull/bear 双状态为门槛（`rule_search.py`）。
- **未实现**：连续超参数优化、Bayesian optimization、自动重训、自动因子权重学习、模型 ensemble、强化学习、实时因子选择。

## 当前策略结论

`docs/RESEARCH_CONCLUSIONS.md` 和真实 `data/candidate_comparisons/g_20260809_01/comparison_result.json` 表明：Gen1 三个正式比较候选 `roc/rsi/breakdown` 均为 `research_eliminated_event`；178 个技术事件规则在要求 bull/bear 均稳定、成本压力与多周期时无通过项。ROC 仅内部相对第一，不是有效或可发布策略。
