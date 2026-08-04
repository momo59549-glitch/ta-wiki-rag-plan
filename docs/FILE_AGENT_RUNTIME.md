# 文件型九 Agent 运行时

当前不接 SQL、LLM 或 LangGraph，但九个角色已经作为可审计的确定性节点运行：

| Agent | 运行产物/职责 |
|---|---|
| Coordinator | `case.json`，状态与发布锁 |
| Data | 数据快照与源配置 |
| Scanner | `observations.jsonl` |
| Reviewer | `outcomes.jsonl` |
| Research | `hypothesis_draft.json` |
| Backtest | `backtest_review.json` |
| Knowledge | `knowledge_card_draft.json` |
| Report | `research_run/report.md` |
| QA | `qa_review.json` 与结构化门禁 |

运行：

```powershell
python scripts\run_team.py --limit 20 --start 2020-01-01 --end 2026-07-24 --oos-start 2024-01-01
```

每个案例目录都有 `agent_runs.jsonl`，按 Coordinator → Data → Scanner → Reviewer → Research → Backtest → Knowledge → Report → QA 的顺序保留运行记录。

## 发布政策

- Research 只在样本外平均净超额为正且达到 `--min-oos-observations` 时产生候选；默认门槛 300；
- QA 不通过或没有候选时，案例状态是 `needs_more_evidence`；
- 即便 QA 通过并有候选，状态也只能是 `awaiting_human_approval`；
- 当前代码没有自动发布 Rule 的接口，`case.json` 固定标记 `blocked_until_human_approval`。

这是一条从确定性文件流到未来 LangGraph/PostgreSQL 的迁移边界：保留同样的角色、工件和状态，再替换存储/调度器即可。
