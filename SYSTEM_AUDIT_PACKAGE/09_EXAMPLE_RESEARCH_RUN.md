# 09 · 真实研究运行示例：Gen1 ROC 候选

选择：`roc`（自动发现 generation `g_20260809_01` 的 momentum oscillator 候选）。此示例用于说明真实文件与结论；它**不是**可交易策略。

## 1. 产生与定义

- 生成器：`packages/research/auto_discovery.py` 的 `_grammar_candidates()`；ROC 规则族由 `roc_positive/roc_negative` 离散窗口与阈值构成。
- Generation/试验治理：`DiscoveryConfig`、`build_auto_discovery_protocol()`、registry/trial ledger。其 grammar 在读取 outcome 前固定，候选预算受限。
- 该代的派生候选之一为 `roc`，semantic hash：`sha256:3788dcf0ac8d10c07d3ddafbdeeb21980efe86ccba456c7738cba0156a2c797c`（见 `data/candidate_comparisons/g_20260809_01/comparison_protocol.json`）。

## 2. 冻结比较协议

文件：`data/candidate_comparisons/g_20260809_01/comparison_protocol.json`。

- 同批候选：`breakdown`、`roc`、`rsi`；比较不是只挑 ROC 后再测。
- OOS：2022-01-01 至 2026-08-04；最终 lockbox 从 2026-09-01 起，协议标记 `final_lockbox_must_remain_unread=true`。
- 入场/退出：T 收盘信号，T+1 open，fixed-horizon close。
- 组合：逐日 `daily_cash_positions_equity` ledger，最多 20 个等权 slot；同日选股以冻结 hash 排序；禁止同 symbol 重叠。
- 可交易性：entry 必须 open 可交易、exit 必须 close 可交易；exit 最多延迟 5 bars。
- 成本：单边 commission 3 bps + slippage 5 bps，另跑 2x/3x stress。

代码路径：`packages/research/comparison_panel.py: build_comparison_panel()` 生成并 hash 校验 symbol shard；`packages/research/candidate_comparison.py` 对事件 HAC/FDR 和组合 ledger 汇总。

## 3. 结果

文件：`data/candidate_comparisons/g_20260809_01/comparison_result.json`。

- `comparison_id`: `comparison_ce50fd168bbbda30ed8b571c`
- `comparison_hash`: `sha256:ce50fd168bbbda30ed8b571c4f12d3d3fd473c304fbaf82849c036f327f6c6a4`
- `final_lockbox_read`: `false`
- `approval`: `forbidden`
- ROC 事件数：170,682；与其他规则存在重叠，但 unique-event fraction 约 89.29%。
- ROC 的组合 5/10/20 日 HAC 家族调整 p 值均约 0.936–0.937 或更高，`fdr_reject=false`；组合正收益 horizon 数为 0。
- ranking 中 ROC：`status=research_eliminated_event`，`passes_primary_gate=false`，`passes_portfolio_gate=false`，`publication=forbidden`。

## 4. 最终结论

ROC 在三候选内部相对排名第一，但没有通过 primary、portfolio、FDR/CI 和成本压力的组合门槛；因此被正式淘汰，不能进入 Rule approval 或发布。

同一结论也记录在 `docs/RESEARCH_CONCLUSIONS.md`：RSI、ROC、breakdown 三候选全部为 `research_eliminated_event`，而不是“ROC 有效”。最终未来 lockbox 尚未读取，因此本例也不能被包装为最终 OOS 成功/失败裁决。

## 5. 复现/关联文件

```text
packages/research/auto_discovery.py             # 有界 grammar、去重、trial ledger
packages/research/rule_search.py                # FDR、压力、regime、Jaccard screening
packages/research/comparison_panel.py            # 强 snapshot 绑定的 OOS panel
packages/research/candidate_comparison.py        # 独立组合 ledger、HAC/FDR/ranking
data/auto_discovery/g_20260809_01/               # 代次协议和候选记录
data/candidate_comparisons/g_20260809_01/
  comparison_protocol.json
  comparison_result.json
  ... panel/staging/ledger artifacts ...
docs/RESEARCH_CONCLUSIONS.md
```
