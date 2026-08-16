# 06 · 验证与抗过拟合审计

| 项目 | 状态 | 证据/限制 |
|---|---|---|
| Train / Validation / Test | 部分实现 | `PipelineConfig.out_of_sample_start` 将结果标为 in/out-of-sample；`protocol.py` 要求显式研究/验证/锁箱边界。没有机器学习训练器的 train/validation/test 数据加载与训练循环。 |
| Out-of-Sample | 已实现但历史已见 | `FileResearchPipeline` 报告 split；候选比较 OOS 为 2022-01-01..2026-08-04。README 明确 2022–2026 已被查看，不是新的 fresh OOS。 |
| 最终 Lockbox | 预注册/未读取 | 协议阻止 `end >= lockbox_start`；当前锁箱 2026-09-01 后。未有锁箱执行结果。 |
| Walk-Forward / Rolling Window | Walk-forward 已实现；rolling strategy retrain 未实现 | `validation.build_walk_forward_folds()` 调 `skfolio.WalkForward`，含 purge。Case 会生成 `walk_forward_validation.json`；但没有逐折调参、训练模型、re-fit 后部署。 |
| Purge / embargo | 已实现 | `purge_size=max(horizons)`，`WalkForwardConfig` 强制 ≥1；用于避免标签/持有期重叠。 |
| 参数敏感性 | 部分实现 | 离散 grammar 覆盖多窗口/阈值、2x/3x 成本压力、多个 horizon、regime、年度稳定性；没有系统化连续参数曲面/邻域稳健性报告。 |
| Bootstrap / Monte Carlo | 未实现 | 未找到 bootstrap、permutation、block bootstrap、Monte Carlo 组合路径模拟代码。 |
| 多重假设检验 | 已实现 | `statistics._apply_multiple_testing()` 用 `statsmodels.multipletests(..., method="fdr_bh")`；`rule_search.py` 明确对所有候选/组校正。 |
| 多策略相关性控制 | 部分实现 | `rule_search._deduplicate_candidates()` 用信号 Jaccard；比较结果有 event overlap。没有 White Reality Check、SPA、Deflated Sharpe 或全局层级模型。 |
| 成本稳健性 | 部分实现 | 预注册基准成本与 2x/3x stress；不含真实券商最低收费、税费、容量/冲击成本。 |
| 独立引擎复核 | 部分实现 | vectorbt/Backtrader adapter 存在；文档要求候选再复核。未看到每个最终候选都有完整订单级一致性比较。 |
| Code/data 可复现 | 已实现 | `snapshot.py` 强 SHA-256，`protocol.py`、`run_artifacts.py` 绑定/验证数据、代码、batch shard。 |

## 防未来函数机制

1. `Candle.available_at` 与 `RuleDefinition` 将信息边界固定到 T 收盘。
2. `Pipeline` 记录 `executable_at=next_bar_open`；entry 检查不读取 T+1 close/全天 amount（`execution.assess_execution()` 的 `price_at="open"` 分支）。
3. `SearchConfig` / `build_experiment_protocol()` 需要显式时间边界并拒绝读取 final lockbox。
4. `comparison_panel.build_comparison_panel()` 拒绝 OOS 跨过 lockbox，且记录 first-row `prev_close` 处理。
5. 每 observation 日期调用 PIT universe `active_on()`；无 manifest 直接标 `survivorship_unsafe`。

## 未能由代码消除的泄漏风险

- 复权 Parquet/供应商历史修订没有 availability/revision lineage；强快照只能锁住运行时当前字节。
- ST 由名称记录推导，且正式停复牌/涨跌停数据不是 PIT 事件源。
- 当前 OOS 已被搜索/比较使用；不能重新称为未见测试集。
- 规则开发、grammar 设计和人类审阅仍可能对历史结果适配；FDR 不会解决所有研究者自由度。
- Qlib 试验脚本存在，但 Qlib 数据集与生产事件 pipeline 并未形成统一、已审计的训练—验证—部署闭环。
