# Model 历史实验导入

`packages.integrations.ModelExperimentAdapter` 以只读方式导入 `H:\股票模型\Model\data\experiments` 下的 JSON。它不执行旧策略代码，也不复制行情数据。

```python
from packages.integrations import ModelExperimentAdapter

adapter = ModelExperimentAdapter(r"H:\股票模型\Model\data\experiments")
legacy = adapter.import_file("baseline.json")
manifest = legacy.manifest()
```

把 `manifest` 写入新的 Research Case，即可保留源文件相对路径、SHA-256、导入时间、识别出的配置与指标。原始 JSON 保留在 `raw_payload`，仅供审阅；它不会自动成为 Rule Version 或可发布知识。
