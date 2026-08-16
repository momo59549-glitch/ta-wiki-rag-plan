# 04 · 回测引擎审计

系统有三条回测/核算路径，成熟度不同，不能混用其结论。

## A. 事件 Outcome 流水线（主研究路径）

证据：`packages/research/pipeline.py`（`FileResearchPipeline.run()`、`_outcome()`），`packages/research/execution.py`（`assess_execution()`），`packages/research/models.py`。

1. `rule_engine.evaluate(series, index, rule)` 在 **T 日收盘**判断 DSL 条件；`RuleDefinition` 固定 `observed_at="bar_close"`、`executable_from="next_bar_open"`。
2. `Observation` 记录 `observed_at`、`executable_at`、规则 semantic hash、数据 snapshot、命中条件。
3. 对每个固定 `horizon_bars`，Outcome 以 T+1 开盘为 entry；以固定 horizon 的收盘为 exit（详情由 `_outcome()` 计算）。
4. `assess_execution()` 对 entry 以 `price_at="open", require_session_liquidity=False` 检查，避免用当天收盘/成交量倒看开盘订单；exit 以 close 和流动性检查。
5. `PipelineConfig.commission_bps_per_side`、`slippage_bps_per_side` 从 raw return 扣双边成本，写入 `net_return/net_excess_return`。
6. 每批 Observation/Outcome 写到 JSONL shard 后，`run_artifacts.write_batch()` 才发布 hash commit；断点恢复只接受连续、哈希正确的 commit。

### 交易约束覆盖

| 要求 | 实现 | 审计结论 |
|---|---|---|
| 信号/成交时序 | T 收盘 → T+1 open | 已实现且有显式字段。 |
| 手续费/滑点 | bps 费率；典型协议为单边 3 bps 佣金 + 5 bps 滑点 | 已实现为比例成本。 |
| 印花税 | 无 | **未实现**。 |
| 最低佣金 | 无 | **未实现**。 |
| 过户费 | 无 | **未实现**。 |
| 最小交易单位/整手 | 无 | **未实现**。 |
| 现金、仓位、资金占用 | Outcome 事件本身无账户 | 主路径**未实现组合现金账户**。 |
| 停牌/无量 | 日线 flag/价/量近似 | 仅近似；无正式停牌 PIT。 |
| 涨跌停 | `prev_close` 推导阈值 | 仅近似；无订单簿/封板队列。 |
| 不能退出 | 普通 pipeline 可跳过；比较 ledger 延迟 close exit 最多 5 bars | 有限实现，非真实委托。 |

## B. `packages/backtest/engine.py`（最小单资产模型）

`run_single_bar_strategy()` 是测试/演示级模型：T 匹配后，下一根 open 买入、**同一根 close 卖出**，将 `commission_bps + slippage_bps` 双边扣除。它没有组合仓位、固定持有期、多股票竞争、ST/涨跌停/停牌、印花税、最低佣金或整手逻辑，不应作为生产策略回测器。

## C. vectorbt / Backtrader 复核

- `verify_fixed_horizon_portfolio()`：对单序列把 close signal `.shift(1)` 成 T+1 open entry，`horizon_bars>=2`，VectorBT `Portfolio.from_signals()` 用比例 `fees/slippage`。
- `run_fixed_wide_spike()`：三条固定烟雾策略（SMA 10/30、RSI 14、20 日动量），shared cash，初始 100,000、每次 value 5,000；源码明确 `nonadjudicable`，且注释说明 VectorBT 不能裁定 A 股停牌/涨跌停。
- `BacktraderVerification.verify_fixed_horizon_candidate()`：`cheat_on_open=True`，前一日 signal 在 current open 提交；固定 horizon close。只设 commission，未设滑点、印花税、最低佣金、整手、涨跌停/停牌约束。

## D. 候选比较的逐日组合 ledger

证据：`packages/research/candidate_comparison.py`、`comparison_panel.py`；真实协议 `data/candidate_comparisons/g_20260809_01/comparison_protocol.json`。

- 它替代了“单资产 vectorbt 可代表 Top-K”的错误假设，使用逐日 `cash/positions/equity` ledger。
- T 收盘信号，T+1 开盘；同日竞争按 `sha256(seed|candidate|symbol|signal_date)` 排序；最多 20 个等权 slot；禁止同 symbol 重叠仓位。
- exit 必须 close 可交易；不可退出会延迟到首个可交易 close，最多 5 bars，否则 ledger 阻断。
- 基准成本为单边 3+5 bps，压力为 2x/3x；仍没有真实税费、最低佣金、整手、冲击成本、排队成交和账户级风控。

## 调仓逻辑

不存在统一的“每日调仓策略”。主研究为独立事件 + 固定 holding horizon；候选组合的 slot 在 entry/exit 时变化；VectorBT smoke 为各策略 entry/exit boolean；并无已实现的 Top-N 定期调仓、动态止损止盈、真实订单管理或实盘持仓同步。
