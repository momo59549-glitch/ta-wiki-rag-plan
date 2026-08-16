"""One fixed, approximate Microsoft Qlib Topk execution spike.

This is deliberately a narrow integration run, not a portfolio framework.  It
reuses the already-frozen 200-symbol Alpha158-reduced prediction, gives Qlib's
own TopkDropoutStrategy the trade decision, and writes only a gitignored,
write-once artifact.  The local gates are observed-row approximations, not
authoritative Chinese tradability evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from scripts.run_qlib_spike import _series_hash
    from scripts.run_vectorbt_candidate_screen import (
        CONFIRM,
        ROOT,
        contract,
        fixed_symbols,
        frames,
        state,
    )
except ModuleNotFoundError:
    from run_qlib_spike import _series_hash
    from run_vectorbt_candidate_screen import CONFIRM, ROOT, contract, fixed_symbols, frames, state


TEST = ("2019-01-01", "2021-12-31")
# pyqlib 0.9.7 needs one following provider-calendar entry to close a daily
# step.  The frozen provider stops at 2021-12-31 and 2022 data is forbidden,
# so the last executable step is 2021-12-30; 2021-12-31 is boundary-only.
EXECUTE_END = "2021-12-30"
TOPK = 30
DROP = 3
ACCOUNT = 1_000_000.0
TRADE_UNIT = 100
BASE_COST = {"open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5.0, "impact_cost": 0.0}
STRESS_COST = {"open_cost": 0.001, "close_cost": 0.003, "min_cost": 10.0, "impact_cost": 0.0}
# The whole ordered implementation-trial set. It includes the v4 control and
# is intentionally not generated from results or caller parameters.
IMPLEMENTATION_TRIALS = (
    ("A_control_topk30_drop3", 30, 3),
    ("B_topk30_drop1", 30, 1),
    ("C_topk50_drop1", 50, 1),
    ("D_topk50_drop3", 50, 3),
    ("E_topk100_drop3", 100, 3),
)
# v1 was a failed, preserved run: Qlib treats benchmark=None as its CSI300
# default.  v2 uses its documented Series benchmark form only as a neutral
# reporting placeholder and never reports it as a market benchmark.
OUTPUT = Path("data/qlib_spikes/topk-approx-fixed200-2019-2021-v4")
V5_OUTPUT = Path("data/qlib_spikes/topk-approx-fixed200-2019-2021-v6-benchmark-diagnostic")
DISCOVERY_OUTPUT = Path("data/qlib_spikes/topk-implementation-discovery-fixed200-2019-2021-v1")
DIAGNOSTIC = Path("data/qlib_spikes/alpha158-reduced-ohlcv-fixed200-2015-2021-diagnostic-v3/diagnostic.json")
PREDICTION = Path("mlruns/1/7478fcddaf114eac8b726fbd78d45fbd/artifacts/pred.pkl")


def H(value: object) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _write_json_once(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    if path.exists() or temporary.exists():
        raise ValueError("Qlib Topk artifact already exists")
    primary = None
    try:
        with open(temporary, "xb") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    except BaseException as exc:
        primary = exc
        raise
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                if primary is None:
                    raise


def _require_symbol(symbol: str) -> None:
    if not isinstance(symbol, str) or len(symbol) != 6 or not symbol.isdigit():
        raise ValueError("Topk source symbol must be six digits")


def quote_frame(raw: dict[str, pd.DataFrame], symbol: str) -> pd.DataFrame:
    """Build only Qlib-required, observed-row fields; no filled market rows."""
    _require_symbol(symbol)
    required = {"open", "high", "low", "close", "volume", "is_st"}
    if set(raw) != required:
        raise ValueError("frozen OHLCV capability mismatch")
    frame = pd.DataFrame({field: raw[field][symbol] for field in ("open", "high", "low", "close", "volume", "is_st")}).dropna(
        subset=["open", "high", "low", "close", "volume"]
    )
    if frame.empty or not isinstance(frame.index, pd.DatetimeIndex) or not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise ValueError("Topk observed rows must have a unique increasing calendar")
    prices = frame[["open", "high", "low", "close"]].to_numpy(dtype=float)
    volume = frame["volume"].to_numpy(dtype=float)
    if not np.isfinite(prices).all() or (prices <= 0).any() or not np.isfinite(volume).all() or (volume < 0).any():
        raise ValueError("Topk observed prices must be finite positive and volume nonnegative")
    one_price = frame["open"].eq(frame["high"]) & frame["open"].eq(frame["low"]) & frame["open"].eq(frame["close"])
    blocked = frame["volume"].le(0) | frame["is_st"].astype(bool) | one_price
    out = frame[["open", "high", "low", "close", "volume"]].copy()
    # Factor=1 is explicitly a representation of the already-adjusted local
    # prices, never a corporate-action/PIT factor claim.  Change is only
    # adjacent *observed* close; its first value remains NaN rather than a
    # synthetic zero.
    out["factor"] = 1.0
    out["change"] = out["close"].pct_change(fill_method=None)
    out["limit_buy"] = blocked.astype(float)
    out["limit_sell"] = blocked.astype(float)
    return out


def _canonical_number(value: float) -> str:
    number = float(value)
    if np.isinf(number):
        raise ValueError("Topk provider cannot encode infinity")
    return "__NAN__" if np.isnan(number) else format(number, ".17g")


def write_topk_provider(provider_root: Path, symbol_frames: dict[str, pd.DataFrame]) -> str:
    """Minimal official Qlib binary provider with explicit observed-row gates."""
    fields = ("open", "high", "low", "close", "volume", "factor", "change", "limit_buy", "limit_sell")
    if provider_root.exists() or not symbol_frames or tuple(symbol_frames) != tuple(sorted(symbol_frames)):
        raise ValueError("Topk provider target or symbol ordering invalid")
    for symbol, frame in symbol_frames.items():
        _require_symbol(symbol)
        if tuple(frame.columns) != fields:
            raise ValueError("Topk provider field schema invalid")
    calendar = sorted({stamp for frame in symbol_frames.values() for stamp in frame.index})
    if not calendar:
        raise ValueError("Topk observed calendar empty")
    provider_root.mkdir(parents=True)
    (provider_root / "calendars").mkdir(); (provider_root / "instruments").mkdir(); (provider_root / "features").mkdir()
    (provider_root / "calendars" / "day.txt").write_text("\n".join(x.strftime("%Y-%m-%d") for x in calendar) + "\n", encoding="utf-8")
    calendar_index = {stamp: index for index, stamp in enumerate(calendar)}
    instruments: list[str] = []
    digest = hashlib.sha256()
    for symbol, frame in symbol_frames.items():
        local = provider_root / "features" / symbol.lower(); local.mkdir()
        instruments.append(f"{symbol}\t{frame.index[0].date()}\t{frame.index[-1].date()}")
        for stamp, row in frame.loc[:, fields].iterrows():
            digest.update((symbol + "\x1f" + stamp.isoformat() + "\x1f" + "\x1f".join(_canonical_number(row[x]) for x in fields) + "\n").encode("utf-8"))
        for field in fields:
            values = np.full(len(calendar), np.nan, dtype="<f4")
            values[[calendar_index[x] for x in frame.index]] = frame[field].to_numpy(dtype="<f4")
            np.hstack([np.array([0], dtype="<f4"), values]).astype("<f4").tofile(local / f"{field}.day.bin")
    (provider_root / "instruments" / "all.txt").write_text("\n".join(instruments) + "\n", encoding="utf-8")
    provider_hash = "sha256:" + digest.hexdigest()
    identity = {
        "schema_version": "qlib-topk-approx-provider/v1",
        "provider_hash": provider_hash,
        "fields": list(fields),
        "calendar_count": len(calendar),
        "factor_representation": "1_for_already_adjusted_local_prices_only",
        "change_representation": "adjacent_observed_close_pct_change_fill_method_none",
        "limit_representation": "observed_gate_missing_row_zero_volume_st_or_one_price; not_official_limit_or_suspension",
        "non_pit": True,
        "nonadjudicable": True,
    }
    (provider_root / "conversion_identity.json").write_text(json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    return provider_hash


def load_saved_prediction() -> tuple[pd.Series, dict]:
    if not DIAGNOSTIC.is_file() or not PREDICTION.is_file():
        raise ValueError("frozen Qlib diagnostic prediction is unavailable; retraining is forbidden for this run")
    diagnostic = json.loads(DIAGNOSTIC.read_text(encoding="utf-8"))
    saved_hash = diagnostic.pop("result_hash", None)
    if saved_hash != H(diagnostic) or diagnostic.get("status") != "completed_nonadjudicable_diagnostic":
        raise ValueError("frozen Qlib diagnostic identity invalid")
    prediction = pd.read_pickle(PREDICTION)
    if isinstance(prediction, pd.DataFrame):
        if list(prediction.columns) != ["score"]:
            raise ValueError("frozen Qlib prediction schema invalid")
        prediction = prediction.iloc[:, 0]
    if not isinstance(prediction, pd.Series) or _series_hash(prediction) != diagnostic.get("prediction_hash"):
        raise ValueError("frozen Qlib prediction hash mismatch")
    prediction = prediction.loc[(prediction.index.get_level_values("datetime") >= pd.Timestamp(TEST[0])) & (prediction.index.get_level_values("datetime") <= pd.Timestamp(TEST[1]))]
    if prediction.empty:
        raise ValueError("frozen Qlib prediction has no test rows")
    return prediction, {"diagnostic_hash": saved_hash, "prediction_hash": diagnostic["prediction_hash"]}


def _official_metrics(portfolio_metrics: dict, indicator_metrics: dict, cost: dict) -> dict:
    from qlib.contrib.evaluate import risk_analysis

    if "1day" not in portfolio_metrics:
        raise ValueError("official Qlib daily portfolio metrics missing")
    report = portfolio_metrics["1day"][0]
    if not isinstance(report, pd.DataFrame) or not {"account", "return", "cost", "turnover"}.issubset(report.columns):
        raise ValueError("official Qlib report schema missing")
    net = report["return"] - report["cost"]
    risk = risk_analysis(net, freq="day", mode="product")
    risk_data = {str(index): float(row["risk"]) for index, row in risk.iterrows()}
    indicator = indicator_metrics.get("1day", (None,))[0]
    trades = None
    if isinstance(indicator, pd.DataFrame):
        if "count" in indicator.index:
            trades = float(indicator.loc["count"].sum())
        elif "count" in indicator.columns:
            trades = float(indicator["count"].sum())
    return {
        "official_report_api": "qlib.backtest.backtest",
        "official_risk_api": "qlib.contrib.evaluate.risk_analysis(freq='day', mode='product')",
        "annualization": "Qlib_official_risk_analysis_daily",
        "annualized_return": risk_data.get("annualized_return"),
        "max_drawdown": risk_data.get("max_drawdown"),
        "total_return_from_official_final_account": float(report["account"].iloc[-1] / ACCOUNT - 1.0),
        "mean_daily_turnover": float(report["turnover"].mean()),
        "total_turnover": float(report["total_turnover"].iloc[-1]) if "total_turnover" in report else None,
        "official_trade_count": trades,
        "report_rows": int(len(report)),
        "cost": cost,
    }


def equal_weight_close_to_close_benchmark(raw: dict[str, pd.DataFrame]) -> tuple[pd.Series, dict]:
    """Transparent daily cross-sectional equal-weight *return* series.

    Each return at T uses only a member's raw adjusted closes at T-1 and T.
    `fill_method=None` deliberately prevents a missing observation becoming a
    forward-filled market price.  This is a costless, survivor-biased diagnostic
    comparator -- neither an index nor a buy-and-hold portfolio.
    """
    close = raw["close"].copy()
    if not isinstance(close.index, pd.DatetimeIndex) or not close.index.is_unique or not close.index.is_monotonic_increasing:
        raise ValueError("benchmark raw close calendar invalid")
    returns = close.pct_change(fill_method=None)
    returns = returns.loc[(returns.index >= pd.Timestamp(TEST[0])) & (returns.index <= pd.Timestamp(EXECUTE_END))]
    series = returns.mean(axis=1, skipna=True).dropna()
    if series.empty or not np.isfinite(series.to_numpy(dtype=float)).all():
        raise ValueError("benchmark has no finite close-to-close returns")
    identity = {
        "formula": "at_T mean_i(close_i_T / close_i_T_minus_1 - 1), only_i_with_both_raw_observed_closes; pandas_pct_change_fill_method_none",
        "window": [TEST[0], EXECUTE_END],
        "members": list(close.columns),
        "daily_return_hash": _daily_series_hash(series),
        "days": int(len(series)),
        "costs": 0.0,
        "survivor_bias": True,
        "not_index": True,
        "not_buy_and_hold": True,
    }
    identity["benchmark_hash"] = H(identity)
    return series, identity


def _daily_series_hash(series: pd.Series) -> str:
    if not isinstance(series, pd.Series) or not isinstance(series.index, pd.DatetimeIndex) or not series.index.is_unique or not series.index.is_monotonic_increasing:
        raise ValueError("daily series identity schema invalid")
    digest = hashlib.sha256()
    for stamp, value in series.items():
        number = float(value)
        if not np.isfinite(number):
            raise ValueError("daily series identity value invalid")
        digest.update(f"{stamp.isoformat()}\x1f{number:.17g}\n".encode("utf-8"))
    return "sha256:" + digest.hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _write_pickle_once(path: Path, value: object) -> str:
    temporary = path.with_name(path.name + ".tmp")
    if path.exists() or temporary.exists():
        raise ValueError("official Qlib record artifact already exists")
    primary = None
    try:
        with open(temporary, "xb") as handle:
            pickle.dump(value, handle, protocol=pickle.HIGHEST_PROTOCOL)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    except BaseException as exc:
        primary = exc
        raise
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                if primary is None:
                    raise
    return _file_hash(path)


def _yearly_diagnostic(report: pd.DataFrame) -> dict:
    required = {"return", "cost", "bench"}
    if not isinstance(report, pd.DataFrame) or not required.issubset(report.columns) or not isinstance(report.index, pd.DatetimeIndex):
        raise ValueError("official Qlib annual report schema invalid")
    result = {}
    for year, rows in report.groupby(report.index.year):
        strategy_net = rows["return"] - rows["cost"]
        benchmark = rows["bench"]
        strategy_return = float((1.0 + strategy_net).prod() - 1.0)
        benchmark_return = float((1.0 + benchmark).prod() - 1.0)
        result[str(year)] = {"strategy_net_return": strategy_return, "benchmark_return": benchmark_return, "excess_return_arithmetic": strategy_return - benchmark_return, "sessions": int(len(rows))}
    if tuple(result) != ("2019", "2020", "2021"):
        raise ValueError("official Qlib annual diagnostic window invalid")
    return result


def run_v5_benchmark_diagnostic() -> dict:
    """One same-hypothesis Topk rerun with a frozen transparent comparator."""
    import qlib
    from qlib.backtest import backtest
    from qlib.config import REG_CN
    from qlib.contrib.evaluate import risk_analysis
    from qlib.contrib.strategy.signal_strategy import TopkDropoutStrategy

    output = V5_OUTPUT.resolve()
    if output.exists():
        raise ValueError("Qlib Topk benchmark diagnostic output already exists")
    prediction, prediction_identity = load_saved_prediction()
    frozen = state(contract("configs/gen3_trend_cache_quality.json"))
    symbols = fixed_symbols(frozen)
    raw, _, _ = frames(frozen, symbols)
    benchmark, benchmark_identity = equal_weight_close_to_close_benchmark(raw)
    source = {symbol: quote_frame(raw, symbol) for symbol in symbols}
    output.mkdir(parents=True)
    provider_hash = write_topk_provider(output / "provider", source)
    sqlite_uri = "sqlite:///" + (output / "mlflow.db").as_posix()
    qlib.init(provider_uri=output / "provider", region=REG_CN, exp_manager={"class": "MLflowExpManager", "module_path": "qlib.workflow.expm", "kwargs": {"uri": sqlite_uri, "default_exp_name": "qlib-topk-approx-fixed200-v5"}})
    executor = {"class": "SimulatorExecutor", "module_path": "qlib.backtest.executor", "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True}}
    exchange_common = {"codes": list(symbols), "deal_price": "$open", "trade_unit": TRADE_UNIT, "limit_threshold": ("$limit_buy", "$limit_sell"), "volume_threshold": None}
    metrics, record_hashes, annual = {}, {}, None
    for name, costs in (("base", BASE_COST), ("stress_2x", STRESS_COST)):
        strategy = TopkDropoutStrategy(topk=TOPK, n_drop=DROP, signal=prediction, only_tradable=True, forbid_all_trade_at_limit=True)
        portfolio_metrics, indicator_metrics = backtest(start_time=TEST[0], end_time=EXECUTE_END, strategy=strategy, executor=executor, benchmark=benchmark, account=ACCOUNT, exchange_kwargs={**exchange_common, **costs})
        metric = _official_metrics(portfolio_metrics, indicator_metrics, costs)
        report, positions = portfolio_metrics["1day"]
        indicator = indicator_metrics["1day"][0]
        report_hash = _write_pickle_once(output / f"official_daily_report_{name}.pkl", report)
        position_hash = _write_pickle_once(output / f"official_positions_{name}.pkl", positions)
        indicator_hash = _write_pickle_once(output / f"official_indicator_{name}.pkl", indicator)
        metric["official_record_hashes"] = {"daily_report": report_hash, "positions": position_hash, "indicator": indicator_hash}
        net = report["return"] - report["cost"]
        excess = net - report["bench"]
        excess_risk = risk_analysis(excess, freq="day", mode="product")
        risk_values = {str(index): float(row["risk"]) for index, row in excess_risk.iterrows()}
        metric["official_excess_diagnostic"] = {"annualized_return": risk_values.get("annualized_return"), "information_ratio": risk_values.get("information_ratio"), "max_drawdown": risk_values.get("max_drawdown"), "daily_excess_hash": _daily_series_hash(excess)}
        metrics[name] = metric
        record_hashes[name] = metric["official_record_hashes"]
        if name == "base":
            annual = _yearly_diagnostic(report)
    annualized, drawdown, stress_annualized = metrics["base"]["annualized_return"], metrics["base"]["max_drawdown"], metrics["stress_2x"]["annualized_return"]
    if any(value is None or not np.isfinite(value) for value in (annualized, drawdown, stress_annualized, metrics["base"]["official_excess_diagnostic"]["annualized_return"])):
        raise ValueError("official Qlib v5 diagnostic metrics invalid")
    result = {
        "schema_version": "qlib-topk-approx-benchmark-diagnostic/v1",
        "status": "completed_same_hypothesis_nonadjudicable_diagnostic",
        "source_v4_result_hash": "sha256:8af3784ae0bb577257ee518c6b9830ed586a720e0091e1545d9f0cd6a6e7955a",
        "source": "trend_cache_adjusted", "source_universe_hash": frozen.universe_hash, "symbols": list(symbols), "test_window": list(TEST), "executed_window": [TEST[0], EXECUTE_END],
        "prediction": prediction_identity, "provider_hash": provider_hash, "benchmark": benchmark_identity,
        "strategy": {"class": "qlib.contrib.strategy.signal_strategy.TopkDropoutStrategy", "topk": TOPK, "n_drop": DROP, "signal_timing": "prediction_T_close_used_by_official_Topk_shift_1_for_T_plus_1_trade"},
        "exchange": {"deal_price": "$open", "trade_unit": TRADE_UNIT, "limit_threshold": ["$limit_buy", "$limit_sell"], "only_tradable": True, "forbid_all_trade_at_limit": True},
        "metrics": metrics, "annual": annual, "official_record_hashes": record_hashes,
        "worth_data_upgrade": bool(annualized > .08 and drawdown > -.30 and stress_annualized > 0),
        "positive_excess_each_year": all(row["excess_return_arithmetic"] > 0 for row in annual.values()),
        "not_candidate": True, "approximate_tradability": True, "official_tradability_verified": False, "non_pit": True, "nonadjudicable": True, "no_trial_budget": True, "no_lockbox": True,
        "limitations": ["benchmark is daily equal-weight raw-adjusted-close close-to-close return, costless and survivor biased", "benchmark is not an official index and not buy-and-hold", "observed gates are not authoritative suspension, limit, or historical ST evidence", "no independent official benchmark or PIT content manifest"],
    }
    result["result_hash"] = H(result)
    _write_json_once(output / "result.json", result)
    return {"status": result["status"], "artifact": str(output / "result.json"), "result_hash": result["result_hash"], "worth_data_upgrade": result["worth_data_upgrade"], "positive_excess_each_year": result["positive_excess_each_year"]}


def implementation_trial_table() -> tuple[dict, ...]:
    """The exact five counted trials; no generated or caller-supplied sixth row."""
    expected = (("A_control_topk30_drop3", 30, 3), ("B_topk30_drop1", 30, 1), ("C_topk50_drop1", 50, 1), ("D_topk50_drop3", 50, 3), ("E_topk100_drop3", 100, 3))
    if IMPLEMENTATION_TRIALS != expected or len(IMPLEMENTATION_TRIALS) != 5:
        raise ValueError("implementation trial table must be exactly frozen five")
    return tuple({"trial_id": trial_id, "topk": topk, "n_drop": n_drop, "trial_hash": H({"trial_id": trial_id, "topk": topk, "n_drop": n_drop, "T_plus_1_open": True, "trade_unit": TRADE_UNIT, "base_cost": BASE_COST, "stress_cost": STRESS_COST})} for trial_id, topk, n_drop in IMPLEMENTATION_TRIALS)


def _trial_passes(metrics: dict, annual: dict) -> bool:
    return bool(
        metrics["base"]["annualized_return"] > .08
        and metrics["base"]["official_excess_diagnostic"]["annualized_return"] > .03
        and metrics["base"]["max_drawdown"] > -.30
        and metrics["stress_2x"]["annualized_return"] > 0
        and metrics["stress_2x"]["official_excess_diagnostic"]["annualized_return"] > 0
        and all(row["excess_return_arithmetic"] > 0 for row in annual.values())
    )


def run_implementation_discovery() -> dict:
    """Run precisely five counted official Qlib implementation trials once."""
    import qlib
    from qlib.backtest import backtest
    from qlib.config import REG_CN
    from qlib.contrib.evaluate import risk_analysis
    from qlib.contrib.strategy.signal_strategy import TopkDropoutStrategy

    output = DISCOVERY_OUTPUT.resolve()
    if output.exists():
        raise ValueError("implementation discovery output already exists")
    definitions = implementation_trial_table()
    prediction, prediction_identity = load_saved_prediction()
    frozen = state(contract("configs/gen3_trend_cache_quality.json"))
    symbols = fixed_symbols(frozen)
    raw, _, _ = frames(frozen, symbols)
    benchmark, benchmark_identity = equal_weight_close_to_close_benchmark(raw)
    source = {symbol: quote_frame(raw, symbol) for symbol in symbols}
    output.mkdir(parents=True)
    provider_hash = write_topk_provider(output / "provider", source)
    sqlite_uri = "sqlite:///" + (output / "mlflow.db").as_posix()
    qlib.init(provider_uri=output / "provider", region=REG_CN, exp_manager={"class": "MLflowExpManager", "module_path": "qlib.workflow.expm", "kwargs": {"uri": sqlite_uri, "default_exp_name": "qlib-topk-implementation-discovery-fixed200"}})
    executor = {"class": "SimulatorExecutor", "module_path": "qlib.backtest.executor", "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True}}
    exchange = {"codes": list(symbols), "deal_price": "$open", "trade_unit": TRADE_UNIT, "limit_threshold": ("$limit_buy", "$limit_sell"), "volume_threshold": None}
    trials = []
    for definition in definitions:
        metrics, annual = {}, None
        for cost_id, costs in (("base", BASE_COST), ("stress_2x", STRESS_COST)):
            strategy = TopkDropoutStrategy(topk=definition["topk"], n_drop=definition["n_drop"], signal=prediction, only_tradable=True, forbid_all_trade_at_limit=True)
            port, indicator = backtest(start_time=TEST[0], end_time=EXECUTE_END, strategy=strategy, executor=executor, benchmark=benchmark, account=ACCOUNT, exchange_kwargs={**exchange, **costs})
            metric = _official_metrics(port, indicator, costs)
            report, positions = port["1day"]
            indicator_frame = indicator["1day"][0]
            metric["official_record_hashes"] = {
                "daily_report": _write_pickle_once(output / f"{definition['trial_id']}_{cost_id}_daily_report.pkl", report),
                "positions": _write_pickle_once(output / f"{definition['trial_id']}_{cost_id}_positions.pkl", positions),
                "indicator": _write_pickle_once(output / f"{definition['trial_id']}_{cost_id}_indicator.pkl", indicator_frame),
            }
            net = report["return"] - report["cost"]
            excess_risk = risk_analysis(net - report["bench"], freq="day", mode="product")
            risk = {str(index): float(row["risk"]) for index, row in excess_risk.iterrows()}
            metric["official_excess_diagnostic"] = {"annualized_return": risk.get("annualized_return"), "information_ratio": risk.get("information_ratio"), "max_drawdown": risk.get("max_drawdown"), "daily_excess_hash": _daily_series_hash(net - report["bench"])}
            metrics[cost_id] = metric
            if cost_id == "base":
                annual = _yearly_diagnostic(report)
        required = (metrics["base"]["annualized_return"], metrics["base"]["max_drawdown"], metrics["stress_2x"]["annualized_return"], metrics["base"]["official_excess_diagnostic"]["annualized_return"], metrics["stress_2x"]["official_excess_diagnostic"]["annualized_return"])
        if any(value is None or not np.isfinite(value) for value in required):
            raise ValueError("implementation trial Qlib metrics invalid")
        trials.append({**definition, "metrics": metrics, "annual": annual, "passes_all_preregistered_gates": _trial_passes(metrics, annual)})
    passing = sorted((trial for trial in trials if trial["passes_all_preregistered_gates"]), key=lambda trial: (-trial["metrics"]["stress_2x"]["official_excess_diagnostic"]["annualized_return"], trial["trial_id"]))
    result = {
        "schema_version": "qlib-topk-implementation-discovery/v1", "status": "completed_five_counted_implementation_trials_nonadjudicable",
        "source_v6_result_hash": "sha256:7ff7bd0dc74978b41286e555624b3b612f0daccd8d4e2e9a8c97764635bf6fa5", "source": "trend_cache_adjusted", "source_universe_hash": frozen.universe_hash,
        "symbols": list(symbols), "test_window": list(TEST), "executed_window": [TEST[0], EXECUTE_END], "prediction": prediction_identity, "provider_hash": provider_hash, "benchmark": benchmark_identity,
        "trial_table": trials, "trial_count": len(trials), "trial_counting": "five implementation trials reviewed; if formally adopted they can consume at most five of global 42; no sixth trial is authorized by this artifact",
        "preregistered_gates": ["base_absolute_annualized_return_gt_8pct", "base_excess_annualized_return_gt_3pct", "base_max_drawdown_lt_30pct", "stress_absolute_annualized_return_gt_0", "stress_excess_annualized_return_gt_0", "base_excess_positive_each_2019_2021_year"],
        "selection": {"ranking": "passing trials descending stress excess annualized return, then trial_id", "passing_trial_ids": [trial["trial_id"] for trial in passing], "selected_trial_id": passing[0]["trial_id"] if passing else None, "no_selection_if_none_pass": not bool(passing)},
        "not_candidate": True, "approximate_tradability": True, "official_tradability_verified": False, "non_pit": True, "nonadjudicable": True, "no_lockbox": True, "no_history_oos": True,
        "limitations": ["five are implementation trials, not free hypotheses", "benchmark is costless survivor-biased daily equal-weight comparison, not an index or buy-and-hold", "observed gates are not authoritative suspension, limit, or historical ST evidence", "no independent official benchmark or PIT content manifest"],
    }
    result["result_hash"] = H(result)
    _write_json_once(output / "result.json", result)
    return {"status": result["status"], "artifact": str(output / "result.json"), "result_hash": result["result_hash"], "trial_count": 5, "passing": len(passing), "selected_trial_id": result["selection"]["selected_trial_id"]}


def run() -> dict:
    """Run exactly the predeclared 200-symbol Topk configuration once."""
    import qlib
    from qlib.backtest import backtest
    from qlib.config import REG_CN
    from qlib.contrib.strategy.signal_strategy import TopkDropoutStrategy

    output = OUTPUT.resolve()
    if output.exists():
        raise ValueError("Topk spike output already exists")
    prediction, prediction_identity = load_saved_prediction()
    frozen = state(contract("configs/gen3_trend_cache_quality.json"))
    symbols = fixed_symbols(frozen)
    raw, _, _ = frames(frozen, symbols)
    source = {symbol: quote_frame(raw, symbol) for symbol in symbols}
    output.mkdir(parents=True)
    provider_hash = write_topk_provider(output / "provider", source)
    sqlite_uri = "sqlite:///" + (output / "mlflow.db").as_posix()
    qlib.init(provider_uri=output / "provider", region=REG_CN, exp_manager={"class": "MLflowExpManager", "module_path": "qlib.workflow.expm", "kwargs": {"uri": sqlite_uri, "default_exp_name": "qlib-topk-approx-fixed200"}})
    executor = {"class": "SimulatorExecutor", "module_path": "qlib.backtest.executor", "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True}}
    exchange_common = {"codes": list(symbols), "deal_price": "$open", "trade_unit": TRADE_UNIT, "limit_threshold": ("$limit_buy", "$limit_sell"), "volume_threshold": None}
    # pyqlib 0.9.7 turns benchmark=None into its CSI300 default.  Its supported
    # Series form avoids that unavailable external instrument.  This all-zero
    # series is a reporting placeholder only: it is not emitted, never called a
    # benchmark, and no excess return is calculated from it.
    report_sessions = sorted({stamp for frame in source.values() for stamp in frame.index if pd.Timestamp(TEST[0]) <= stamp <= pd.Timestamp(EXECUTE_END)})
    neutral_reporting_series = pd.Series(0.0, index=pd.DatetimeIndex(report_sessions))
    metrics = {}
    for name, costs in (("base", BASE_COST), ("stress_2x", STRESS_COST)):
        strategy = TopkDropoutStrategy(topk=TOPK, n_drop=DROP, signal=prediction, only_tradable=True, forbid_all_trade_at_limit=True)
        portfolio_metrics, indicator_metrics = backtest(
            start_time=TEST[0], end_time=EXECUTE_END, strategy=strategy, executor=executor, benchmark=neutral_reporting_series, account=ACCOUNT,
            exchange_kwargs={**exchange_common, **costs},
        )
        metrics[name] = _official_metrics(portfolio_metrics, indicator_metrics, costs)
    annualized = metrics["base"]["annualized_return"]
    drawdown = metrics["base"]["max_drawdown"]
    stress_annualized = metrics["stress_2x"]["annualized_return"]
    if any(value is None or not np.isfinite(value) for value in (annualized, drawdown, stress_annualized)):
        raise ValueError("official Qlib risk metrics invalid")
    result = {
        "schema_version": "qlib-topk-approx/v1",
        "status": "completed_approximate_nonadjudicable_topk",
        "designation": "one fixed official Qlib TopkDropoutStrategy integration spike",
        "source": "trend_cache_adjusted",
        "source_universe_hash": frozen.universe_hash,
        "symbols": list(symbols),
        "test_window": list(TEST),
        "executed_window": [TEST[0], EXECUTE_END],
        "execution_boundary": "Qlib 0.9.7 needs a following provider calendar entry; no 2022 source is read, and 2021-12-31 is boundary-only",
        "prediction": prediction_identity,
        "provider_hash": provider_hash,
        "strategy": {"class": "qlib.contrib.strategy.signal_strategy.TopkDropoutStrategy", "topk": TOPK, "n_drop": DROP, "signal_timing": "prediction_T_close_used_by_official_Topk_shift_1_for_T_plus_1_trade"},
        "exchange": {"deal_price": "$open", "trade_unit": TRADE_UNIT, "limit_threshold": ["$limit_buy", "$limit_sell"], "only_tradable": True, "forbid_all_trade_at_limit": True},
        "metrics": metrics,
        "benchmark": "no_independent_benchmark; Qlib neutral zero reporting Series used solely to suppress unavailable CSI300 default; absolute metrics only and no excess",
        "worth_data_upgrade": bool(annualized > .08 and drawdown > -.30 and stress_annualized > 0),
        "not_candidate": True,
        "approximate_tradability": True,
        "official_tradability_verified": False,
        "non_pit": True,
        "nonadjudicable": True,
        "no_trial_budget": True,
        "no_lockbox": True,
        "limitations": [
            "observed gates block missing rows, zero volume, local is_st true, and one-price rows only",
            "no official trading calendar, suspension, daily price-limit, historical ST coverage, factor/corporate-action receipt, or independent benchmark",
            "factor=1 represents already-adjusted local prices only; it is not a formal factor source",
            "no excess return is reported",
        ],
    }
    result["result_hash"] = H(result)
    _write_json_once(output / "result.json", result)
    return {"status": result["status"], "artifact": str(output / "result.json"), "result_hash": result["result_hash"], "worth_data_upgrade": result["worth_data_upgrade"], "symbols": len(symbols)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-read-source", action="store_true")
    parser.add_argument("--benchmark-diagnostic", action="store_true")
    parser.add_argument("--implementation-discovery", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not args.confirm_read_source:
            raise ValueError("Topk spike requires --confirm-read-source")
        if args.benchmark_diagnostic and args.implementation_discovery:
            raise ValueError("choose exactly one Qlib diagnostic mode")
        result = run_implementation_discovery() if args.implementation_discovery else (run_v5_benchmark_diagnostic() if args.benchmark_diagnostic else run())
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (ValueError, OSError, TypeError, IndexError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
