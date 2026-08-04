# 总体架构、技术栈与仓库结构

> v2 架构更新：在既有模块化单体上增加 LangGraph Research Case 状态机与 Prefect 批处理编排；PostgreSQL 仍是唯一业务真相。Agent、研究数据库、消息契约和运行拓扑以 `MASTER_IMPLEMENTATION_PLAN.md` 为准。

## 1. 架构风格

MVP 采用“模块化单体 API + 独立异步 Worker + 明确领域边界”。不要一开始拆微服务；先通过数据库 Schema、包边界、事件契约和任务队列形成可拆分边界。

领域模块：

- Identity & Entitlement
- Source & Evidence
- Knowledge & Wiki
- Retrieval & Answer
- Market Data
- Rules & Signals
- Vision
- Backtest
- Jobs & Audit

## 2. 运行组件

| 组件 | 职责 | 状态存储 |
|---|---|---|
| `web` | 用户交互、图表、证据查看 | 无持久状态 |
| `api` | 认证、CRUD、编排、查询 | PostgreSQL |
| `worker-ingest` | 渲染、OCR、分块、嵌入 | S3、Postgres、索引 |
| `worker-research` | 扫描、视觉、回测 | S3、Postgres |
| `postgres` | 元数据、版本、权限、任务 | 主事务库 |
| `opensearch` | 全文、过滤、聚合 | 可重建索引 |
| `redis/queue` | 异步任务与短期缓存 | 非权威状态 |
| `object-store` | 原文件、页面、产物 | 不可变对象 |

## 3. 建议 Monorepo

```text
/
├─ apps/
│  ├─ web/
│  ├─ api/
│  ├─ worker_ingest/
│  └─ worker_research/
├─ packages/
│  ├─ contracts/          # OpenAPI、JSON Schema、事件契约
│  ├─ db/                 # ORM、迁移、种子
│  ├─ evidence/
│  ├─ retrieval/
│  ├─ market_data/
│  ├─ rule_dsl/
│  ├─ rule_engine/
│  ├─ vision/
│  ├─ backtest/
│  ├─ authz/
│  └─ observability/
├─ data/
│  ├─ fixtures/           # 可分发的小型合成夹具
│  ├─ golden/             # 受权限控制的黄金集清单
│  └─ schemas/
├─ evals/
│  ├─ retrieval/
│  ├─ rag/
│  ├─ ocr/
│  ├─ vision/
│  └─ backtest/
├─ infra/
│  ├─ compose/
│  ├─ kubernetes/
│  ├─ terraform/
│  └─ monitoring/
├─ docs/
│  ├─ adr/
│  ├─ runbooks/
│  ├─ data_cards/
│  └─ model_cards/
├─ scripts/
├─ tests/
└─ pyproject.toml / package.json
```

## 4. 包依赖规则

- `contracts` 不依赖业务包。
- `rule_dsl` 不依赖行情、视觉或回测；它只负责 Schema、解析与静态校验。
- `rule_engine` 依赖 `rule_dsl` 的中间表示，不依赖 Web/API。
- `vision` 和 `market_data` 都转换为统一的 `CandleSeries`，再调用 `rule_engine`。
- `backtest` 只消费版本化规则与时间对齐后的市场数据接口。
- `retrieval` 只返回证据引用候选，不直接生成最终产品文案。
- 跨领域写入通过应用服务；禁止前端或 Worker 绕过权限直接写数据库。

使用架构测试禁止循环依赖和越层导入。

## 5. 环境

- `local`：Docker Compose；合成数据；本地对象存储；可选离线模型。
- `test`：每次 CI 创建临时数据库/索引；固定种子。
- `staging`：去标识化或授权样本；接近生产拓扑。
- `production`：独立密钥、私网数据库、对象版本控制、集中审计。

环境配置使用显式 schema 校验。缺少关键配置时启动失败，不能使用不安全默认值。

## 6. API 与异步任务边界

低延迟请求（检索、Wiki 浏览、规则读取）同步返回。超过约 2 秒或资源不可控的任务异步化：

- 书籍导入/OCR/嵌入；
- 大范围候选扫描；
- 截图视觉解析；
- 回测与报告；
- 索引重建和来源删除。

异步任务状态统一为：

`queued → running → succeeded | failed | cancelled`

任务提供 `progress_current/progress_total/stage`，支持幂等键、取消、超时、重试和死信。重试不得重复创建权威对象。

## 7. ID、版本和命名

- 外部 ID：UUIDv7/ULID，避免暴露自增规模。
- 数据库内部可使用 bigint，但 API 不泄露。
- 版本实体采用不可变 revision；“当前版本”是可移动指针。
- 对象路径：`tenant/{tenant_id}/{domain}/{entity_id}/{sha256}/{artifact}`。
- 数据集、规则、模型、索引、提示模板均有语义版本或内容哈希。

## 8. 数据库与索引策略

- 事务真相在 PostgreSQL；OpenSearch/pgvector 可重建。
- 大型 OHLCV 不逐行塞入主事务表：按市场/频率/日期分区保存 Parquet，Postgres 保存 manifest、统计与谱系。
- 小规模/近期 bar 可在 TimescaleDB 或 Postgres 分区表缓存，但必须与权威快照对应。
- 使用 Outbox Pattern 同步索引：事务提交 domain event，worker 幂等更新索引。
- 索引文档永远携带 `tenant_id`, `entitlement_id`, `revision_id`, `visibility`，查询时服务端强制过滤。

## 9. CI/CD 门禁

每个变更至少运行：

- 格式与静态检查；
- 单元、契约、迁移、架构依赖测试；
- 最小端到端样例；
- DSL 黄金测试与未来函数测试；
- 依赖漏洞、密钥扫描、SBOM；
- API/Schema breaking-change 检查。

主分支产物按 commit 构建一次，通过 digest 晋级 staging/production；禁止生产环境重新构建不同镜像。

## 10. 架构 DoD

- 一条命令启动本地最小栈并加载合成夹具。
- API、Worker、迁移和索引初始化可重复运行。
- 所有组件有健康检查、结构化日志和 trace_id。
- 备份与索引重建均有 runbook。
- 包依赖和领域所有权通过自动测试执行。
