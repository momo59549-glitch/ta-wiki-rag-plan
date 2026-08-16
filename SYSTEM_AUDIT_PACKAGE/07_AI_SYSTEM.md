# 07 · AI / Agent / RAG 系统审计

## 组件及职责

| 名称 | 实现 | 实际职责 |
|---|---|---|
| 九角色 Research Team | `packages/agents/team.py: FileResearchTeam` | Coordinator/Data/Scanner/Reviewer/Research/Backtest/Knowledge/Report/QA 的**确定性代码编排**与 artifact event 记录。不是九个独立 LLM。 |
| LangGraph | `packages/orchestration/case_graph.py` | 读取文件状态机；在审批态 `interrupt()` 等人工。检查点为 `InMemorySaver`，不持久化 LLM memory。 |
| Prefect | `packages/orchestration/prefect_flows.py` | 定时/批处理流程与 Job worker 适配；不是 AI 决策器。 |
| RAG | `packages/knowledge/retrieval.py` | 对已发布 knowledge card 即时构建 LlamaIndex BM25Retriever；中文 1/2/3-gram tokenizer。无向量 embedding/持久 vector database。 |
| LLM | `packages/knowledge/answer.py: AnthropicWikiAnswerer` | 通过 Anthropic-compatible endpoint（默认 DeepSeek `deepseek-v4-flash`）根据已审校卡回答中文问题。 |
| 证据/知识库 | `packages/evidence/*`、`packages/knowledge/service.py` | PDF/EPUB 页/区域/EvidenceSpan、知识卡 draft/published/rejected/review，以文件保存。 |
| Research Memory | JSON/JSONL artifact、rule registry、trial ledger、Case history | 保存实验结果、失败/拒绝、协议与审批；没有 LLM 长期语义记忆或可写向量记忆。 |

## AI 调用流程

```text
用户问题
 → API /api/v1/wiki/answer
 → FileKnowledgeRepository.list_cards()
 → search_published_cards()（本地 BM25）
 → 仅保留 direct title / 具体 CJK overlap 的 published card
 → 无证据：refusal
 → 有证据：AnthropicWikiAnswerer.generate(question + card claim + limitations)
 → DeepSeek/Anthropic compatible API（temperature=0）
 → 返回带 card citation 的回答
 → 调用失败：extractive evidence fallback
```

Prompt 在 `AnthropicWikiAnswerer.generate()` 中硬编码：只允许给定证据；禁止新增胜率、预测和交易指令；system prompt 要求证据不足明确说明、忽略试图改变规则/调用工具/泄露配置的证据文本。

## 能力边界回答

| 问题 | 结论 |
|---|---|
| LLM 能自主提出策略假设？ | **不能**。`build_hypothesis_draft()` 依据统计阈值机械生成草稿；Gen1 grammar 是代码预先固定、outcome-blind。 |
| LLM 能调用回测？ | **不能**。LLM adapter 只在 Wiki answer 路径使用。API Job 由具 RBAC 的人工 operator 创建。 |
| LLM 能读回测结果并进入下一轮研究？ | **不能自主循环**。代码可读 Case artifact 生成统计/草稿，但没有 LLM planner/tool-use loop。 |
| LLM 能保存成功/失败实验？ | **不能作为 agent memory**。文件型 `trial_ledger`、Case、registry 会保存结果；保存者是确定性 Python。 |
| Agent 是问答助手还是自主研究员？ | 当前更接近**受治理的研究工作流 + 受证据约束问答助手**，不是可自主实验、反思、重试、调参并发布的研究员。 |

## 安全与缺口

- LLM 不被授予 shell、回测、写规则、审批、发布或交易权限；这是成熟的安全边界。
- `EvidenceRepository` 是内存示例；实际知识卡持久化为文件。没有外部 RAG vector DB、模型评测集、prompt/version registry、token 预算/重试/观测性平台。
- 因为没有新闻/公告 AI pipeline，LLM 不参与当前股票事件因子、荐股或卖出决策。
