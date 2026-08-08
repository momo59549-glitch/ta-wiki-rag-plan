# 框架实现状态

更新时间：2026-08-05。这里描述“已经运行的代码”，目标架构仍以 `MASTER_IMPLEMENTATION_PLAN.md` 为准。

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

## 仍需外部环境

- 安装 Docker 后执行 Compose 构建和容器健康检查；
- 由运维决定是否常驻 Prefect Server；本地 Server 与真实 Flow Run 已验收；

## 明确延期

- PostgreSQL/pgvector：当前无需 SQL；文件并发、检索或治理达到门槛后迁移；
- MinIO、MLflow、DVC、Qdrant、Celery、Kafka、Next.js、Kubernetes：按容量和协作指标引入，不为技术清单而部署；
- Docker 实机验收：当前工作站未安装 Docker；
- 策略验证与参数搜索：按当前要求冻结，不是本轮框架完成条件。

## 完成口径

框架完成必须满足：本地一键启动；API 创建白名单 Job 后 Worker 自动执行；失败可追踪、取消、重试或超时重排；案例状态与双审批不可绕过；知识结论可追溯到证据；审计链可验证；备份可恢复；全量离线测试通过。
