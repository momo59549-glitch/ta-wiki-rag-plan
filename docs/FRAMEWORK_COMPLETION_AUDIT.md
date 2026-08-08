# 多 Agent 研究团队框架完成审计

审计日期：2026-08-05。审计范围是当前无 SQL、单机/小团队研究框架；策略参数搜索和收益验证按要求冻结，不属于完成条件。

| 要求 | 当前证据 | 结论 |
|---|---|---|
| 九类 Agent | `packages/agents/team.py` 产生 Coordinator/Data/Scanner/Reviewer/Research/Backtest/Knowledge/Report/QA 独立审计记录 | 完成 |
| 显式状态机 | `packages/orchestration/state_machine.py`；非法跳转和审批绕过测试 | 完成 |
| Job、队列和事件 | 文件 Job/Event/Outbox/Dead-letter、FIFO claim、租约心跳、取消、进度、幂等、超时重排 | 完成 |
| 自动执行 | API 创建白名单 Job 后本机 Worker 自动完成；真实烟雾测试产生 5 条事件 | 完成 |
| API 与 UI | FastAPI 0.3、Streamlit 研究台；本机 API/UI/Worker 启停和 HTTP 健康检查通过 | 完成 |
| 权限、审计和人工审批 | API Key、RBAC、哈希链审计；Research Lead、Rule Owner、Content Reviewer 三人职责隔离 | 完成 |
| 研究治理闭环 | Case→Hypothesis 审批→Rule 审批→规则注册→Knowledge 审校→检索 E2E | 完成 |
| 知识与 Wiki 定位 | KnowledgeCard claim-evidence、EPUB/EvidenceSpan 引用、本地 BM25；研究工件无来源证据时不能发布 | 完成 |
| Wiki Answer Agent | DeepSeek Anthropic 兼容适配、已审校证据约束、无证据拒答、模型失败安全降级、FastAPI/Streamlit 入口；研究台支持单次密码输入且不写文件/审计 | 完成（真实 `deepseek-v4-flash` 生成已验收） |
| 策略测试准备 | 强数据/代码快照、预注册、锁箱、Walk-forward/FDR、成本压力、试验预算；全量 Campaign 14/14 门禁通过 | 完成 |
| 行情数据 | 只读 `trend_cache`、历史补缺缓存、每日增量覆盖层、点时股票池和覆盖率审计 | 完成 |
| 真实增量连接 | 2026-08-04 拉取完成，5,529 个 Parquet 覆盖文件、0 失败；Token 未写入检查点 | 完成 |
| 调度 | Prefect Server 健康，真实 `daily-research-operations` Flow Run 为 Completed；本地代理绕过已固化 | 完成 |
| 开源复用边界 | `MASTER_IMPLEMENTATION_PLAN.md` 明确 vectorbt/Backtrader、Tushare/AKShare、LangGraph/Prefect、LlamaIndex、FastAPI、Streamlit 及延期组件 | 完成 |
| 数据存储 | 当前 JSON/JSONL/Parquet 为权威存储；按用户要求不引入 SQL；PostgreSQL/pgvector 仅保留迁移门槛 | 完成 |
| 部署与运维 | 本机一键启停、Dockerfile/Compose、备份、恢复、完整性校验和运行手册 | 完成 |
| 备份恢复 | 控制面、知识/证据、研究案例、报告与检查点均纳入；1,267 文件、299.95 MiB 隔离恢复逐文件 SHA-256 一致 | 完成 |
| 路径安全 | Job 路径只能位于项目根或 `TA_MODEL_DATA_ROOT`；路径越界测试通过 | 完成 |
| 文档交付 | MASTER、迁移说明、README、架构、Multi-Agent、Roadmap、Prompt、API/运维和实现状态均已更新 | 完成 |
| 测试基线 | H 盘主项目 `python -m unittest discover -s tests -v`：83/83 通过 | 完成 |

## 有意延期而非缺失

- PostgreSQL/pgvector、MinIO、MLflow、DVC、Qdrant、Celery、Kafka、Next.js、Kubernetes 只有在容量、并发或组织治理指标触发时引入。
- 当前工作站未安装 Docker，因此没有执行容器构建；本机原生部署已完整验收，Compose 是替代部署方案而不是运行前置条件。
- 视觉截图识别、完整 OCR/RAG 黄金集和多租户产品化属于后续产品能力，不伪装成当前文件型研究框架能力。
- 策略验证、调参和收益结论保持冻结；已有防未来函数与执行约束测试仅作为安全门禁保留。

## 运行证据

- API/UI/Worker：健康检查成功，白名单覆盖率 Job `succeeded`，产物生成，随后所有进程停止；
- Windows PowerShell 5.1：同用户命令实际启动成功；重复启动被阻止，停止后状态归档，重复停止安全返回；
- Prefect：本机 Server `/api/health` 返回 200，Flow `spiked-tamarin` 完成；
- 增量数据：`H:\股票模型\Model\data\tushare_incremental_cache` 共 5,529 个文件，样例 `000001` 与历史基线合并后最后交易日为 2026-08-04；
- 最终安全审计：项目内 Token 明文匹配 0，检查点敏感字段 0，API 审计链 78 条记录有效；
- 最新 API/UI 运行验收：`/healthz`、`/api/v1/wiki/status`、证据模式 `/api/v1/wiki/answer` 均为 200；
- 真实 Wiki 生成验收：乌云盖顶回答为 `llm_grounded`，只引用直接匹配知识卡；相反的刺透形态限制不会混入；
- 当前代码 Campaign `campaign_20260805T170009Z`：14/14 门禁通过，`strategy_executed=false`；
- 安全清理：所有烟雾测试使用隔离控制目录，临时服务、队列、备份和恢复目录在验收后删除。
