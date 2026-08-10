# 三候选预注册比较

## 2026-08-09 正式结果

Generation `g_20260809_01` 的 RSI、ROC、breakdown 已完成正式比较，三者均为 `research_eliminated_event`，即在进入组合确认前已被事件研究门禁淘汰。ROC 仅是本次三者排序的相对第一名，不代表策略净收益为正、可交易、获批或可发布。正式 `comparison_result.json` 的 result hash 为 `sha256:e38d07cabb182c5f8de97a1149d0b1ae172638dd7b44daee43dbdea6cf39cebb`。最终锁箱保持未读；所有批准、发布与自动执行仍禁止。

权威产物保留在本代正式研究 shards 与 `../data/candidate_comparisons/g_20260809_01/` 下的 v4 panel、comparison protocol、result 和 staging。合并 JSONL 是可由 shards 重建的兼容视图，不是替代权威 shards 的新真相。本轮未删除任何数据；后续删除必须取得用户单独确认。

该流程只比较固定的 RSI、ROC、breakdown 三个已完成 Case，输出仅为 research ranking；不会新增候选、读取最终锁箱、批准或发布规则。固定的是三条已选规则的完整 semantic hash、logic hash、promotion receipt ID/hash，而不是历史 Case ID；因此旧的非分片 Case 会被拒绝，新派生的强分片 Case ID 可在 `prepare` 时一次性冻结。每个输入还必须具备 execution request 到 Campaign promotion receipt、frozen rule、experiment protocol、dataset/code snapshot 的完整一致绑定，替换规则或重复占用候选槽位都会被拒绝。

panel 不能手工提供。`build-panel` 先验证三份 Case 共用的 strong dataset snapshot、source root/dataset 和 OOS/lockbox 边界，再从正式 market source 构建。它逐 symbol 调用正式 `assess_execution`，生成开盘买入与收盘卖出的 executable/reason codes，并写成每-symbol 原子 JSONL shard；manifest 强绑定 source、snapshot、执行政策、日期范围和每个 shard 的行数/内容哈希。

`build-panel` 还会在新 panel 目录写一次 `builder_code_snapshot.json`，覆盖 repository 的 `packages`、`apps`、`scripts` 与 `pyproject.toml`；其 ID、固定相对路径和文件哈希进入 panel v4 identity。`prepare` 必须先确认当前项目仍与 builder snapshot 完全一致（包括没有新增未登记代码文件），否则要求用新目录重建 panel。随后它在 protocol 同目录一次性写 `comparison_code_snapshot.json`；若该路径已有文件，即使只是失败残留也拒绝覆盖。comparison protocol 冻结其 ID、绝对路径与 manifest hash，并要求 ID 与 panel builder snapshot 相同。

`run` 在创建 staging 或 result 前再次校验 comparison snapshot 内容、固定 sibling 路径、当前项目全部代码，以及它与 panel builder snapshot 的 ID 相等性。build-panel、prepare 或 run 之间发生任何代码变化，旧 panel/protocol 均不能继续使用；staging manifest 也会绑定同一 comparison code snapshot ID。

```powershell
python scripts/run_candidate_comparison.py build-panel `
  --rsi-case <case-dir> --roc-case <case-dir> --breakdown-case <case-dir> `
  --model-data <frozen-source-root> --panel-dir <comparison-panel-dir>

python scripts/run_candidate_comparison.py prepare `
  --rsi-case <case-dir> --roc-case <case-dir> --breakdown-case <case-dir> `
  --market-panel <comparison-panel-dir/panel_manifest.json> `
  --protocol <comparison_protocol.json> `
  --result <comparison_result.json>

python scripts/run_candidate_comparison.py run `
  --protocol <comparison_protocol.json> --output <comparison_result.json>
```

panel reader 只常驻小型 manifest，一次最多加载一个 symbol shard；不会对全市场 panel 使用 `read_text().splitlines()`。每次读取还会重新拒绝重复/乱序日期、NaN 或非正价格、缺失/不一致 `prev_close`、越过 OOS/锁箱、行数/日期边界/内容哈希错误，以及与正式 execution gate 不一致的 reason codes。

若 source 第一条可用历史 bar 恰为 IPO 首日且确实没有 `prev_close`，builder 不伪造前收，也不让全市场构建失败：该首行不进入可比较 panel，只将其有限正 `close` 作为下一条 bar 的前收校验基准。每-symbol shard 元数据和 manifest 汇总会记录跳过计数、日期、固定原因 `missing_prev_close_first_available_bar` 及校验用 close reference，并纳入 panel identity/hash。内部任意位置缺前收、下一行前收与 IPO close 不符、或 reader 中伪造 skip 元数据均拒绝；落在跳过日的事件因 panel 日历中不存在而拒绝。

`prepare` 会验证三条固定 promoted rule identity、QA、协议哈希、规则、数据/代码快照身份、completed checkpoint、全部研究分片和 panel 分片内容哈希、共同数据快照与 OOS 范围、成本口径，以及 `final_lockbox_consumed=false`；实际 Case/protocol ID 在 comparison protocol 中一次性冻结。协议和结果路径均只能创建一次。

主分析固定为 bearish、5/10/20 日、每标的 20 个交易栏 cooldown。事件重叠同时报告同标的同日期精确 Jaccard/包含率/独有比例，以及 ±5 交易栏 proximity coverage。收益先按信号日期做横截面均值，再对日期序列使用固定 lag（分别为 5/10/20）的 Newey-West/HAC；BH-FDR 的唯一 family 是三个候选乘三个 horizon 共 9 项。

淘汰同时检查 HAC CI、FDR、至少两个 horizon、2x 成本压力、年份稳定性、其他 regime 的显著负面证据，以及高重叠候选的增量/独有证据。任何幸存状态仍只是 `research_survivor`。

事件研究通过后状态先是 `event_study_survivor_pending_portfolio`，不能直接成为最终 survivor。组合确认对完整 OOS 交易日逐日净值收益（初始 NAV=1，现金日和无持仓日均保留零收益）按 horizon 作为固定 Newey-West lag；base ledger 的三个候选乘三个 horizon 共 9 项构成独立 BH-FDR family。至少两个 horizon 必须同时满足 ledger completed、净收益为正、HAC CI lower>0 且 FDR 通过；blocked、亏损或样本不足均转为 `research_eliminated_portfolio`。

每个 horizon 还实际重跑 2×交易成本 ledger，而不是用事件均值压力替代组合压力；至少两个 horizon 的 2×组合净收益仍须为正。协议明确 2× HAC 不作为额外门槛。只有通过组合确认的候选才参加同族/高重叠去冗余，并按组合通过 horizon 数、组合 HAC 下界、2×正收益 horizon 数优先，事件证据仅作次级排序。

现有 vectorbt adapter 是单资产接口，不能可靠执行跨标的 `max_positions`。本流程因此使用独立、可审计的逐日 cash/positions/equity ledger：T 收盘信号只能在 T+1 open 入场，同日竞争按冻结 seed 哈希排序，禁止同一 symbol 重叠持仓，真实复用释放的槽位，并逐日盯市计算净值、年化、回撤和换手。

每个 horizon 只接纳同时存在该 horizon Outcome 且在 panel 中拥有 `horizon + 5` 个后续交易栏的事件；尾部仅按长度预先 purge，绝不查看未来可成交性再决定是否入场。计划退出不可成交时，持仓延迟到最多 5 个后续栏中的首个可成交收盘；超过上限或仍未决会将整个 ledger 标为 blocked。严禁事后删掉失败退出，也严禁用 event mean 代替 portfolio PnL。

真实规模运行先按已提交的 source batch 逐批 join Observation/Outcome：内存中只保留当前 Observation batch，Outcome 按行流入并只留下 5/10/20 日紧凑字段。cooldown 后事件写入原子 event shard；交易计划按 candidate/horizon/entry-date 分片，逐日 ledger 每次只读取一个入场日，候选计划峰值受可用槽位约束，活跃 panel symbol cache 受 `max_positions` 约束。manifest 绑定 comparison hash、panel ID、三份 Case/protocol ID、所有 source commit hash 和每个紧凑 shard 内容哈希。

暂存目录同样 write-once：只要目录已经存在（包括崩溃残留），重试就拒绝继续，避免新旧分片混用；必须生成新的冻结协议/结果路径。最终结果只保存事件/成交/拒绝/尾部的计数、滚动哈希和至多 100 条确定性审计样本，不复制无界事件或交易数组。`peak_batch_observations`、`peak_batch_outcomes`、`peak_compact_events`、`peak_plans` 与 `peak_active_positions` 用于规模审计；其中 Outcome 实际逐行流式缓冲为 1 行。
