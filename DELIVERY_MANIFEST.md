# 交付清单与需求覆盖矩阵

> `MASTER_IMPLEMENTATION_PLAN.md` 保留为长期目标架构与历史实施基线；当前运行事实以 `README.md` 和 `docs/IMPLEMENTATION_STATUS.md` 为准。v1 总览与迁移说明已归档到 `docs/archive/plans/`。运行时 Agent、路线图与 Prompt 分别由 `11_MULTI_AI_EXECUTION.md`、`12_ROADMAP_TASKS_RISKS.md`、`13_TEMPLATES.md` 维护。

## 1. 文档清单

| 文档 | 主要内容 |
|---|---|
| `MASTER_IMPLEMENTATION_PLAN.md` | 长期目标架构与历史实施基线；不代表当前 PostgreSQL 已部署 |
| `docs/archive/plans/MASTER_PLAN.md` | 历史 v1 总览、目标、架构、技术栈、阶段、成功标准 |
| `docs/archive/plans/MIGRATION_FROM_CURRENT_PLAN.md` | 历史原地迁移说明 |
| `docs/IMPLEMENTATION_STATUS.md` | 当前实现、运行事实、已知限制与延期项 |
| `01_PRODUCT_SCOPE.md` | 产品目标、用户、范围、非目标、权限、指标 |
| `02_ARCHITECTURE_REPOSITORY.md` | 组件、仓库、依赖、环境、CI/CD |
| `03_BOOK_INGESTION_EVIDENCE.md` | 导入、OCR、页码、版面、证据链 |
| `04_DATA_WIKI_RAG.md` | 数据模型、Wiki Schema、混合检索、引用 RAG |
| `05_MARKET_DATA_VISION.md` | OHLCV、数据质量、截图视觉、候选筛选 |
| `06_RULE_DSL_ENGINE.md` | DSL、编译、规则执行、解释、版本 |
| `07_BACKTEST_NO_LOOKAHEAD.md` | 回测、成本、点时数据、防未来函数 |
| `08_API_FRONTEND.md` | API、异步任务、页面、交互、错误态 |
| `09_DEPLOY_SECURITY_COPYRIGHT.md` | 部署、运维、安全、隐私、版权 |
| `10_TEST_EVAL_ACCEPTANCE.md` | 测试、黄金集、量化门槛、发布验收 |
| `11_MULTI_AI_EXECUTION.md` | 角色、依赖、路径所有权、交接、DoD |
| `12_ROADMAP_TASKS_RISKS.md` | 分阶段任务、优先级、里程碑、版本与风险 |
| `13_TEMPLATES.md` | AI 任务、交接、阻塞、ADR、数据/模型/发布模板 |

## 2. 用户要求覆盖

| 要求 | 覆盖位置 | 状态 |
|---|---|---|
| 总览版 MASTER_PLAN | `docs/archive/plans/MASTER_PLAN.md` | 已归档保留 |
| 产品目标、范围、非目标 | `01_PRODUCT_SCOPE.md` | 完成 |
| 总体架构、仓库结构 | `02_ARCHITECTURE_REPOSITORY.md` | 完成 |
| 数据模型 | `03`、`04`、`05` | 完成 |
| 书籍导入/OCR/页码映射 | `03_BOOK_INGESTION_EVIDENCE.md` | 完成 |
| Wiki schema | `04_DATA_WIKI_RAG.md` | 完成 |
| RAG 混合检索 | `04_DATA_WIKI_RAG.md` | 完成 |
| 视觉识别 | `05_MARKET_DATA_VISION.md` | 完成 |
| 行情数据层 | `05_MARKET_DATA_VISION.md` | 完成 |
| 规则 DSL/规则引擎 | `06_RULE_DSL_ENGINE.md` | 完成 |
| 回测/防未来函数 | `07_BACKTEST_NO_LOOKAHEAD.md` | 完成 |
| API 契约/前端 | `08_API_FRONTEND.md` | 完成 |
| 部署/安全/版权 | `09_DEPLOY_SECURITY_COPYRIGHT.md` | 完成 |
| 测试/评测/验收标准 | `10_TEST_EVAL_ACCEPTANCE.md` | 完成 |
| 多 AI 分工、依赖、交接、I/O、DoD | `11_MULTI_AI_EXECUTION.md` | 完成 |
| 阶段任务、优先级、MVP/后续版本 | `12_ROADMAP_TASKS_RISKS.md` | 完成 |
| 里程碑和风险 | `12_ROADMAP_TASKS_RISKS.md` | 完成 |
| 可复制实施模板 | `13_TEMPLATES.md` | 完成 |

## 3. 当前交付状态

- 2026-08-09：generation `g_20260809_01` 与三候选正式 comparison 已归档；三者均为 `research_eliminated_event`，result hash 为 `sha256:e38d07cabb182c5f8de97a1149d0b1ae172638dd7b44daee43dbdea6cf39cebb`；
- 锁箱未读，批准与发布禁止；ROC 仅相对第一，不代表可交易；
- 权威 shards、正式 v4 panel/result/staging 保留，合并 JSONL 为可重建兼容视图；本轮未删除数据；
- 候选比较相关回归由主代理验证 26/26。完整环境仍可能缺少可选依赖 `pypdf`，因此不声称全套测试无条件全绿；
- Markdown 相对链接和文件引用在本次整理后重新检查。
