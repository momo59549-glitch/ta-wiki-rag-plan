# Gen2 候选发现：预注册骨架

Gen2 第一阶段只冻结候选空间、父代关系、全局试验预算和未来验证边界；**不读取最终锁箱、不对父代已查看区间运行新筛选、不创建全市场回测结果**。截至父代研究结束日（当前治理口径为 2026-08-04）已查看的数据不能重标为 fresh OOS；2026-09 之后尚未到来的数据可以预注册为未来验证，但在数据到来前不能运行。

## 设计

Gen2 采用 `base RuleDefinition + context filters` 的包装器。基础规则仍交给现有单证券 DSL 编译器；跨证券或跨序列条件不塞入 `rule_engine`：

- `market_regime`：冻结 benchmark 在信号日 T 收盘相对自身 SMA 的状态，T 收盘确认后才可用于 T+1 开盘；
- `relative_strength`：证券与同一冻结 benchmark 在**完全相同的 T 与 T-window 日期**上的收益差。任何缺失 benchmark 日期都会使过滤器为 false，绝不前向填充；
- `realized_volatility`：证券自身历史日收益的滚动标准差；
- `volume_category`：证券自身成交量相对滚动均量的类别。

候选是基础信号与全部 context filters 的 AND。独立 canonical semantic ID 包含基础规则的 ID/版本无关逻辑和排序无关的过滤器，因此仅改显示名称、参数标签或过滤器顺序不会消耗新的试验名额。它会标识“基础逻辑已在 Gen1 测过”的情况；带有实质新增冻结上下文的组合仍是新候选，不能伪装成 Gen1 从未覆盖。

## 试验账本与预算

全局账本目录由一个不可变 `policy.json` 和追加式 `entries/<generation_id>.json` 构成。每个 entry 固定代次、父代、候选 semantic ID、窗口角色、状态、淘汰原因、产物引用与 `final_lockbox_read=false`。已存在 generation、重复候选 semantic ID、损坏哈希或预算超限均 fail closed。

首个账本 policy 会只读扫描 `rule_search`、`auto_discovery` 和可验证的 frozen Campaign 裁决，按 canonical `rule_logic_hash` 去重，同时冻结 inventory hash、原始试验记录数、唯一逻辑数和总预算；之后不允许以更高预算或不同 inventory 改写 policy。任何历史 artifact 解析或哈希校验失败都会拒绝创建账本。Gen2 预注册在全局剩余额不足时拒绝创建。

## 安全运行

下面只生成内存中的预注册协议，**不写文件**：

```powershell
python scripts\run_gen2_discovery.py `
  --generation-id g_20260810_dry `
  --parent-protocol data\auto_discovery\g_20260809_01\auto_discovery_protocol.json `
  --research-start 2026-09-01 --validation-start 2026-09-02 `
  --research-end 2026-09-30 --lockbox-start 2026-10-01 `
  --candidate-budget 8 --global-trial-budget 256 --dry-run
```

实际预注册还需提供新的空 `--output-root`。该命令仍不接收行情路径，也不包含 screen/backtest 子命令；它只写 Gen2 protocol、candidate space 和一条全局账本 entry。它要求 `validation_start` 晚于父代 `research_end`，因此 2022–2026 传入时会拒绝。

未来第二阶段必须另行冻结数据快照、点时股票池、实际 benchmark 数据源和确定性筛选协议，并在真正未查看的未来窗口到达后才可运行。最终锁箱要保持更晚、独立且未读；预注册、筛选通过都不代表批准、发布或执行。

## Stage2 未来验证框架

`packages/research/gen2_validation.py` 现提供只接收内存合成 frame 的 Stage2 框架：它会复核冻结的 Gen2 protocol、父代、候选 semantic/base hash、benchmark、代码快照与严格的数据/PIT 契约；信号为 T 收盘、入场为 T+1 开盘、退出为计划 horizon 收盘。最终锁箱行、PIT `NA`、非完整 benchmark 对齐、尾部不可完成观察都会 fail closed 或写成独立的 `tail_purged` 审计行，绝不混入收益样本。

观察行按不可变 shard+sidecar 提交；提交临界区使用 create-new 独占锁，残留锁、损坏 sidecar、跨 shard 重复 key 都要求显式审计恢复。事件统计与 2x 成本压力分别按 candidate×horizon×signal-date 横截面日均做 HAC 和完整 FDR family。独立组合确认使用冻结日历与每日计划，固定等权、仓位上限、同证券重叠禁止、T+1 open、计划 close 退出、不可交易延迟并对未解决退出 fail closed；它不把事件均值冒充组合 PnL。

最小入口是 `scripts/run_gen2_validation.py`，仅有 `contract --dry-run`、写入指定空目录的 `contract` 和 `synthetic-smoke`；没有行情根目录、历史回测或全市场运行参数。合成 fixture/CLI 默认不写 `data/`。

这些是框架和合成测试，不是正式研究结论：**正式 Gen2 protocol、global ledger、future dataset/PIT snapshot、观察 shard、组合结果和任何收益结果仍未落盘**；2026-09 之后的真正未查看数据到来前不得执行或宣称收益。

Stage3 的 `packages/research/gen2_future_runner.py` 已提供依赖注入的 future-only 增量 runner：它只接受经绑定的 source/PIT provider、实际强快照与 revision、精确 benchmark、交易日历及 PIT manifest hash；没有市场根目录扫描。进度由写一次的逐日 PIT freeze、date receipt、observation shard 和 source revision chain 派生，**不**以可变 checkpoint 决定跳过。旧 receipt 固定其当时 revision，新 revision 必须精确接在唯一链头并用历史前缀证明旧 OHLC、benchmark、日历和 PIT 未回写。

Stage4 的 `packages/research/gen2_file_provider.py` 提供正式入口可用的本地 Parquet adapter：source-revision manifest 必须逐项列出证券、benchmark、calendar、PIT 文件及 size/SHA256，所有路径 resolve 后限定在显式 `--allowed-data-root` 内；绝不接受 `market-root` 或目录扫描。未来验证 CLI 的 `future-run` 也只接受该 manifest、冻结 protocol/Stage2/ledger、run root 和 as-of。waiting 路径仅校验协议和 manifest 身份，不打开 Parquet；进入验证窗口后才校验每个文件哈希、规范化日期与字段，并执行 runner。

这仍不是正式研究结果：正式 Gen2 protocol、ledger、source revision manifest、PIT/行情文件、date receipt、observation shard、组合确认及收益结果**均未在本项目正式 data 中落盘**。任何未来数据运行都必须由操作者另外生成写一次 manifest，并保持最终锁箱未读。

父代 rollover 也被冻结：`--parent-closure-result` 必须是哈希有效、全部候选已 research-eliminated、未读锁箱且禁止批准/发布的比较结果。其未读父锁箱仅在 Gen2 **预注册时**被记录为重新分配给子代 validation；子代 validation 不得早于该日期，且 Gen2 的新最终锁箱必须更晚（规划为 2027-09 之后）并仍未读。这不表示父锁箱会永久保持未读。

当前 Stage4 合成回归覆盖 4 项文件 provider 场景和 7 项 future runner 场景：Parquet 身份零读取、文件 hash/路径逃逸、OHLCV/布尔/PIT 输入约束、写一次 manifest、两 revision 的实际规范化历史前缀一致性，以及 receipt 与 PIT freeze 的交叉绑定。它们只使用临时 fixture，不代表行情或策略验证。

## 正式 Gen2 预注册（waiting）

2026-08-10 已一次性预注册 `g_20260810_01`，但未执行任何筛选、回测或 future runner。冻结 protocol 为 `gen2_55b5b42902e1287ebddca681`（`sha256:55b5b42902e1287ebddca68142c8ccd0a37ebf2f574fa0c5107bc8461814f85b`），含 8 个 outcome-blind candidates；当前 canonical Stage2 contract 为 `data/gen2_validation/g_20260810_01_r3/` 中的 `gen2_stage2_ff0399f0734c5551f878dd4a`（`sha256:ff0399f0734c5551f878dd4a879734c83259bcae4c16fdfb20e9516ef0655771`；code snapshot `sha256:d2d3fe3d2d8cbe1dd9b54cfc6a6fe7ec1159ab1f94a5be034943c07c95da43f7`）。

旧目录 `data/gen2_validation/g_20260810_01/` 与 `data/gen2_validation/g_20260810_01_r2/` 均保留以便审计，但它们的 contract 代码快照均在从未运行的情况下因后续 verifier 安全修复而失效；两者标记为 **never-run / invalidated_by_code_change**，不得用于 future-run。

父代 closure 使用已验证 comparison result hash `sha256:e38d07cabb182c5f8de97a1149d0b1ae172638dd7b44daee43dbdea6cf39cebb`，其未读父锁箱 rollover 为本代 2026-09-01 validation 起点；本代研究窗口为 2026-05-01 至 2027-08-31，新的最终锁箱自 2027-09-01 起。protocol 和 Stage2 contract 都记录 `final_lockbox_read=false`。

全局账本冻结 legacy unique logic=206；正式登记后 used=214、remaining=42（总预算 256）。`future_dataset_contract.json` 与 `future_pit_contract.json` 只声明未来 adjusted daily 数据、精确 benchmark/calendar、执行所需字段和 point-in-time membership 的逻辑身份。尚未创建实际 source revision manifest、PIT/行情文件、runner、date receipt、observation shard、outcome、组合确认或收益结论；当前状态为 **waiting for future data**，批准、发布和交易仍被禁止。
