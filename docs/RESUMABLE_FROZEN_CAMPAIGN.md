# 冻结 Campaign 的安全断点恢复

全市场研究按固定数量的标的分批。每批先原子写入 Observation/Outcome 分片，再写带内容哈希的 commit marker；只有存在且通过校验的 commit 才属于运行结果。checkpoint 原子记录连续 commit、累计行数和执行身份，进度只在 commit 后推进。

执行身份同时绑定 Campaign 的 protocol/hash、代码快照、数据快照、规则定义/语义、完整标的列表、Pipeline 配置、批大小、Case ID 和 Run ID。恢复不会创建新 Case、Run 或候选试验，也不接受参数覆盖。

首次执行：

```powershell
python scripts/run_frozen_campaign.py --campaign <campaign-dir>
```

仅当同一执行的 `execution_request.json` 为 v2、状态为 `running`/`interrupted`，且 checkpoint 和所有已提交分片均通过完整性校验时，才可显式恢复：

```powershell
python scripts/run_frozen_campaign.py --campaign <same-campaign-dir> --resume
```

恢复时默认沿用登记请求中的批大小；若显式传入不同的 `--batch-size`，身份校验会拒绝执行。

崩溃发生在 commit 之前时，恢复最多重算该未提交批次；发生在 commit 之后、checkpoint 之前时，恢复会从 commit 重建累计状态而不重复该批。`progress.json` 在异常退出时标记 `interrupted`，只有报告、兼容视图和 artifact manifest 全部完成后才标记 `completed`。

旧版中断运行只有 `progress.json`、没有强绑定 checkpoint，无法证明已完成边界，必须从冻结来源派生新的 Campaign；禁止直接重试或手工伪造 checkpoint。已完成的旧版单体 `observations.jsonl` / `outcomes.jsonl` 仍可由报告与汇总读取。
