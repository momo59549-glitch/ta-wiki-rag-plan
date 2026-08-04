# 从现有 TA Wiki + RAG 方案迁移

## 1. 迁移策略

采用原地演进，不另起仓库、不删除已有证据与规则设计。现有方案中成熟的“页码语义、证据 revision、规则 DSL、统一 CandleSeries、无未来函数、数据快照、审计”全部保留；改变的是产品中心和运行编排。

旧主线：

`Book → Evidence → Wiki → RAG → Rule → Scan/Backtest`

新主线：

`Data Snapshot → Observation → Outcome → Hypothesis → Backtest → Rule Version → Knowledge Update`

`Book/Evidence/Wiki/RAG` 作为闭环的证据入口和解释层，不再是主业务流水线。

## 2. 现有文件审阅结论

| 文件 | 保留 | 升级动作 |
|---|---|---|
| `01_PRODUCT_SCOPE.md` | 证据分层、用户角色、非目标 | 北极星改为“可验证研究闭环完成率”，增加研究团队角色 |
| `02_ARCHITECTURE_REPOSITORY.md` | 模块化单体、Outbox、不可变对象 | 加 LangGraph、Prefect、研究核心包和 Worker |
| `03_BOOK_INGESTION_EVIDENCE.md` | 整体保留 | 降为按需导入；不以全书蒸馏作为 MVP 阻塞项 |
| `04_DATA_WIKI_RAG.md` | revision、claim-citation、混合检索 | 研究数据库成为主模型；Wiki/向量索引均可重建 |
| `05_MARKET_DATA_VISION.md` | CandleSeries、数据质量、截图不猜测 | 优先结构化行情；Vision 延后且只做辅助核验 |
| `06_RULE_DSL_ENGINE.md` | 整体保留 | DSL 成为 Scanner 和两个回测引擎的唯一规则源 |
| `07_BACKTEST_NO_LOOKAHEAD.md` | 整体保留 | 加预注册、walk-forward、第二引擎复核和发布门禁 |
| `08_API_FRONTEND.md` | FastAPI、异步任务边界 | 加 research case、timeline、approval、agent run UI/API |
| `09_DEPLOY_SECURITY_COPYRIGHT.md` | 权限、版权、备份 | 加 Agent 工具权限、Prompt 审计、Compose 组件 |
| `10_TEST_EVAL_ACCEPTANCE.md` | 黄金集、因果测试 | 加状态机、事件幂等、恢复、审批绕过和闭环验收 |
| `11_MULTI_AI_EXECUTION.md` | 可保留为开发阶段协作参考 | 重新定义为运行时九 Agent 研究团队 |
| `12_ROADMAP_TASKS_RISKS.md` | 风险清单 | 重排为 Phase 0–3 和 P0/P1/P2 Backlog |
| `13_TEMPLATES.md` | Schema 化模板 | 增加每个 Agent 的 Prompt 合同、Hypothesis 和审批模板 |
| `MASTER_PLAN.md` | 历史基线 | 标记由 `MASTER_IMPLEMENTATION_PLAN.md` 接替，不删除 |
| `DELIVERY_MANIFEST.md` | 交付索引 | 加入新主计划和迁移文档 |
| `README.md` | 仓库入口 | 改为研究团队入口与阅读顺序 |

## 3. 目录迁移

在现有目录上新增，不移动已存在包：

```text
apps/
  api/
  worker_research/       # 新：Agent/闭环节点执行
  worker_data/           # 新：数据与批任务
  research_ui/           # 新：Streamlit MVP
packages/
  agents/                # Coordinator 与节点定义
  research/              # case/observation/outcome/hypothesis
  orchestration/         # LangGraph + Prefect 适配
  backtest/              # vectorbt/Backtrader adapters
  knowledge/             # LlamaIndex 与经验卡
  db/                    # ORM/Alembic/outbox
  contracts/             # API/event JSON Schema
infra/
  compose/
docs/
  adr/
  runbooks/
evals/
```

`packages/evidence` 与 `packages/rule_engine` 原样保留并被新包调用。

## 4. 数据迁移顺序

1. 新建 `research_cases`, `agent_runs`, `observations`, `outcome_protocols`, `outcomes`, `hypotheses`, `experiments`, `backtest_runs`, `qa_reviews`, `approvals`, `knowledge_cards`, `outbox_events`。
2. 给现有 rule/evidence/wiki 对象补 `status`, `schema_version`, `content_hash`（已有则复用）。
3. 将旧扫描结果导入 `observations`，标记 `migration_source`；缺少数据快照或规则版本的记录保持 `legacy_unverifiable`，不伪造关联。
4. 将旧回测结果导入只读历史表或 artifact manifest；只有具备完整 manifest 的结果才进入正式 `backtest_runs`。
5. 建当前 Rule Revision 指针，但所有旧规则先处于 `draft`，经 QA 后才 `published`。
6. 最后切换 Scanner 读取 `published` Rule Revision。

所有迁移先 dry-run，输出行数、孤儿、哈希和回滚 SQL；备份成功并抽样校验后执行。

## 5. 兼容与弃用

- 旧 Wiki/RAG API 保留一个小版本，只读优先；
- 新 API 使用 `/v1/research-*`，不在旧端点偷偷改变语义；
- 旧 task 状态映射到新 `jobs`，但不等同于 `research_case.state`；
- `OpenSearch` 若已部署可继续用；新安装默认 Postgres FTS + pgvector；
- 旧 `MASTER_PLAN.md` 保留为历史设计，不再作为排期真相；
- Vision、完整书籍 OCR、OpenSearch、Kubernetes 从 MVP 必选降为按需能力。

## 6. 两周迁移切片

### 第 1 周

- 接受主计划与 ADR；
- 建新表、事件 Schema 与状态机；
- 包装现有 Candle/Rule Engine；
- 用现有合成数据生成 Observation/Outcome；
- 加未来函数与幂等门禁。

### 第 2 周

- LangGraph 串起 Coordinator → Scanner → Reviewer → Backtest → QA；
- Prefect 调度数据同步和到期复核；
- Streamlit 显示 case timeline 和两个审批点；
- 完成一个规则端到端演示；
- 旧入口指向新 README。

## 7. 迁移验收与回滚

验收：

- 原有测试全部通过；
- 旧证据 ID、规则 revision 和引用仍可解析；
- 新闭环以固定 fixture 重跑完全一致；
- 重复事件不重复建 Observation/Outcome；
- 未批准规则不能被 Scanner 使用；
- 旧数据中不可验证部分被明确隔离。

回滚：

- 数据库迁移每批次可反向执行；
- 新消费者通过 feature flag 开启；
- 旧 API 在兼容期继续只读；
- 切换失败时恢复旧当前指针，不删除新表和审计记录。

