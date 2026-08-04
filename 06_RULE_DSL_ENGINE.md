# 规则 DSL、规则引擎、解释与版本管理

## 1. 设计目标

DSL 用于表达受限、可审计、确定性的技术形态规则。它不是通用编程语言，不允许任意代码、网络、文件、动态 SQL 或模型调用。

同一规则 revision 必须用于：

- OHLCV 候选筛选；
- K 线截图候选验证；
- 规则详情逐项解释；
- 历史回测信号生成。

## 2. 规则分层

一条规则包含四个层次：

1. `source_semantics`：书中如何描述，带证据。
2. `operationalization`：将主观词转为工程阈值的理由。
3. `dsl`：机器可执行定义。
4. `execution_policy`：何时观察完成、何时允许成交。

不得把工程阈值写成作者原话。示例：“长下影”可实现为 `lower_shadow >= 2 * body`，但必须标记为默认近似且允许参数化。

## 3. RuleDefinition

```yaml
id: hammer
version: 1.2.0
name_zh: 锤子线（工程近似）
status: draft|verified|deprecated
timeframe_agnostic: true
evidence_citations: [citation_uuid]
parameters:
  min_lower_shadow_body:
    type: number
    default: 2.0
    min: 1.0
    max: 5.0
  max_upper_shadow_range:
    type: number
    default: 0.10
    min: 0
    max: 0.5
warmup_bars: 10
observed_at: bar_close
executable_from: next_bar_open
expression: {}
explanation_template: {}
test_vector_set: hammer_v1
```

语义版本：

- patch：文案/元数据修正，不改变信号；
- minor：新增向后兼容参数或解释；
- major：默认值、谓词、时间或信号语义变化。

任何改变信号集合的变更都创建新 revision，旧回测保持可重放。

## 4. DSL v1 能力

### 4.1 数据引用

- `bar(offset).open/high/low/close/volume`
- `feature(name, params, offset)`
- `context(window, predicate)`
- `quality_flag(name)`

offset 必须 `<= 0`；正 offset 在编译期拒绝。

### 4.2 运算

- 算术：`add/sub/mul/div/abs/min/max`
- 比较：`gt/gte/lt/lte/eq/between`
- 逻辑：`all/any/not`
- 序列：`rising/falling/count/exists/for_all`
- 安全比例：`safe_div`，显式 zero policy
- 参数引用：`param`

### 4.3 示例

```yaml
expression:
  all:
    - gte:
        - safe_div: [lower_shadow(0), max(body(0), tick_size)]
        - param: min_lower_shadow_body
    - lte:
        - safe_div: [upper_shadow(0), max(range(0), tick_size)]
        - param: max_upper_shadow_range
    - lte:
        - safe_div: [body(0), max(range(0), tick_size)]
        - param: max_body_range
    - context:
        name: prior_downtrend
        args: {window: 5, method: lower_close_count, min_count: 3}
```

实际实现采用 JSON AST，不解析任意表达式字符串。上例只展示可读形式。

## 5. 静态校验与编译

编译阶段：

1. JSON Schema 校验。
2. 类型检查、参数范围、未知特征检查。
3. offset 和窗口检查，拒绝未来引用。
4. 计算最大 lookback/warm-up。
5. 检查除零、NaN 和缺失处理是否显式。
6. 规范化 AST，计算 `semantic_hash`。
7. 编译为受控中间表示 IR。
8. 生成解释计划和测试向量绑定。

禁止 `eval/exec`。执行器仅遍历白名单节点或执行生成的安全向量表达式。

## 6. 规则引擎接口

```python
evaluate(
    series: CandleSeries,
    as_of_index: int,
    rule_revision: RuleRevision,
    parameters: dict
) -> RuleEvaluation
```

输出：

```yaml
matched: true
status: matched|not_matched|ambiguous|insufficient_data|data_error
observed_at: 2026-01-05T07:00:00Z
executable_from: 2026-01-06T01:30:00Z
conditions:
  - path: expression.all[0]
    label: 下影线至少为实体 2 倍
    actual: 2.34
    operator: gte
    threshold: 2.0
    passed: true
    confidence_interval: [2.20, 2.48]
warnings: []
semantic_hash: sha256:...
```

视觉输入的实际值可以带置信区间；如果区间跨越阈值，条件为 `ambiguous`。

## 7. 上下文与确认

形态、上下文、确认信号分开建规则：

- `shape_rule`：当前一根或数根 K 线几何；
- `context_rule`：此前趋势、位置、波动；
- `confirmation_rule`：后续 bar 的确认，仅在后续时点才可成立；
- `entry_rule`：确定回测成交时刻。

UI 可以展示“形态已出现，尚未确认”。扫描某日不能使用下一日确认来标记当日已知信号。

## 8. 参数与研究治理

- 系统默认参数由 Reviewer 批准并有证据/工程说明。
- 用户自定义参数生成 `RuleInstantiation`，不改变定义。
- 扫描/回测 manifest 固定实例参数。
- 参数搜索在独立训练区间进行；搜索空间、次数和目标函数入日志。
- 不允许根据测试集结果回写默认参数而不创建新版本和新评测。

## 9. 黄金测试

每个 verified 规则必须有：

- 最小正例、边界正例；
- 每个谓词单独失败的反例；
- doji/零实体、零 range、缺失、复权跳变；
- 上下文不足；
- 与相似形态的区分例；
- 时间确认例；
- 视觉误差区间导致 ambiguous 的例子。

测试向量是人可读 OHLC 数列，同时保存预期条件树。

## 10. DoD

- JSON Schema、静态检查、规范化和 semantic hash 稳定。
- 任意正 offset、未来 rolling 窗口或非法函数被编译期拒绝。
- 8–12 个 MVP 规则通过黄金测试。
- 扫描、API 解释、视觉和回测对相同输入返回同一 evaluation。
- 规则变更影响分析能列出受影响扫描、回测和 Wiki 引用。

