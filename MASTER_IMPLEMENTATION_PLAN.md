# 多 Agent 股票研究团队：主实施计划

> 状态：实施基线 v2.0  
> 定位：研究与教育系统，不是自动荐股或实盘交易系统  
> 迁移入口：`MIGRATION_FROM_CURRENT_PLAN.md`

## 1. 一句话目标

把现有 “TA Wiki + RAG + 股票分析” 改造成一个可审计、可复现、可人工批准的研究团队：

`Observation → Outcome → Hypothesis → Backtest → Rule Version → Knowledge Update`

书籍、Wiki 和 RAG 只提供证据、术语与第一版规则；长期核心资产是带数据快照、规则版本、结果和复盘记录的研究数据库。

## 2. 产品边界与关键原则

### 2.1 系统做什么

- 每日同步和质检 A 股行情，保存不可变数据快照；
- 用已发布规则扫描并生成 Observation；
- 到期后按预注册窗口计算 Outcome；
- 将异常、失败模式和条件差异形成 Hypothesis；
- 用固定数据、成本和规则版本执行可复现回测；
- 经 QA 与人工审批发布新 Rule Version；
- 更新可引用的经验卡、Wiki 和研究报告。

### 2.2 系统不做什么

- MVP 不做自动下单、券商账户、实时高频流处理；
- LLM 不直接生成或执行任意 Python/SQL；
- 回测好看不等于规则自动晋级；
- 未完成样本外验证、偏差检查与人工审批的内容不得成为生产规则；
- 不把模型对市场的自然语言判断冒充事实。

### 2.3 数据优先级

1. 可复现研究记录与已确认 Outcome；
2. 已批准 Rule Version 和回测结果；
3. 行情/公司行动/交易日历等版本化事实；
4. 书籍原文、论文、公告等外部证据；
5. Wiki 总结；
6. LLM 推断。

## 3. 总体架构

MVP 保持“模块化单体 API + 独立 Worker”，不拆微服务。

```text
Next.js / Streamlit
        |
     FastAPI
        |
  Coordinator (LangGraph)
        |
  +-----+---------+----------+-----------+
  |               |          |           |
Data/Scanner   Reviewer   Research   Report/Knowledge
  |               |          |           |
  +--------- Prefect flows / work queues--+
                       |
       PostgreSQL + pgvector + Outbox
          |          |          |
        MinIO      Redis      MLflow
          |
   Parquet market snapshots
```

职责边界：

- **LangGraph**：单个研究案例的有状态决策图、条件路由、暂停/恢复、人工审批点。
- **Prefect**：行情同步、每日扫描、Outcome 到期复核、批量回测、报告生成等定时/批量作业。
- **PostgreSQL**：研究对象、版本、状态、审计和 Outbox 的唯一真相。
- **Redis**：短期缓存、限流和可丢失的任务信号，不保存权威研究状态。
- **MinIO**：原始文件、Parquet、图表、报告和模型产物。

## 4. Agent 团队与权限

Agent 是有明确输入、工具、输出 Schema 和权限的角色，不是自由聊天人格。

| Agent | 核心职责 | 可调用工具 | 权威写入 | 禁止事项 |
|---|---|---|---|---|
| Coordinator | 建案、路由、预算、重试、暂停、汇总 | 状态查询、任务分派 | `research_cases`, `agent_runs` | 改行情、发布规则 |
| Data | 拉取、标准化、复权、日历、质检、快照 | AKShare/Tushare 适配器 | `datasets`, `data_quality_issues` | 填补未知值 |
| Scanner | 对已发布规则批量计算候选 | Rule Engine/vectorbt 指标 | `observations` | 修改规则参数 |
| Reviewer | 到期计算结果、失败归因、证据核验 | Outcome evaluator | `outcomes`, review notes | 选择性改变窗口 |
| Research | 比较分层结果，提出可证伪假设 | 只读 SQL、统计模板、RAG | `hypotheses` 草稿 | 任意 SQL/Python、发布结论 |
| Backtest | 预注册实验、运行及稳健性检验 | vectorbt；Backtrader 复核 | `backtest_runs`, artifacts | 自动调参至最优 |
| Knowledge | 将已批准结论生成经验卡与引用 | LlamaIndex 检索、模板 | knowledge draft | 将草稿标成已验证 |
| Report | 生成日报、研究备忘录和变更摘要 | 查询 API、图表模板 | `reports` | 隐藏失败或限制 |
| QA | 数据泄漏、引用、复现、契约和发布门禁 | 测试套件、清单 | `qa_reviews` | 自批自己提交 |

人工角色：

- **Research Lead**：批准 Hypothesis 进入正式回测；
- **Rule Owner**：批准 Rule Version；
- **Compliance/Content Reviewer**：批准受限证据发布与导出；
- **Admin**：权限、密钥和灾备，不代替研究审批。

同一 `research_case` 中，提出假设者不能是最终规则批准者。LLM Agent 永远没有 `publish` 权限。

## 5. Agent 状态机

### 5.1 Research Case 主状态

```text
draft
  -> data_pending -> data_ready
  -> scanning -> observation_ready
  -> outcome_waiting -> outcome_ready
  -> hypothesis_draft
  -> hypothesis_review [人工闸门 A]
  -> backtest_queued -> backtesting -> backtest_ready
  -> qa_review
  -> rule_review [人工闸门 B]
  -> approved -> knowledge_pending -> published
```

终止/旁路状态：`rejected`, `failed`, `cancelled`, `superseded`, `needs_data_fix`。

### 5.2 状态转移规则

- 每次转移记录 `from`, `to`, `actor`, `reason_code`, `input_hash`, `timestamp`；
- 只允许状态机声明的转移，API 不接受任意状态字符串；
- 重试创建新 `agent_run`，不覆盖旧输出；
- 人工暂停由 LangGraph checkpoint 恢复，审批决定写入 Postgres 后再继续；
- 任何数据快照、规则、提示模板或模型变更都会产生新 run；
- 预算超限、质量门禁失败、证据不足时停止，不让 Agent 自行放宽标准。

## 6. 研究闭环

### 6.1 Observation

Scanner 对 `dataset_snapshot_id + rule_revision_id + universe_id` 运行，输出条件逐项值、阈值、信号时间与可交易时间。唯一键防重：

`(instrument_id, timeframe, signal_at, rule_revision_id, dataset_snapshot_id)`

### 6.2 Outcome

在 Observation 创建时就冻结评估协议：

- horizon：1/3/5/10/20 个交易日；
- entry：下一可交易 bar 开盘；
- return 与 MFE/MAE；
- 停牌、涨跌停、退市和缺失处理；
- 基准与行业超额收益；
- 成本模型版本。

Reviewer 只能执行该协议，不能看到结果后改变定义。

### 6.3 Hypothesis

必须包含：可证伪陈述、适用总体、主指标、基线、预期方向、分层变量、样本外区间、多重检验方案、停止条件和来源。LLM 只能提交 `draft`。

### 6.4 Backtest

Backtest Agent 冻结 experiment manifest，先 vectorbt 批量筛选，再对候选用 Backtrader 或独立参考实现复核。必跑：

- 时间顺序 train/validation/test；
- walk-forward；
- 成本和滑点敏感性；
- 参数邻域而非单点最优；
- survivorship/look-ahead/corporate-action 检查；
- 与基准、简单规则和旧规则对照；
- 多重比较修正和样本量披露。

### 6.5 Rule Version 与 Knowledge Update

只有 `QA passed + Rule Owner approved` 才创建不可变 Rule Revision。发布后：

- Scanner 只使用 `published` 规则；
- Knowledge Agent 生成经验卡，明确适用市场、样本、版本和限制；
- Wiki 只链接到规则和证据，不复制为无版本“真理”；
- 旧版本保留，可回滚当前指针，不删除历史。

## 7. 开源复用决策

| 能力 | 决策 | 复用边界 |
|---|---|---|
| 行情 | **AKShare + Tushare 适配器** | AKShare 便于原型与交叉验证；Tushare 用于稳定 A 股数据。自建统一 Schema、质量检查、快照和授权管理，不把供应商对象泄漏到领域层。 |
| 批量回测 | **vectorbt 主引擎** | 复用指标、信号组合、Portfolio 与统计；自建 Rule DSL 编译、A 股成交约束、实验 manifest 和偏差门禁。 |
| 事件复核 | **Backtrader 可选适配器** | 只复核订单/成交时序和复杂持仓；不维护两套策略定义，均消费同一中间表示和数据快照。 |
| Agent 编排 | **LangGraph** | 复用 typed state、checkpoint、条件路由、interrupt/resume；领域状态仍在 Postgres，图状态不是唯一真相。 |
| CrewAI/AutoGen | **不进 MVP** | 更适合快速角色对话/多方会话；本项目需要显式状态、可恢复审批和确定性节点。可做隔离 PoC，不混入核心。 |
| 批处理调度 | **Prefect** | 复用 schedule、retry、deployment、work pool、可观测性；研究业务状态不放 Prefect 元数据库。 |
| Celery/Airflow | **MVP 不采用** | Celery 仅在短任务吞吐成为瓶颈时加；Airflow 仅在组织已有平台或大规模 DAG 治理时替换 Prefect。不要三者并存。 |
| API | **FastAPI + Pydantic** | API、认证依赖、OpenAPI、Schema 校验；领域规则放 packages，不写进路由。 |
| 数据库 | **PostgreSQL + pgvector** | 事务、版本、审计、Outbox、MVP 向量检索；OHLCV 主体放 Parquet。 |
| 向量库 | **Qdrant 可选** | 仅当向量量级、延迟或过滤压测不达标时引入；可重建，不做真相源。 |
| RAG | **LlamaIndex** | 复用 ingestion/retriever/reranker/引用接口；答案必须走 claim-evidence QA，不能写权威规则。 |
| 对象存储 | **MinIO** | S3 API、不变对象、版本/保留；Postgres 保存 manifest 和哈希。 |
| 实验追踪 | **MLflow** | 参数、指标、artifact、模型/实验对照；Rule 发布状态仍由领域表管理。 |
| 数据版本 | **manifest + MinIO 首选；DVC 可选** | DVC 只用于小团队离线黄金数据与 Git 协作，不管理持续增长的生产行情。 |
| UI | **Streamlit 内部 MVP，Next.js 产品化** | Streamlit 先交付审校/研究台；Next.js 在权限、长流程和多人协作成熟后替换。Open WebUI 仅作探索聊天入口，不作审批台。 |

二次开发边界：适配器、Schema、审计、A 股规则、研究闭环和审批是本项目代码；通用回测、OCR、向量索引、调度、对象存储、实验追踪直接复用。

## 8. 数据库最小模型

### 8.1 研究核心

| 表 | 关键字段 |
|---|---|
| `research_cases` | id, title, state, owner_id, priority, budget, current_graph_run_id |
| `research_case_transitions` | case_id, from_state, to_state, actor, reason, input_hash |
| `agent_runs` | case_id, agent_type, status, prompt_version, model, tool_policy, input/output hash, cost |
| `observations` | instrument, signal_at, tradable_at, rule_revision_id, dataset_snapshot_id, condition_values |
| `outcome_protocols` | horizons, entry_policy, benchmark, cost_model_id, missing_policy |
| `outcomes` | observation_id, protocol_id, returns, mfe, mae, status |
| `hypotheses` | claim, population, metric, baseline, split_plan, correction, status |
| `experiments` | hypothesis_id, preregistration_hash, dataset_snapshot_id, code_commit |
| `backtest_runs` | experiment_id, engine, config_hash, status, metrics, artifact_uri |
| `rules` / `rule_revisions` | semantic version, DSL, evidence, status, supersedes |
| `qa_reviews` | target_type/id, checks, decision, reviewer, evidence |
| `approvals` | gate, target, decision, approver, comment, decided_at |
| `knowledge_cards` | claim, scope, evidence links, confidence, valid_from/to |

### 8.2 平台与谱系

`datasets`, `dataset_snapshots`, `data_files`, `data_quality_issues`, `instruments`, `calendars`, `corporate_actions`, `sources`, `evidence_spans`, `wiki_entries`, `citations`, `reports`, `jobs`, `outbox_events`, `audit_log`。

所有研究表至少带：`tenant_id`, `created_at`, `created_by`, `schema_version`。JSONB 只存可演进附属字段；可查询的身份、状态、外键和时间必须是正常列。

## 9. API 与消息契约

### 9.1 API

- `POST /v1/research-cases`
- `GET /v1/research-cases/{id}`
- `POST /v1/research-cases/{id}/commands/{command}`
- `GET /v1/research-cases/{id}/timeline`
- `POST /v1/datasets/sync-jobs`
- `POST /v1/scans`
- `GET /v1/observations`
- `POST /v1/outcomes/evaluate`
- `POST /v1/hypotheses/{id}/submit`
- `POST /v1/backtests`
- `POST /v1/approvals/{gate}/{target_id}`
- `POST /v1/rules/{id}/revisions`
- `POST /v1/rules/{id}/publish`
- `GET /v1/reports/{id}`

命令端点要求 `Idempotency-Key` 和 `If-Match`；异步请求返回 `202 + job_id`。

### 9.2 事件信封

```json
{
  "event_id": "uuidv7",
  "event_type": "observation.created.v1",
  "occurred_at": "ISO-8601 UTC",
  "producer": "scanner-agent",
  "tenant_id": "uuid",
  "correlation_id": "research-case-id",
  "causation_id": "prior-event-id",
  "schema_version": 1,
  "idempotency_key": "string",
  "payload": {},
  "payload_sha256": "hex"
}
```

首批事件：

`dataset.snapshot_ready.v1`, `scan.completed.v1`, `observation.created.v1`, `outcome.due.v1`, `outcome.evaluated.v1`, `hypothesis.submitted.v1`, `backtest.completed.v1`, `qa.completed.v1`, `rule.approved.v1`, `rule.published.v1`, `knowledge.updated.v1`。

Postgres Outbox 与业务事务同提交。消费者以 `event_id` 幂等，失败进入 dead-letter 表；MVP 不引入 Kafka。

## 10. 调度

| 流程 | 默认时间/触发 | 所有者 |
|---|---|---|
| 交易日历/标的同步 | 每日 06:00 | Data |
| 日线增量与质量检查 | 收盘后 18:00 | Data |
| 已发布规则扫描 | 数据快照 ready 事件 | Scanner |
| Outcome 复核 | 每交易日 19:30 | Reviewer |
| 假设候选汇总 | 每周六 | Research |
| 已批准实验回测 | 人工批准/队列 | Backtest |
| 知识和周报 | 规则发布/每周日 | Knowledge/Report |
| 漂移与数据完整性 | 每日/每周 | QA |

每个流程有超时、并发限制、重试白名单和运行手册。数据错误不自动重试为“成功”；供应商限流使用退避和断点续传。

## 11. Prompt 与工具安全

系统 Prompt 的共同条款：

- 只接受与输出声明的 Pydantic/JSON Schema；
- 区分事实、外部观点、统计结果和推断；
- 引用必须是系统返回的稳定 ID；
- 工具白名单、参数上限、只读查询视图；
- 不得调整协议来迎合结果；
- 证据不足输出 `insufficient_evidence`；
- 输出不构成投资建议；
- Prompt 版本、模型、温度、工具响应哈希全部审计。

Agent 专用 Prompt 和输出模板见 `13_TEMPLATES.md`。模型输出先过 Schema、领域不变量与 QA，再允许状态转移。

## 12. 审计与人工审批

审计记录追加写：身份、Agent、模型、Prompt 版本、工具、输入输出哈希、成本、状态转移和审批。敏感原文不复制进日志。

强制审批点：

1. `hypothesis_review`：Research Lead 批准实验问题和协议；
2. `rule_review`：Rule Owner 查看样本外、成本敏感性、失败样本和 QA；
3. `knowledge_publish`：涉及版权原文或外部发布时由内容 Reviewer 批准。

拒绝必须给 reason code；修改后生成新 revision，不复用旧批准。

## 13. 分阶段路线图

### Phase 0：基线与骨架（1–2 周）

- 冻结领域词汇、状态机、事件 Schema 和 3 个 ADR；
- Compose 启动 Postgres/Redis/MinIO/Prefect/FastAPI；
- 建立研究核心迁移、合成行情和一个锤子线规则；
- CI 跑契约、未来函数、迁移和最小闭环测试。

验收：一条命令启动；固定 fixture 可重复跑出相同 Observation；所有产物有 hash。

### Phase 1：闭环 MVP（3–5 周）

- Data/Scanner/Reviewer/Backtest/QA 五个确定性 Agent；
- LangGraph case 状态机和两个人工闸门；
- AKShare/Tushare 适配器、vectorbt、Prefect；
- Streamlit 研究台：任务、Observation、Outcome、实验、审批、报告；
- 完成单市场、日线、1–3 条规则、固定股票池闭环。

验收：连续 10 个交易日无人值守同步/扫描/到期复核；任意结论可追溯至数据、代码、规则和审批；相同 manifest 复跑指标一致。

### Phase 2：研究团队（4–6 周）

- Research/Knowledge/Report Agent；
- LlamaIndex claim-evidence 检索；
- MLflow 实验比较、walk-forward 与参数稳健性；
- 10–20 条规则、市场状态分层、周报；
- 权限、审计导出、备份恢复演练。

验收：至少 3 个假设完成全链路，其中至少 1 个被门禁拒绝；引用黄金集通过；恢复演练满足 RPO/RTO。

### Phase 3：扩展与产品化（6–10 周）

- Next.js、多人协作、通知；
- Qdrant/独立 Celery 仅按压测引入；
- Backtrader 复杂成交复核；
- 多频率/更多数据源、研究组合与漂移监控。

验收：性能、权限、灾备、安全与研究复现 SLO 全部达标后再扩大范围。

## 14. MVP 验收指标

- 数据完整率 ≥ 99.9%，异常不得静默；
- Observation 逐条件解释覆盖率 100%；
- 同 manifest 重跑交易记录完全一致；
- 未来函数黄金测试 100% 通过；
- 事件重复投递不产生重复权威对象；
- 未审批规则发布成功次数 = 0；
- 报告中的可验证 claim 引用覆盖率 100%；
- 20 个黄金案例人工核对一致率 ≥ 95%；
- API p95（同步查询）< 800ms；批任务给出进度和可取消；
- 备份 RPO 24h、RTO 4h（MVP）。

## 15. 部署与成本

### 15.1 本地/单机 MVP

Docker Compose：FastAPI、Worker、Prefect Server、Postgres、Redis、MinIO、Streamlit。推荐 8 核、32GB RAM、1–2TB SSD；LLM 使用 API，不在单机部署大模型。

月成本粗估（人民币，取决于供应商和调用量）：

- 已有工作站：基础设施增量 0–300；
- 小型云主机 + 数据盘 + 备份：800–2,500；
- Tushare/商业行情授权：按实际套餐，单列预算；
- LLM：轻量闭环 300–2,000，设置 case/token 月预算；
- 对象备份：50–300。

### 15.2 小团队生产

2 个 API/Worker 节点、托管 Postgres、对象存储、独立备份与监控，约 3,000–10,000/月，不含专业行情授权和人力。Kubernetes 不是 MVP 前置条件。

成本控制：缓存 embedding、优先确定性算法、批量调用、分层模型、case 预算、失败早停；不得用更便宜模型绕过 QA。

## 16. 实施 Backlog（优先顺序）

P0：

- 状态机与迁移；
- 事件信封/Outbox；
- Data snapshot 与质量门禁；
- Rule DSL → vectorbt 适配；
- Observation/Outcome 协议；
- LangGraph checkpoint + 审批；
- 复现实验 manifest；
- QA future-leak 与幂等测试；
- Compose 和备份。

P1：

- Research Hypothesis 模板；
- LlamaIndex 引用；
- MLflow；
- Streamlit 审批台；
- 周报与漂移检测；
- 数据源交叉核验。

P2：

- Backtrader 复核；
- Next.js；
- Qdrant；
- Celery 或 Kafka（仅基于度量）；
- 多租户与更细 RBAC。

## 17. 完成定义

本计划完成不是“Agent 能聊天”，而是：

- 一个研究案例能在故障和人工等待后恢复；
- 每一步都由版本化输入驱动并产生 Schema 化产物；
- 失败案例和拒绝决定与成功结果同样保留；
- 新规则只有在预注册回测、QA 和人工批准后发布；
- 任何报告结论可沿谱系回到 Observation、Outcome、数据快照、规则、代码和证据。

