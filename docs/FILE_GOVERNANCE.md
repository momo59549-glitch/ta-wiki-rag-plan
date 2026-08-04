# 文件型人工审批与规则版本库

Agent 只能生成 `hypothesis_draft.json`、`qa_review.json` 和 `knowledge_card_draft.json`。它们不具备发布权限。

只有满足以下条件时，才可创建审批请求：

1. QA 为 `passed`；
2. Research 已有满足最小样本与样本外净超额门槛的候选；
3. `case.json` 仍标记为 `blocked_until_human_approval`。

请求审批：

```powershell
python scripts\review_case.py data\research_cases\<case_id> --request
```

人工决定（示例；仅在你明确决定后执行）：

```powershell
python scripts\review_case.py data\research_cases\<case_id> `
  --decision approve `
  --approver "research-lead" `
  --comment "已复核样本外、成本和失败样本"
```

批准后写入 `data/rule_registry/<rule>-<version>.json`；同版本已存在时会失败，不会覆盖历史。所有批准规则仍是 `research_only`，不构成交易或投资建议。
