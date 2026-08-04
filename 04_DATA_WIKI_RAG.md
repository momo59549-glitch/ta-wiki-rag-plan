# 核心数据模型、Wiki Schema 与 RAG 混合检索

> v2 定位：本文件是证据/知识子域设计。研究主域及 Observation→Outcome→Hypothesis→Backtest→Rule Version→Knowledge Update 模型见 `MASTER_IMPLEMENTATION_PLAN.md`。

## 1. 数据模型总览

核心关系：

```text
Tenant ─ Entitlement ─ SourceEdition ─ SourcePage ─ PageRegion ─ EvidenceSpan
                                      │
                                      └─ Section
EvidenceSpan ─ Citation ─ WikiRevision ─ WikiEntry ─ WikiRelation
EvidenceSpan ─ Citation ─ RuleRevision ─ RuleDefinition
Question ─ RetrievalRun ─ RetrievedEvidence ─ Answer ─ Claim ─ ClaimCitation
```

每个表含 `id`, `created_at`, `created_by`, `tenant_id`（适用时）和审计字段。可编辑对象使用 revision 表，不覆盖历史。

## 2. Wiki Schema

### 2.1 条目类型

- `concept`：术语与理论。
- `pattern`：单根/多根 K 线或图表形态。
- `context`：趋势、位置、确认条件。
- `indicator`：指标定义与参数。
- `method`：分析步骤或交易管理方法。
- `warning`：限制、失败场景、常见误解。
- `person/book/edition`：来源实体。

### 2.2 WikiRevision

```yaml
wiki_revision:
  entry_id: uuid
  revision: 4
  slug: hammer
  title_zh: 锤子线
  aliases: [锤头线]
  entry_type: pattern
  summary: "..."
  formal_definition: "..."
  prerequisites:
    - "此前存在可操作化的下跌上下文"
  components:
    - name: lower_shadow
      description: "..."
  confirmations: ["次日确认（来源相关）"]
  invalidations: ["..."]
  common_confusions: [hanging_man]
  source_scoped_notes:
    - source_edition_id: uuid
      note: "该译本的表述..."
  computable_mapping:
    rule_revision_ids: [uuid]
    approximation_note: "实体/影线阈值为工程参数，并非原文精确数值"
  review:
    status: draft|in_review|verified|deprecated
    reviewer_ids: [uuid]
```

### 2.3 关系类型

`is_a`, `opposite_of`, `similar_to`, `requires_context`, `confirmed_by`, `invalidated_by`, `derived_from`, `contradicts`, `same_concept_different_edition`, `implemented_by_rule`。

关系也要有证据和 revision。禁止仅凭 embedding 相似度自动写入 verified 关系。

## 3. Wiki 生成与审校

1. 从章节/证据片段生成候选术语。
2. 聚类别名，但保留来源作用域。
3. 生成结构化草稿和 claim 列表。
4. 为每个字段绑定一个或多个 evidence span。
5. 运行引用支撑检查与重复检测。
6. 编辑修订，Reviewer 批准。
7. 发布 revision，触发检索索引和关系图更新。

自动生成内容永远是草稿。规则映射必须由兼具知识和量化能力的 Reviewer 批准。

## 4. 检索索引

### 4.1 索引单元

- Evidence chunk：原文、标题路径、页码、内容类型、权限。
- Wiki revision：结构化字段、别名、关系、已验证状态。
- Rule revision：人类描述、DSL、参数、证据引用。

不同类型分索引或通过 `document_type` 区分；不要把 AI 总结伪装为原文。

### 4.2 中文规范化

- 保存原文，不做不可逆替换。
- 检索字段可做繁简转换、全半角、Unicode、标点和空格规范化。
- 自定义技术术语词典、别名词典和英文缩写。
- 使用字符 n-gram/适合中文的分析器作为 BM25 兜底。
- 查询扩展必须记录原查询与扩展词，便于解释和评测。

## 5. 混合检索流程

1. **查询理解**：识别术语、形态、来源、版次、时间/市场意图。
2. **权限过滤**：在召回前应用 tenant、entitlement、visibility。
3. **并行召回**：
   - BM25 top 50；
   - dense vector top 50；
   - 精确别名/标题/关系图候选；
   - 可选最近邻问题。
4. **融合**：RRF 为默认；分数归一化仅在校准后使用。
5. **去重与多样化**：同页相邻 chunk 合并；保留不同来源观点。
6. **重排**：Cross-Encoder 或受控 LLM 对 top 30 重排到 top 8–12。
7. **上下文扩展**：按需带前后句、标题和图注，仍受 token 与版权窗口限制。
8. **证据打包**：分配稳定 citation label，携带 page/bbox/revision。

伪配置：

```yaml
retrieval:
  bm25_k: 50
  vector_k: 50
  fusion: {method: rrf, k: 60}
  rerank_k: 30
  final_k: 10
  filters_mandatory: [tenant_id, entitlement_id, review_status]
  diversity:
    max_chunks_per_page: 2
    preserve_source_disagreement: true
```

## 6. RAG 回答协议

回答器输入为结构化 `EvidenceBundle`，输出：

```json
{
  "answerability": "answerable|partial|insufficient",
  "claims": [
    {
      "text": "锤子线通常要求出现在下跌背景中。",
      "citation_ids": ["cit_01"],
      "epistemic_status": "source_statement|editorial_summary|inference"
    }
  ],
  "caveats": [],
  "conflicts": []
}
```

之后由验证器检查：

- 每个外部事实 claim 至少一个引用；
- 引用文本是否蕴含 claim，而非只词面相关；
- 引用是否在用户权限和许可窗口内；
- 页码、revision、bbox 是否存在；
- 不同来源冲突是否被扁平化；
- 输出是否包含不当投资建议或收益承诺。

验证失败时重写一次；再次失败则删除该 claim 或返回证据不足。

## 7. 引用展示

引用格式至少包含：

`[书名，版次/译本，书内第 15 页（文件第 28 页），证据 revision 3]`

点击后打开受权限控制的页面查看器，默认只显示必要的短上下文和高亮区域。公开/低权限用户不应通过连续翻页重建全文。

## 8. 缓存与可重复性

- RetrievalRun 保存 query、过滤、索引版本、embedding 版本、融合和重排参数。
- Answer 保存模型、提示模板哈希、EvidenceBundle 哈希和安全策略版本。
- 缓存键包含权限作用域；禁止跨租户或跨授权共享回答缓存。
- 来源撤销、Wiki revision 或索引切换后，相关缓存失效。

## 9. RAG 攻击面

书籍内容和用户查询均视为不可信数据：

- 不执行来源中的指令，不允许检索文本改变系统策略；
- 工具调用由固定编排器决定，模型无数据库任意查询权限；
- 对提示注入、数据外泄、跨租户引用建立专门测试集；
- 输出引用 URL 由服务端生成，不接受模型提供的对象 URL。

## 10. DoD

- 同一查询在固定版本下可重放到相同候选集合（允许明确记录的模型非确定性）。
- 检索评测按术语、比较、限定条件、跨来源冲突、不可回答分类报告。
- 回答中的每个重要 claim 可点击并定位到证据区域。
- 受限来源不能从检索、缓存、日志或错误信息泄露。
- 来源删除后完成数据库状态、索引、缓存和对象派生物的验证清单。
