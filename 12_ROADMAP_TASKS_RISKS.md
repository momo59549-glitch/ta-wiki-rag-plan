# Roadmap、Backlog 与风险

## 1. 路线图

| 阶段 | 时长 | 交付 |
|---|---:|---|
| Phase 0 基线 | 1–2 周 | 状态机、事件、核心表、Compose、合成闭环 |
| Phase 1 MVP | 3–5 周 | 五个确定性 Agent、两个人工闸门、Streamlit |
| Phase 2 研究团队 | 4–6 周 | Research/Knowledge/Report、LlamaIndex、MLflow |
| Phase 3 产品化 | 6–10 周 | Next.js、多人、复杂成交复核、按压测扩展 |

长期阶段目标参考 `MASTER_IMPLEMENTATION_PLAN.md` 第 13–14 节；当前完成度与运行限制以 `docs/IMPLEMENTATION_STATUS.md` 和 `README.md` 为准。未通过门禁不得扩大股票池、规则数或 Agent 自主权。

## 2. P0 Backlog

- [x] ADR：Agent 编排、业务真相、双回测引擎边界
- [x] Research Case 状态机与非法转移测试
- [x] 文件型 Job/Event/Outbox/Dead-letter（SQL/Alembic 按门槛延期）
- [x] DatasetSnapshot 和质量报告
- [x] AKShare/Tushare adapter 契约及增量覆盖层
- [x] DSL → vectorbt adapter
- [x] Observation 唯一键和 OutcomeProtocol
- [x] LangGraph checkpoint/interrupt 适配
- [x] Hypothesis/Rule 两个审批 API
- [x] Experiment manifest（MLflow 按门槛延期）
- [x] future leak、事件幂等、审批绕过测试
- [x] Streamlit case timeline
- [x] Compose、备份与恢复 runbook（Docker 实机验收待环境具备）

当前收口项：Job payload 契约、Worker 租约心跳、端到端启动烟雾测试、审计/恢复演练和文档一致性。策略验证不在当前工作流中继续执行。

2026-08-09 研究里程碑：generation `g_20260809_01` 的 RSI、ROC、breakdown 正式比较全部为 `research_eliminated_event`；ROC 的相对第一不代表可交易。锁箱未读，禁止批准和发布。下一研究步骤不是在 2022–2026 上继续调参，而是为 Gen2 建立顺序试验台账、累计预算、父代去重和真正未来的新 OOS。

## 3. P1 / P2

P1：Research 只读分析视图、预注册与多重检验、LlamaIndex claim-evidence、KnowledgeCard、反例报告、数据源交叉核验、RBAC、10–20 条规则黄金集。

P2：Backtrader 复核、Next.js、Qdrant 压测决策、Celery/Kafka 容量决策、多频率、DVC 小型黄金数据、必要时 Kubernetes。

## 4. 风险

| 风险 | 控制 |
|---|---|
| Agent 越权/难复现 | Schema、白名单、显式状态、人工闸门 |
| 框架堆叠 | MVP 只用 LangGraph + Prefect |
| 行情不稳定 | 适配层、快照、质量门禁、多源抽检 |
| 过拟合 | 预注册、样本外、参数邻域、多重修正 |
| 未来函数 | 时间 ADR、因果黄金测试 |
| 成本失控 | case/月预算、批处理、缓存、失败早停 |
| 版权泄露 | entitlement、短引用、审计、签名 URL |
| 旧数据伪完整 | `legacy_unverifiable` 隔离 |
| 单引擎偏差 | 关键候选用独立实现复核 |
| 顺序试验污染 | 每代冻结协议、累计预算与父代；2022–2026 不再复用为新鲜 OOS |
| 研究产物误删 | 权威 shards/v4 panel/result/staging 默认保留；删除必须单独确认 |

## 5. 扩展门槛

Qdrant、Celery、Kafka、Kubernetes 必须由容量或 SLO 指标触发并记录 ADR。“以后可能需要”不是引入理由。
