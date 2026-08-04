# OHLCV 行情数据层、K 线截图识别与候选筛选

## 1. 统一 CandleSeries

行情与视觉都适配为统一接口，但必须标记来源与精度：

```yaml
candle_series:
  source_type: market_data|chart_image
  instrument_id: uuid|null
  timeframe: 1d|1h|...|unknown
  timezone: Asia/Shanghai|null
  price_scale: absolute|relative_pixel
  candles:
    - ts: datetime|null
      open: number
      high: number
      low: number
      close: number
      volume: number|null
      confidence: 1.0
  provenance:
    dataset_snapshot_id: uuid|null
    chart_parse_id: uuid|null
```

视觉数据若无坐标，只能使用归一化相对值，不能冒充真实 OHLC。

## 2. 行情标准模型

### 2.1 Instrument

保存稳定内部 ID；代码是有有效期的标识：

- asset_class、exchange、currency、timezone；
- symbol history、ISIN/FIGI（如合法可用）；
- listing/delisting、tick size、lot size；
- 交易状态与数据许可标签。

### 2.2 Bar

- `event_time`：bar 所代表区间或收盘时刻；
- `available_at`：该记录对策略可用的最早时刻；
- `open/high/low/close/volume`；
- `adjustment_type`：raw/split_adjusted/total_return；
- `vendor_revision`, `ingested_at`, `quality_flags`。

主键语义为 `(instrument_id, timeframe, event_time, vendor_revision)`；快照选择确定 revision。

### 2.3 日历与公司行动

- 每个交易所独立日历、半日市、临时休市和夏令时。
- 公司行动包含公告时间、生效时间、可用时间。
- 原始价、拆股复权、总回报序列分开，报告清楚说明。
- 成分股与行业分类必须支持点时查询，避免幸存者偏差。

## 3. 数据接入流程

1. Connector 拉取到 raw landing zone，不覆盖。
2. 校验字段、时区、单位、重复、OHLC 约束和交易日历。
3. 标准化为 canonical Parquet，按市场/周期/日期分区。
4. 生成 DatasetSnapshot manifest：文件哈希、范围、供应商、许可、修订。
5. 质量报告与异常隔离。
6. 特征层只读取明确 snapshot。

质量规则：

- `low <= min(open, close) <= max(open, close) <= high`；
- volume 非负；
- 时间戳唯一且严格递增；
- 缺失 bar 与非交易日分开；
- 极端跳变与公司行动交叉检查；
- 不自动用前值填充 OHLC。

## 4. 特征计算

基础蜡烛特征只使用当下及过去数据：

```text
body = abs(close - open)
range = high - low
upper_shadow = high - max(open, close)
lower_shadow = min(open, close) - low
body_ratio = body / max(range, epsilon)
lower_shadow_to_body = lower_shadow / max(body, tick_size)
```

所有特征有：

- 明确窗口对齐方式；
- warm-up 数量；
- 缺失策略；
- 代码版本与参数哈希；
- `computed_at` 和输入 snapshot。

## 5. 行情候选扫描

扫描不是完整回测。输入为 universe、日期/区间、周期、规则 revision 和参数。输出每个 `SignalEvent`：

- instrument、signal_time、data_available_at；
- rule revision、每个 predicate 的实际值/阈值/通过状态；
- 上下文窗口摘要；
- 数据质量警告；
- 可打开图表与规则证据的链接。

扫描和回测必须调用同一 `rule_engine.evaluate(series, as_of, rule_revision)`。

## 6. 截图识别 MVP 边界

受支持的输入：

- 单一 K 线主图；
- 线性价格轴；
- 蜡烛无遮挡或少量网格；
- 颜色主题在已知集合或可由用户确认；
- 至少 20 根可见蜡烛；
- 不要求从截图识别证券身份。

不支持或降级：

- 对数轴、Heikin-Ashi、Renko 等非标准 K 线；
- 大量指标遮挡、透视拍照、压缩严重；
- 颜色含义不明、像素过低；
- 无法确定蜡烛宽度或图图区；
- 需要精确成交量但无量柱。

## 7. 视觉流水线

1. **安全与元数据**：去 EXIF，限制尺寸，恶意文件检查。
2. **质量分类**：截图类型、分辨率、遮挡、轴类型、主题置信度。
3. **图图区检测**：识别主价格 pane，排除标题、工具栏、成交量 pane。
4. **坐标/网格解析**：OCR 价格和时间标签；拟合像素到数值映射。
5. **颜色/几何分割**：检测实体、上下影线、蜡烛中心和宽度。
6. **序列重建**：按 x 排序，输出视觉 OHLC 与每根置信度。
7. **候选生成**：宽松阈值找形态候选。
8. **统一规则验证**：转换为 CandleSeries，执行 DSL。
9. **人工校准**：用户修正图图区、颜色含义、周期和目标蜡烛。
10. **解释输出**：显示叠加框、条件、置信度和不可推断项。

### 7.1 坐标映射

线性轴至少需两个可靠 tick：

`price(y) = a*y + b`

用 RANSAC 拟合多个 tick 并报告残差。若残差超过阈值或疑似对数轴：

- 不输出绝对价格；
- 切换 `relative_pixel`；
- 规则仅使用对仿射变换不敏感的比例；
- 显示明确警告。

## 8. 视觉真值集

分三层：

1. **合成标准图**：由已知 OHLCV 渲染，多主题、分辨率、线宽。
2. **受控扰动图**：压缩、缩放、网格、标注、轻微裁剪。
3. **真实授权截图**：不同平台和设备，人工标注 pane、蜡烛、轴、形态。

划分按模板/标的/时间分组，防止同一底图变体泄露到训练和测试。

指标：

- pane IoU；
- 蜡烛检测 precision/recall；
- OHLC 像素 MAE 或价格相对误差；
- 规则候选 precision/recall；
- 置信度校准 ECE；
- 不支持输入的正确拒绝率。

## 9. 候选与结论区分

视觉模型只做：

- 图图区/蜡烛检测；
- 几何重建；
- 宽松候选和置信度。

最终“匹配某规则版本”的判断由统一规则引擎给出，并显示：

- 视觉测量误差范围；
- 条件在误差范围内是否稳定；
- 若阈值附近不稳定，输出 `ambiguous` 而非硬判定。

## 10. DoD

- OHLCV 快照可追溯到原始供应商文件和许可标签。
- 时间、复权、缺失、重复和公司行动测试通过。
- 标准图像上视觉重建达到 MVP 指标。
- 不支持/低置信度截图不会生成精确价格或高确定性结论。
- 同一标准图的原始 OHLC 与视觉重建分别输入规则引擎时，形态一致率达到门禁。

