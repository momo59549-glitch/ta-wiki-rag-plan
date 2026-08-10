# 框架实现状态

更新时间：2026-08-09。这里描述已经运行的代码与正式研究结果；长期目标架构和历史实施基线见 `../MASTER_IMPLEMENTATION_PLAN.md`。两者冲突时，本文件与 `../README.md` 描述当前无 SQL 文件运行时的事实。

## 已完成

- Coordinator、Data、Scanner、Reviewer、Research、Backtest、Knowledge、Report、QA 九类 Agent 的确定性运行骨架与审计产物；
- Research Case 显式状态机、非法跳转阻断、LangGraph 人工中断适配；
- 文件型 Job、Event、Outbox、Dead-letter、幂等、取消、进度、Worker 排他领取与超时重排；
- FastAPI Case/Job/Timeline/Report/Approval/Knowledge API；
- Hypothesis 与 Rule Version 两个不同人员审批闸门；
- Streamlit 研究台、角色/API Key、案例时间线、任务、审批和知识检索；
- Claim-evidence KnowledgeCard、EPUB 章节/EvidenceSpan 引用校验、本地 LlamaIndex BM25；
- Wiki Answer Agent：DeepSeek Anthropic 兼容适配、证据约束提示、无证据拒答、模型故障降级、引用与限制结构化返回；真实模型生成和精确形态证据隔离已验收；
- 策略测试准备门禁：强内容数据快照、运行代码快照、实验预注册、未来锁箱、真实 Walk-forward folds、成本压力场景、试验预算和全量行情质检；
- Tushare 历史补缺、交易日增量覆盖层、断点续跑，原始历史缓存不覆盖；
- Prefect 定时流程适配，本地文件 Worker 可脱离 Prefect Server 执行；
- RBAC、API 哈希链审计、本机启动/停止、备份脚本、Compose 和运行手册。

## 已完成的收口项

- 当前代码 Campaign `campaign_20260805T170009Z` 的 14/14 策略测试准备门禁通过；复用并重新校验 5,874 个标的、17,929,117 行强数据快照，未执行策略；
- `derive_strategy_test_campaign.py` 可从已就绪 Campaign 派生新代码绑定，保留旧 Campaign 不可变并重跑全部门禁；
- 行情适配器前收盘计算由逐行切片改为一次 `shift(1)`，消除全市场质检的 O(n²) 路径；

- 各 Job 类型的 payload schema、白名单和 API→队列→Worker→产物→事件端到端测试；
- Worker FIFO 领取、长任务租约心跳和异常超时重排；
- Job 文件路径限制在项目根目录和显式配置的只读行情根目录；
- Research Case→双人审批→规则注册→第三人 Knowledge 审校→检索的完整治理测试；
- 审计链与备份 manifest 的只读完整性校验命令；
- 安全隔离恢复脚本：拒绝覆盖，恢复前后逐文件校验 SHA-256；
- H 盘全范围备份恢复演练：1,267 个文件、299.95 MiB，书籍、证据 manifest 和全量案例均覆盖；
- 主目录 API/UI/Worker 真实 Job 验收和本地 Prefect Server Flow Run 验收；
- Tushare 真实单交易日增量验收：2026-08-04，5,529 个覆盖文件，0 失败，检查点不含 Token；
- README、文件 MVP、Roadmap 和运行手册的当前状态同步。
- 自动发现 generation `g_20260809_01` 已完成；固定 RSI、ROC、breakdown 三候选经过强分片 Case、正式 v4 panel、代码快照绑定、预注册统计与组合门禁比较，全部为 `research_eliminated_event`；
- 正式 comparison result hash：`sha256:e38d07cabb182c5f8de97a1149d0b1ae172638dd7b44daee43dbdea6cf39cebb`。ROC 只是三者内部相对第一，不构成正收益、可交易、批准或发布结论；最终锁箱未读；
- 候选比较相关回归由主代理验证为 26/26。不要据此夸大全套环境状态：完整测试环境仍可能因缺少可选依赖 `pypdf` 而无法全绿；
- 本轮未删除数据；权威 shards、正式 v4 panel、comparison protocol/result/staging 保留，合并 JSONL 仅为可重建兼容视图。

## 仍需外部环境

- 安装 Docker 后执行 Compose 构建和容器健康检查；
- 由运维决定是否常驻 Prefect Server；本地 Server 与真实 Flow Run 已验收；

## 明确延期

- PostgreSQL/pgvector：当前无需 SQL；文件并发、检索或治理达到门槛后迁移；
- MinIO、MLflow、DVC、Qdrant、Celery、Kafka、Next.js、Kubernetes：按容量和协作指标引入，不为技术清单而部署；
- Docker 实机验收：当前工作站未安装 Docker；
- 策略验证与参数搜索：按当前要求冻结，不是本轮框架完成条件。
- Gen2 自动发现：只能按顺序试验治理登记新 generation、父代、累计试验预算和真正未来的新验证窗口；截至父代研究结束日（当前治理口径 2026-08-04）已查看的数据不能再标为 fresh OOS，2026-09 后尚未到来的数据可预注册但到来前不能运行。
- Gen2 第一阶段：已实现 outcome-blind 预注册骨架（基础 DSL 规则加冻结的 benchmark regime、相对强弱、历史波动率和成交量 context filters）、跨代语义识别、append-only 全局 trial ledger、只读历史 inventory（按 rule logic 去重并冻结 hash）、预算 fail-closed 与 dry-run/fixture 测试；尚未运行任何 Gen2 筛选或正式全市场任务。详见 `GEN2_DISCOVERY.md`。
- Gen2 Stage2：未来执行/证据 contract 与合成 evaluator 实现中；正式 Gen2 protocol、账本、数据快照与收益筛选均未落盘。

## 完成口径

框架完成必须满足：本地一键启动；API 创建白名单 Job 后 Worker 自动执行；失败可追踪、取消、重试或超时重排；案例状态与双审批不可绕过；知识结论可追溯到证据；审计链可验证；备份可恢复；全量离线测试通过。
