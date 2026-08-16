# 10 · 已知问题、缺口与风险

以下为代码、当前 README/状态文档、真实工件交叉审计所得；“未实现”不等于未来计划不存在。

## P0：不能宣称实盘可用

1. **没有通过最终锁箱的策略。** 当前正式 ROC/RSI/breakdown 全部 `research_eliminated_event`；lockbox 尚未读取（`docs/RESEARCH_CONCLUSIONS.md`、`comparison_result.json`）。
2. **不存在实盘执行系统。** 没有 broker adapter、订单、账户、持仓、整手、税费、最低佣金、风控/止损、实时交易日流程。MVP 也明确“不做自动下单”（`README.md`）。
3. **PIT 只覆盖部分维度。** 日期级 universe 在代码中可用，但财务发布日期、公告时刻、历史成分股完整来源、供应商修订、正式停复牌/涨跌停数据均未闭环。

## P1：回测真实性与一致性

4. 主事件 Outcome 不是完整 portfolio backtest；其费用只有 bps，缺真实 A 股税费/最低收费/整手/容量/冲击。
5. `execution.assess_execution()` 的涨跌停/停牌判定是日线近似；不能知道开盘能否成交、封单/排队、盘中停复牌或特殊涨跌幅规则。
6. `LocalParquetMarketData` 依赖 adjusted `trend_cache`；`prev_close` 从当前 adjusted close shift 生成，复权/原始价的涨跌停口径可能不一致。
7. VectorBT smoke 明确为 `nonadjudicable`；Backtrader adapter 没有 A 股约束，且只设 commission。
8. Candidate comparison 已有更好的逐日 ledger，但仍用 20 slots、等权、bps 成本，不适配小资金 Top1/Top5 真实账户。

## P1：验证与过拟合

9. 2022–2026 已被探索和比较读取，不能再称 fresh OOS。未来 lockbox 还没有执行结果。
10. 有 walk-forward folds，但没有按 fold 训练/调参/重训并聚合的 ML 流程；它是验证切分记录，不是完整 rolling deployment。
11. **未实现** Bootstrap、block bootstrap、Monte Carlo、permutation/White Reality Check、SPA、Deflated Sharpe。
12. FDR、成本压力、regime、多 horizon、Jaccard 是好门禁，但不能消除所有 data snooping 和人类 grammar/筛选自由度。

## P1：数据与文档不一致

13. `02_ARCHITECTURE_REPOSITORY.md`/`MASTER_IMPLEMENTATION_PLAN.md` 描述 PostgreSQL、pgvector、Redis、MLflow 等目标态；当前 README/`IMPLEMENTATION_STATUS.md` 正确声明无 SQL 文件运行时。外部评估者不能把目标态拓扑当已部署事实。
14. `EvidenceRepository` 是内存对象模型；真实持久知识库是 `FileKnowledgeRepository` JSON 文件。不能称已有数据库证据库。
15. 当前数据目录很大：`strategy_test_executions` 约 46.2GB、`candidate_comparisons` 约 3.9GB（审计盘点时）。没有通过 SQL/对象存储/生命周期管理解决容量问题。
16. 本次工作树已有未提交 Gen3/VectorBT 改动；早期 Campaign 的 code snapshot 与当前 working tree 不是同一事实，重跑必须新建协议/快照。

## P2：AI/运维与产品化

17. AI 仅用于受证据约束问答，不能自主发现/回测/保存研究计划；“多 Agent”主要是确定性角色工作流，不是自主 LLM team。
18. 无持久 LangGraph checkpoint、无 prompt/version registry、无 LLM eval set、无 token/cost/latency observability、无新闻/公告研究 agent。
19. Docker Compose 存在，但 `docs/IMPLEMENTATION_STATUS.md` 记录当前工作站未做 Docker 实机验收。
20. 文件型控制面适合单机/小团队；多人并发、共享文件系统、故障恢复/审计原子性达到阈值时应迁移数据库与对象存储。

## 明确的 placeholder / synthetic / 内存实现

21. `scripts/run_qlib_topk_approx.py` 明确将部分 series 标记为 **reporting placeholder**，且不称为市场 benchmark；该脚本的输出不能作为真实基准回测结论。
22. `scripts/run_qlib_cross_section_holdout.py` 的直接 CLI E2E 使用 temporary synthetic provider，并把结果标为 `temporary_only=true`；它是管线冒烟测试，不读取冻结市场数据。
23. `packages/research/validation.py` 构造名为 `placeholder` 的 DataFrame 仅为把日期索引交给 `skfolio.WalkForward`；它不是行情/因子数据，但也说明该模块不训练模型。
24. `packages/evidence/service.py: EvidenceRepository` 是进程内内存 repository，用于定义未来事务边界；当前持久层是 `FileKnowledgeRepository`，不得把该类当生产数据库实现。
25. 本次搜索没有发现 Python 源码中的 `TODO`、`FIXME`、`NotImplemented` 或 mock 交易实现。大部分 `*.tmp` 命中是原子写入临时文件，属于故障安全机制，不是遗留临时代码。

## 未发现但必须谨慎的事项

- 未在代码审计中发现“使用未来 T+1 close/全天成交量过滤 T+1 开盘订单”的明显主路径错误；该边界有专门实现。
- 未发现自动下单接口。
- 以上不证明不存在数据供应商修订泄漏、策略研究者偏差或环境依赖差异；这些恰是目前最大现实风险。
