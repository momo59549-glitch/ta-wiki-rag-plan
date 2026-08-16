# Microsoft Qlib 最小 Alpha158 spike

环境于 2026-08-13 只安装并验证官方 PyPI `pyqlib 0.9.7`（CPython 3.12 Windows wheel）与
`lightgbm 4.7.0`。Qlib 的 `DatasetH`、`Alpha158`、`LGBModel` 与 `R` workflow 已用合成小样本
实际训练和预测；MLflow 3.15 在 Windows 必须使用本地 SQLite URI，而非默认 file-store。CatBoost/XGBoost
是可选模块，缺失不会影响此 LGBModel spike。

真实试验拟严格固定为已有冻结主板 ordered 前 200，只读 2015-01-05..2021-12-31，窗口固定为 train
2015-2017、valid 2018、test 2019-2021。转换层仅接受显式 `open/high/low/close/volume`，并写
Qlib 官方本地 provider 的 calendar/instrument/feature binary 格式及 conversion identity。没有 PIT，且
样本有幸存者偏差；绝不进入候选、锁箱或 42 budget。

标准 Alpha158 的默认 price config 包含 `$vwap`，而冻结本地主板内容仅有 OHLCV，既没有成交额也没有真实
VWAP。官方 `Alpha158DL.get_feature_config(config)` 支持显式配置，故本 spike 使用
`kbar + OPEN/HIGH/LOW(0) + VOLUME(0)`，不含 VWAP；其 feature expressions、names、count 与 hash 会冻结
在结果中。这个名称只能是 **Alpha158 reduced OHLCV subset**，绝不称作标准 Alpha158；没有以 HLC3、close
或任何近似替代 VWAP/amount。

真实固定 200 仅使用官方 Qlib provider、`DatasetH`、这个 Alpha158 reduced handler、`LGBModel`、`R`
workflow 和 `qlib.contrib.eva.alpha.calc_ic` 的 prediction IC/RankIC。官方 TopkDropoutStrategy/backtest
不在本 spike 授权范围内；若后续需要其执行域或金额/VWAP 字段，必须单独审查，不能由这里伪造。

固定 200 已执行一次：provider identity 为
`sha256:768c6dfaf7d5c0c593480f1bcdf16b0501082f455fbc6559e19c46e8d6ff4603`，reduced-config identity 为
`sha256:a86e092a8f2dfe8b0f9f64dc37b6a16dd577bc68c1714c0ecaccf9238252b0f2`，结果 identity 为
`sha256:8c975f44c12663c1ac53e7dd5569db37179ae53ff99eb5242ca9e38b8d0d8e33`。固定窗口产生 146,000 条
test prediction、728 个 IC/RankIC 日期；官方 Qlib `calc_ic` 的均值为 IC 0.0551076、RankIC
0.0503407。它们仅是 non-PIT、survivor-biased 的预测诊断，不是候选、收益、回测、锁箱结果或 42
trial budget 使用。没有运行 Topk 或任何组合回测。

同一固定假设的确定性诊断已独立复现并保存官方 `SignalRecord` 的 `pred.pkl`/`label.pkl` 与
`SigAnaRecord` 的 `ic.pkl`/`ric.pkl`。诊断 result identity 为
`sha256:44932484274b19b562e5135e22da3f341b4d625317ba81d70fc0959b4c5b7692`；prediction identity 为
`sha256:6afaa3f1d43d35b51350488596cbb10a794e3f6c2bfd462e6315e1ac14c9f375`，label identity 为
`sha256:5844fb1bc40cee56d40f3cafa5174c2173399bcf5df4ceff6e729545a2430f7f`。标签末端无法形成前瞻收益的
NaN 在 identity 中明确以 sentinel 编码，Infinity 仍拒绝。

728 个日 IC 的 mean/std/IR 为 0.0551076/0.1506280/0.3658522，正日比例 0.652473；RankIC 为
0.0503407/0.1083866/0.4644551，正日比例 0.670330。IC 年均值（count）依次为 2019
0.0632212 (244)、2020 0.0505428 (243)、2021 0.0514956 (241)；RankIC 为 2019 0.0471387、2020
0.0534335、2021 0.0504641（同样 count）。这只是对同一次固定假设的稳定性诊断，不增加样本、参数、
候选或预算，也不构成收益或执行结论。
