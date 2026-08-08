# 回测验证与成熟引擎重构

## 已落地的职责边界

| 层 | 采用方案 | 项目保留职责 |
|---|---|---|
| 全市场事件研究 | 本项目 `Observation → Outcome` | A 股数据快照、规则版本、可成交性记录 |
| 候选组合复核 | `vectorbt` | 将 T 收盘信号严格移到 T+1 开盘成交 |
| 时间序列切分 | `skfolio.WalkForward` | 保存每个 fold 的日期与索引 |
| 多重检验 | `statsmodels.multipletests` FDR-BH | 候选/发布门槛与审计 |
| 最终独立复核 | Backtrader（下一阶段） | A 股撮合细节、涨跌停队列假设 |

不重复实现向量化组合回测、Walk-forward 切分或多重检验算法；项目只维护 A 股市场规则、可审计事件链和人工治理。

## 信息时间线

```text
T 日 15:00 收盘数据可见 → 生成 Observation
T+1 开盘前 → 根据昨收、开盘价、ST/停牌状态判断买入资格
T+1 开盘 → 买入成交（不得读取 T+1 收盘、全天量额）
持有 H 个交易日 → H 日收盘退出并记录 Outcome
```

`packages.research.execution.assess_execution` 的 `price_at` 是强制信息边界：

- `open`：不读取当日收盘/全天量额；
- `close`：允许使用完整日线，用于收盘退出的记录。

## 防过拟合协议

1. 冻结 Rule ID、版本、参数搜索空间、交易成本、股票池和最长持有期。
2. 每一训练/测试切分使用 `skfolio.WalkForward`；`purge_size >= max(horizons)`。
3. 参数只能在各自训练窗中选择；测试窗只记录一次，不反向改参数。
4. 所有周期和市场状态组一起接受 FDR-BH 校正；仅正净超额、95% CI 下界为正、校正后显著且样本数达标的组可成为候选。
5. 候选交给 `vectorbt` 做组合级复核；正式发布前再使用从未看过的锁箱时段与 Backtrader 独立复核。

## 旧结果处理

`data/research_cases_full` 在重构前生成，使用过的 2024–2026 数据不能重新称为最终样本外。保留其审计价值，但统一标记为探索性证据。新规则版本必须生成新的研究案例和锁箱报告。

## 尚未完成的高优先级项

- 点时股票池与退市记录已落地：5,875 条记录、338 个退市标的，组合缓存对当前有效股票覆盖 100%；指数成分历史仍属于更细分的扩展数据；
- A 股开盘涨停队列、成交容量及更细粒度冲击成本模型仍是日线测试的限制；当前冻结固定成本及 2×、3× 压力场景；
- Backtrader 候选组合报告与 vectorbt 结果的逐订单差异报告。

## 第二阶段：可见时间、股票池与独立核验

- 本地 Parquet 适配器为日线填充 `available_at=bar_close`；规则引擎会拒绝任何在决策时刻尚不可见的 bar。
- `packages.market_data.universe.load_point_in_time_universe` 使用 JSONL 清单；每行包含 `symbol`、`active_from`、可选 `active_to` 和 `source`。缺少清单不会静默回退为“当前文件列表”。
- `run_team.py` 可接收 `--universe-manifest` 和 `--universe-as-of`。没有该清单的案例仍能探索，但 QA 只能为 `passed_with_limitations`，不能提交规则审批。
- `packages.research.backtrader_adapter` 使用 Backtrader 的事件驱动循环，作为 vectorbt 复核之外的第二引擎；只用于已通过统计门槛的候选。
