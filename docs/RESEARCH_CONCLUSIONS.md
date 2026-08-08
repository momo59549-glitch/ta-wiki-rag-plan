# 研究结论存档：技术分析事件规则空间负结论

归档日期：2026-08-08。本文件是"框架当前搜索空间不存在可发布规则"这一负结论的
正式存档，包含证据清单与复现方法。结论随证据更新，不随观点变化。

## 背景

框架定位是多 Agent 研究闭环：Observation → Outcome → Hypothesis → Backtest →
Rule Version → Knowledge Update。核心资产是可审计的研究数据库与闭环经验；
失败、拒绝和证据不足都是正式结果。本文记录的是**一个正式的负结论**。

## 结论 1：178 个技术分析事件候选无跨市场状态稳定正超额

搜索空间：23 个规则族、178 个候选（蜡烛形态、均线金叉死叉、MACD、RSI、布林带、
动量、突破、放量、组合），全部为 DSL 表达式，与正式流水线一致性由逐根测试保证。

检验口径：开发股票池 300 只（2023-12-31 点时有效）、研究期 2010-01-01 至
2023-12-31、验证期 2022-01-01 起、成本 3+5 bps 单边、T+1 开盘买入、固定 H 日
收盘退出、跨候选 FDR-BH。

| 轮次 | 附加门槛 | 通过数 | 关键发现 |
|---|---|---:|---|
| 第一轮 | 无 | 158/178 | 146 个仅靠 `20日/bearish` 通过，高度相关 |
| 第二轮 | 2×/3× 成本压力、≥2 周期、Jaccard 去重 | 78/178 | 全部通过分组市场状态均为 bearish |
| 第三轮 | 再加"bullish 与 bearish 均需有通过分组" | **0/178** | 双状态一致性检验全灭 |

结论：**该搜索空间内不存在跨市场状态一致、抗成本压力、多周期稳定的规则**。

## 结论 2：Model 项目入场信号未通过检验

把 Model 项目的三条核心入场信号转成 DSL 事件规则后同口径检验：

| 规则 | 信号数 | 结果 |
|---|---:|---|
| donchian_main（20 日突破 + MA 多头排列 + MA200） | 65,230 | 无通过分组（与 Model README 的 -1.00% 结论一致） |
| momentum_breakout（突破 + MA5/20 + RSI<70 + 放量） | 97,563 | 无通过分组 |
| meanrev_rsi（RSI<30 + 价<MA20） | 44,056 | 与已有 RSI 候选重复；bearish 单边 |

注：检验覆盖入场信号的事件收益，不含 Model 的动态出场（ATR 止损/止盈）。

## 结论 3：rsi_oversold 全市场冻结裁决为拒绝发布

`rsi_oversold@1.0.0`（RSI(14) < 30）冻结 Campaign `campaign_20260808T032005Z`
全市场执行完成（1,292,548 条样本外 Outcome）。QA `passed_with_limitations`
（代码快照漂移，阻断审批路径）。样本外净超额：

| 周期 | bearish | bullish |
|---:|---:|---:|
| 1日 | -0.21% | +0.13% |
| 3日 | +0.01%（不显著） | -0.03%（不显著） |
| 5日 | +0.28% | -0.16% |
| 10日 | +0.62% | -0.38% |
| 20日 | +2.38% | -1.55% |

裁决：**拒绝发布**。正超额仅存在于熊市长周期，牛市长周期显著为负；这是熊市反弹
共性效应，不是独立规则信号。完整记录：
`data/strategy_test_executions/protocol_a666a614…/adjudication.json`。

## 锁箱安排（2026-09-01 起）

最终锁箱保持未读。锁箱开启后的唯一动作：对同一规则空间做一次未看过的样本外
裁决。按现有证据，预期同样不通过——该预期不作为结论，仅作为决策前置说明。

## 证据清单

- 搜索轮次：[data/rule_search/round_20260808](H:\股票模型\ta-wiki-rag-plan\data\rule_search\round_20260808)、
  [round_20260808b](H:\股票模型\ta-wiki-rag-plan\data\rule_search\round_20260808b)、
  [round_20260808c](H:\股票模型\ta-wiki-rag-plan\data\rule_search\round_20260808c)、
  [round_20260808d](H:\股票模型\ta-wiki-rag-plan\data\rule_search\round_20260808d)
- 审核记录：各轮 `review.json`
- 冻结裁决：`data/strategy_test_executions/protocol_a666a614…/adjudication.json`
- 框架代码：`packages/research/rule_search.py`、`packages/rules/catalog.py`
- 方法文档：`docs/RULE_SEARCH.md`、`docs/IS_ST_VALIDATION.md`

## 框架定位调整

基于本负结论，框架不再定位为"自动寻找盈利规则"，而是：

1. **证伪工具**：对任何新想法用固定口径快速检验，把坏主意挡在门外；
2. **研究档案**：快照、审计链、知识卡、案例库长期保留。

未来若扩展搜索空间（横截面因子、行业轮动、指数择时等），须先明确"要回答的
问题"，预登记假设，且锁箱开启前不得反复查看开发集结果。
