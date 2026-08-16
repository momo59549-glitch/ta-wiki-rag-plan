"""One frozen cross-sectional (not temporal) holdout for the Qlib Topk C trial."""
from __future__ import annotations

import argparse
import json
import tempfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from scripts.run_qlib_spike import H as spike_hash, _daily_hash, _daily_summary, _reduced_handler, _series_hash, reduced_feature_config
    from scripts.run_qlib_topk_approx import (
        ACCOUNT, BASE_COST, EXECUTE_END, STRESS_COST, TEST, TRADE_UNIT, _daily_series_hash,
        _official_metrics, _write_json_once, _write_pickle_once, _yearly_diagnostic, equal_weight_close_to_close_benchmark,
        quote_frame, write_topk_provider,
    )
    from scripts.run_vectorbt_candidate_screen import SCREEN_FIRST_SESSION, SCREEN_LAST_SESSION, contract, fixed_symbols, frames, state
except ModuleNotFoundError:
    from run_qlib_spike import H as spike_hash, _daily_hash, _daily_summary, _reduced_handler, _series_hash, reduced_feature_config
    from run_qlib_topk_approx import ACCOUNT, BASE_COST, EXECUTE_END, STRESS_COST, TEST, TRADE_UNIT, _daily_series_hash, _official_metrics, _write_json_once, _write_pickle_once, _yearly_diagnostic, equal_weight_close_to_close_benchmark, quote_frame, write_topk_provider
    from run_vectorbt_candidate_screen import SCREEN_FIRST_SESSION, SCREEN_LAST_SESSION, contract, fixed_symbols, frames, state


TRAIN = ("2015-01-05", "2017-12-31")
VALID = ("2018-01-01", "2018-12-31")
HOLDOUT_N = 200
TOPK, DROP = 50, 1  # mechanical C selection from the preceding five-trial artifact
SOURCE_RESULT = Path("data/qlib_spikes/alpha158-reduced-ohlcv-fixed200-2015-2021/result.json")
SOURCE_RESULT_HASH = "sha256:8c975f44c12663c1ac53e7dd5569db37179ae53ff99eb5242ca9e38b8d0d8e33"
DISCOVERY_HASH = "sha256:2247295066fc8a6a4c8945869c43e0e20c2203d4c3926f3e5e2beac7df23727e"
# v1 preserved the preflight and an aborted original200 re-fit because the
# nested handler used a package import unavailable in direct-script execution.
# v3 was deliberately interrupted before the required direct-CLI synthetic e2e
# audit; preserve it and use this fresh write-once destination after that audit.
OUTPUT = Path("data/qlib_spikes/cross-sectional-holdout-fixed-next200-2019-2021-v4")


def ordered_holdout_selection(frozen) -> dict:
    """Select next 200 solely from frozen coverage metadata, never outcomes."""
    original = fixed_symbols(frozen)
    bound = {entry.symbol: entry for entry in frozen.trend_entries}
    selected, scanned, exclusions = [], [], []
    for symbol in frozen.members:
        entry = bound.get(symbol)
        reason = None
        if entry is None:
            reason = "no_frozen_trend_entry"
        elif not entry.selected_rows:
            reason = "zero_selected_rows"
        elif entry.min_session is None or entry.min_session > SCREEN_FIRST_SESSION:
            reason = "starts_after_required_coverage_start"
        elif entry.max_session is None or entry.max_session < SCREEN_LAST_SESSION:
            reason = "ends_before_required_coverage_end"
        scanned.append(symbol)
        if reason is not None:
            exclusions.append({"symbol": symbol, "reason": reason})
            continue
        if symbol in original:
            continue
        selected.append(symbol)
        if len(selected) == HOLDOUT_N:
            break
    if len(original) != HOLDOUT_N or len(selected) != HOLDOUT_N or set(original) & set(selected):
        raise ValueError("cross-sectional holdout coverage selection invalid")
    return {"selection_rule": "ordered frozen members after original fixed200; selected only when frozen trend metadata spans 2015-01-05..2021-12-31 with rows; no outcomes consulted", "original_symbols": list(original), "holdout_symbols": selected, "scanned_prefix": scanned, "coverage_exclusions": exclusions, "coverage_start": str(SCREEN_FIRST_SESSION), "coverage_end": str(SCREEN_LAST_SESSION)}


def _load_source_model_contract(original: tuple[str, ...]) -> dict:
    if not SOURCE_RESULT.is_file():
        raise ValueError("original frozen Qlib source result missing")
    source = json.loads(SOURCE_RESULT.read_text(encoding="utf-8"))
    actual = source.pop("result_hash", None)
    if actual != SOURCE_RESULT_HASH or spike_hash(source) != actual:
        raise ValueError("original frozen Qlib source result hash invalid")
    expected_model = {"seed": 0, "num_leaves": 31, "learning_rate": .05, "feature_fraction": .8, "bagging_fraction": .8, "bagging_freq": 1}
    if source.get("symbols") != list(original) or source.get("model", {}).get("params") != expected_model or source["model"].get("num_boost_round") != 500 or source["model"].get("early_stopping_rounds") != 50:
        raise ValueError("original source model contract does not match frozen original200")
    return {"source_result_hash": actual, "model": source["model"], "feature_hash": source["feature_hash"], "provider_hash": source["provider_hash"]}


def _year_ic(values: pd.Series) -> dict:
    result = _daily_summary(values)
    if tuple(result["years"]) != ("2019", "2020", "2021"):
        raise ValueError("holdout IC calendar is incomplete")
    return result


def _portfolio_pass(ic: dict, rank_ic: dict, metrics: dict, annual: dict) -> bool:
    return bool(
        all(ic["years"][year]["mean"] > 0 and rank_ic["years"][year]["mean"] > 0 for year in ("2019", "2020", "2021"))
        and metrics["base"]["annualized_return"] > .08
        and metrics["base"]["max_drawdown"] > -.30
        and metrics["base"]["official_excess_diagnostic"]["annualized_return"] > .03
        and metrics["stress_2x"]["official_excess_diagnostic"]["annualized_return"] > 0
        and all(row["excess_return_arithmetic"] > 0 for row in annual.values())
    )


def synthetic_cli_e2e() -> dict:
    """Direct-script smoke through train, holdout prediction, annual report and publish.

    It uses a temporary synthetic provider and never loads the frozen market
    state.  This exists to catch direct-execution imports before a long real
    holdout run, not to evaluate a trading idea.
    """
    import qlib
    from qlib.backtest import backtest
    from qlib.config import REG_CN
    from qlib.contrib.model.gbdt import LGBModel
    from qlib.contrib.strategy.signal_strategy import TopkDropoutStrategy
    from qlib.data.dataset import DatasetH
    from qlib.contrib.data.handler import Alpha158

    root = Path(tempfile.mkdtemp(prefix="qlib-cross-sectional-holdout-e2e-"))
    dates = pd.date_range("2015-01-05", "2021-12-31", freq="B")
    raw = {field: {} for field in ("open", "high", "low", "close", "volume", "is_st")}
    for number in range(4):
        symbol = f"{number + 1:06d}"
        # Nonflat, deterministic OHLCV; no synthetic missing rows or labels.
        close = 10 + number + np.linspace(0, 8 + number, len(dates)) + np.sin(np.arange(len(dates)) / (9 + number))
        raw["close"][symbol] = pd.Series(close, index=dates)
        raw["open"][symbol] = pd.Series(close * .999, index=dates)
        raw["high"][symbol] = pd.Series(close * 1.01, index=dates)
        raw["low"][symbol] = pd.Series(close * .99, index=dates)
        raw["volume"][symbol] = pd.Series(1000.0 + number, index=dates)
        raw["is_st"][symbol] = pd.Series(False, index=dates)
    wide = {field: pd.DataFrame(values) for field, values in raw.items()}
    source = {symbol: quote_frame(wide, symbol) for symbol in sorted(raw["close"])}
    write_topk_provider(root / "provider", source)
    qlib.init(provider_uri=root / "provider", region=REG_CN, exp_manager={"class": "MLflowExpManager", "module_path": "qlib.workflow.expm", "kwargs": {"uri": "sqlite:///" + (root / "mlflow.db").as_posix(), "default_exp_name": "holdout-direct-cli-e2e"}})
    train_symbols, holdout_symbols = ["000001", "000002"], ["000003", "000004"]
    train_handler = _reduced_handler(train_symbols, "2015-01-05", "2021-12-31", TRAIN[0], TRAIN[1])
    train_dataset = DatasetH(handler=train_handler, segments={"train": TRAIN, "valid": VALID, "test": TEST})
    model = LGBModel(seed=0, num_leaves=4, learning_rate=.1)
    model.fit(train_dataset, num_boost_round=5, verbose_eval=False)
    class SyntheticHoldoutAlpha158(Alpha158):
        def get_feature_config(self):
            return reduced_feature_config()[:2]
    handler = SyntheticHoldoutAlpha158(instruments=holdout_symbols, start_time="2015-01-05", end_time="2021-12-31", fit_start_time=TRAIN[0], fit_end_time=TRAIN[1], learn_processors=[])
    dataset = DatasetH(handler=handler, segments={"test": TEST})
    prediction = model.predict(dataset, segment="test")
    benchmark, _ = equal_weight_close_to_close_benchmark({field: frame.loc[:, holdout_symbols] for field, frame in wide.items()})
    executor = {"class": "SimulatorExecutor", "module_path": "qlib.backtest.executor", "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True}}
    strategy = TopkDropoutStrategy(topk=2, n_drop=1, signal=prediction, only_tradable=True, forbid_all_trade_at_limit=True)
    port, indicator = backtest(start_time=TEST[0], end_time=EXECUTE_END, strategy=strategy, executor=executor, benchmark=benchmark, account=ACCOUNT, exchange_kwargs={"codes": holdout_symbols, "deal_price": "$open", "trade_unit": TRADE_UNIT, "limit_threshold": ("$limit_buy", "$limit_sell"), **BASE_COST})
    report = port["1day"][0]
    annual = _yearly_diagnostic(report)
    result = {"status": "synthetic_direct_cli_e2e_ok", "report_rows": len(report), "annual_years": list(annual), "prediction_rows": len(prediction), "temporary_only": True}
    result["result_hash"] = spike_hash(result)
    _write_json_once(root / "result.json", result)
    return result


def run() -> dict:
    import qlib
    from qlib.backtest import backtest
    from qlib.config import REG_CN
    from qlib.contrib.eva.alpha import calc_ic
    from qlib.contrib.evaluate import risk_analysis
    from qlib.contrib.data.handler import Alpha158
    from qlib.contrib.model.gbdt import LGBModel
    from qlib.contrib.strategy.signal_strategy import TopkDropoutStrategy
    from qlib.data.dataset import DatasetH

    output = OUTPUT.resolve()
    if output.exists():
        raise ValueError("cross-sectional holdout output already exists")
    frozen = state(contract("configs/gen3_trend_cache_quality.json"))
    selection = ordered_holdout_selection(frozen)
    original, holdout = tuple(selection["original_symbols"]), tuple(selection["holdout_symbols"])
    source_contract = _load_source_model_contract(original)
    # This is written before any model fit or holdout prediction, freezing every
    # selection/model/trial decision.  The later result binds its hash.
    preflight = {"schema_version": "qlib-cross-sectional-holdout-preflight/v1", "kind": "cross_sectional_holdout_not_temporal_oos", "selection": selection, "model_source": source_contract, "train": TRAIN, "valid": VALID, "test": TEST, "topk": TOPK, "n_drop": DROP, "deal_price": "$open", "trade_unit": TRADE_UNIT, "base_cost": BASE_COST, "stress_cost": STRESS_COST, "no_holdout_label_fit_or_early_stop": True, "no_parameter_variants": True, "no_2022_plus": True, "non_pit": True, "survivor_bias": True, "nonadjudicable": True}
    preflight["preflight_hash"] = spike_hash(preflight)
    output.mkdir(parents=True)
    _write_json_once(output / "preflight.json", preflight)
    raw, _, _ = frames(frozen, tuple(original + holdout))
    source = {symbol: quote_frame(raw, symbol) for symbol in tuple(original + holdout)}
    provider_hash = write_topk_provider(output / "provider", source)
    sqlite_uri = "sqlite:///" + (output / "mlflow.db").as_posix()
    qlib.init(provider_uri=output / "provider", region=REG_CN, exp_manager={"class": "MLflowExpManager", "module_path": "qlib.workflow.expm", "kwargs": {"uri": sqlite_uri, "default_exp_name": "qlib-cross-sectional-holdout-fixed-next200"}})
    # No model object was saved by the earlier official SignalRecord.  This is
    # the one permitted deterministic re-fit, using only original200 train and
    # validation segments plus exactly the frozen source-result hyperparameters.
    train_handler = _reduced_handler(list(original), "2015-01-05", "2021-12-31", TRAIN[0], TRAIN[1])
    train_dataset = DatasetH(handler=train_handler, segments={"train": TRAIN, "valid": VALID, "test": TEST})
    model = LGBModel(**source_contract["model"]["params"])
    model.fit(train_dataset, num_boost_round=source_contract["model"]["num_boost_round"], early_stopping_rounds=source_contract["model"]["early_stopping_rounds"], verbose_eval=False)
    model_hash = _write_pickle_once(output / "original200_refit_model.pkl", model)
    # Handler inference features have no Alpha158 default inference processor.
    # Construct this handler with no learn processor from the outset: merely
    # assigning [] after construction would be too late if Qlib had fitted it.
    class HoldoutReducedAlpha158(Alpha158):
        def get_feature_config(self):
            return reduced_feature_config()[:2]
    holdout_handler = HoldoutReducedAlpha158(instruments=list(holdout), start_time="2015-01-05", end_time="2021-12-31", fit_start_time=TRAIN[0], fit_end_time=TRAIN[1], learn_processors=[])
    holdout_dataset = DatasetH(handler=holdout_handler, segments={"test": TEST})
    prediction = model.predict(holdout_dataset, segment="test")
    label = holdout_dataset.prepare("test", col_set="label").iloc[:, 0]
    if not isinstance(prediction, pd.Series) or set(prediction.index.get_level_values("instrument")) != set(holdout):
        raise ValueError("holdout prediction identity invalid")
    ic, rank_ic = calc_ic(prediction, label, dropna=True)
    ic_summary, rank_summary = _year_ic(ic), _year_ic(rank_ic)
    prediction_hash, label_hash = _series_hash(prediction), _series_hash(label)
    _write_pickle_once(output / "holdout_prediction.pkl", prediction)
    _write_pickle_once(output / "holdout_label.pkl", label)
    benchmark, benchmark_identity = equal_weight_close_to_close_benchmark({field: frame.loc[:, list(holdout)] for field, frame in raw.items()})
    executor = {"class": "SimulatorExecutor", "module_path": "qlib.backtest.executor", "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True}}
    exchange = {"codes": list(holdout), "deal_price": "$open", "trade_unit": TRADE_UNIT, "limit_threshold": ("$limit_buy", "$limit_sell"), "volume_threshold": None}
    metrics, annual = {}, None
    for cost_id, costs in (("base", BASE_COST), ("stress_2x", STRESS_COST)):
        strategy = TopkDropoutStrategy(topk=TOPK, n_drop=DROP, signal=prediction, only_tradable=True, forbid_all_trade_at_limit=True)
        port, indicator = backtest(start_time=TEST[0], end_time=EXECUTE_END, strategy=strategy, executor=executor, benchmark=benchmark, account=ACCOUNT, exchange_kwargs={**exchange, **costs})
        metric = _official_metrics(port, indicator, costs)
        report, positions = port["1day"]
        indicator_frame = indicator["1day"][0]
        metric["official_record_hashes"] = {"daily_report": _write_pickle_once(output / f"{cost_id}_daily_report.pkl", report), "positions": _write_pickle_once(output / f"{cost_id}_positions.pkl", positions), "indicator": _write_pickle_once(output / f"{cost_id}_indicator.pkl", indicator_frame)}
        net = report["return"] - report["cost"]
        excess = net - report["bench"]
        excess_risk = risk_analysis(excess, freq="day", mode="product")
        risk = {str(index): float(row["risk"]) for index, row in excess_risk.iterrows()}
        metric["official_excess_diagnostic"] = {"annualized_return": risk.get("annualized_return"), "information_ratio": risk.get("information_ratio"), "max_drawdown": risk.get("max_drawdown"), "daily_excess_hash": _daily_series_hash(excess)}
        metrics[cost_id] = metric
        if cost_id == "base":
            annual = _yearly_diagnostic(report)
    result = {"schema_version": "qlib-cross-sectional-holdout/v1", "status": "completed_cross_sectional_holdout_nonadjudicable", "preflight_hash": preflight["preflight_hash"], "source_implementation_discovery_hash": DISCOVERY_HASH, "source": "trend_cache_adjusted", "source_universe_hash": frozen.universe_hash, "selection": selection, "model_source": source_contract, "model_refit_hash": model_hash, "provider_hash": provider_hash, "prediction_hash": prediction_hash, "label_hash": label_hash, "ic": ic_summary, "rank_ic": rank_summary, "ic_daily_hash": _daily_hash(ic), "rank_ic_daily_hash": _daily_hash(rank_ic), "benchmark": benchmark_identity, "strategy": {"topk": TOPK, "n_drop": DROP, "signal_timing": "prediction_T_close_used_by_official_Topk_shift_1_for_T_plus_1_trade"}, "exchange": {"deal_price": "$open", "trade_unit": TRADE_UNIT, "observed_gates": True}, "metrics": metrics, "annual": annual, "holdout_passes_all_preregistered_gates": _portfolio_pass(ic_summary, rank_summary, metrics, annual), "preregistered_gates": ["IC_and_RankIC_each_year_gt_0", "base_annualized_return_gt_8pct", "base_max_drawdown_lt_30pct", "base_excess_annualized_return_gt_3pct", "stress_excess_annualized_return_gt_0", "base_excess_each_year_gt_0"], "cross_sectional_holdout": True, "fresh_temporal_oos": False, "survivor_bias": True, "non_pit": True, "nonadjudicable": True, "not_candidate": True, "no_lockbox": True, "no_ledger": True, "limitations": ["not fresh temporal OOS", "selection is only frozen coverage metadata but source universe is survivor biased", "no authoritative trading calendar, suspension, historical ST, daily price limit, factor/corporate action receipt, or independent benchmark"]}
    result["result_hash"] = spike_hash(result)
    _write_json_once(output / "result.json", result)
    return {"status": result["status"], "artifact": str(output / "result.json"), "result_hash": result["result_hash"], "holdout_pass": result["holdout_passes_all_preregistered_gates"], "symbols": len(holdout)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-read-source", action="store_true")
    parser.add_argument("--synthetic-e2e", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.synthetic_e2e:
            print(json.dumps(synthetic_cli_e2e(), ensure_ascii=False, sort_keys=True))
            return 0
        if not args.confirm_read_source:
            raise ValueError("cross-sectional holdout requires --confirm-read-source")
        print(json.dumps(run(), ensure_ascii=False, sort_keys=True))
        return 0
    except (ValueError, OSError, TypeError, IndexError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
