"""Strict, non-PIT Microsoft Qlib Alpha158 compatibility spike.

This is intentionally not a research framework.  Standard Alpha158 includes
VWAP.  The frozen local main-board content has OHLCV only, so this spike uses a
named, explicit *reduced OHLCV subset* made by the official Alpha158DL feature
configuration API.  It never synthesizes VWAP or amount.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

try:  # module import in tests
    from scripts.run_vectorbt_candidate_screen import contract, fixed_symbols, frames, state
except ModuleNotFoundError:  # direct ``python scripts/run_qlib_spike.py``
    from run_vectorbt_candidate_screen import contract, fixed_symbols, frames, state

START, END = date(2015, 1, 5), date(2021, 12, 31)
TRAIN, VALID, TEST = ("2015-01-05", "2017-12-31"), ("2018-01-01", "2018-12-31"), ("2019-01-01", "2021-12-31")


def H(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


REDUCED_CONFIG = {"kbar": {}, "price": {"windows": [0], "feature": ["OPEN", "HIGH", "LOW"]}, "volume": {"windows": [0]}, "rolling": {}}


def reduced_feature_config() -> tuple[list[str], list[str], str]:
    from qlib.contrib.data.handler import Alpha158DL
    fields, names = Alpha158DL.get_feature_config(REDUCED_CONFIG)
    digest = H({"kind": "Alpha158_reduced_OHLCV_subset", "config": REDUCED_CONFIG, "fields": fields, "names": names})
    return fields, names, digest


def _require_frame(frame: pd.DataFrame) -> None:
    required = {"open", "high", "low", "close", "volume"}
    if set(frame.columns) != required or not isinstance(frame.index, pd.DatetimeIndex) or not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise ValueError("strict Qlib source frame schema")
    if frame.empty or not np.isfinite(frame.to_numpy(dtype=float)).all() or (frame <= 0).any().any():
        raise ValueError("Qlib source frame requires finite positive OHLCV")


def write_provider(provider_root: Path, symbol_frames: dict[str, pd.DataFrame]) -> str:
    """Write the minimal documented Qlib binary provider layout from explicit fields."""
    if provider_root.exists():
        raise ValueError("Qlib provider target must not already exist")
    if not symbol_frames or tuple(sorted(symbol_frames)) != tuple(symbol_frames):
        raise ValueError("symbol frames must be nonempty and sorted")
    for symbol, frame in symbol_frames.items():
        if not (isinstance(symbol, str) and len(symbol) == 6 and symbol.isdigit()):
            raise ValueError("strict Qlib symbol")
        _require_frame(frame)
    calendar = sorted({stamp for frame in symbol_frames.values() for stamp in frame.index})
    provider_root.mkdir(parents=True)
    (provider_root / "calendars").mkdir(); (provider_root / "instruments").mkdir(); (provider_root / "features").mkdir()
    (provider_root / "calendars" / "day.txt").write_text("\n".join(x.strftime("%Y-%m-%d") for x in calendar) + "\n", encoding="utf-8")
    rows = []
    identity = {"calendar": [x.strftime("%Y-%m-%d") for x in calendar], "symbols": {}}
    calendar_index = {stamp: i for i, stamp in enumerate(calendar)}
    for symbol, frame in symbol_frames.items():
        local = provider_root / "features" / symbol.lower(); local.mkdir()
        rows.append(f"{symbol}\t{frame.index[0].date()}\t{frame.index[-1].date()}")
        identity["symbols"][symbol] = {field: [float(x) for x in frame[field]] for field in sorted(frame.columns)}
        for field in sorted(frame.columns):
            values = np.full(len(calendar), np.nan, dtype="<f4")
            values[[calendar_index[x] for x in frame.index]] = frame[field].to_numpy(dtype="<f4")
            np.hstack([np.array([0], dtype="<f4"), values]).astype("<f4").tofile(local / f"{field}.day.bin")
    (provider_root / "instruments" / "all.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")
    identity_hash = H(identity)
    (provider_root / "conversion_identity.json").write_text(json.dumps({"schema_version": "qlib-provider/v1", "identity_hash": identity_hash, "fields": ["open", "high", "low", "close", "volume"], "non_pit": True}, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return identity_hash


def _reduced_handler(instruments, start, end, fit_start, fit_end):
    """Official Alpha158 class with its documented feature config overridden."""
    from qlib.contrib.data.handler import Alpha158
    class Alpha158ReducedOHLCV(Alpha158):
        def get_feature_config(self):
            return reduced_feature_config()[:2]
    return Alpha158ReducedOHLCV(instruments=instruments, start_time=start, end_time=end, fit_start_time=fit_start, fit_end_time=fit_end)


def official_fixture_smoke() -> dict:
    import qlib
    from qlib.config import REG_CN
    from qlib.contrib.model.gbdt import LGBModel
    from qlib.data.dataset import DatasetH
    from qlib.workflow import R
    root = Path(tempfile.mkdtemp(prefix="qlib-alpha158-fixture-"))
    days = pd.date_range("2020-01-01", periods=90, freq="B")
    samples = {}
    for number in range(3):
        value = np.linspace(10 + number, 30 + number, len(days))
        samples[f"{number + 1:06d}"] = pd.DataFrame({"open": value, "high": value * 1.02, "low": value * .98, "close": value * 1.01, "volume": np.full(len(days), 1000.)}, index=days)
    identity = write_provider(root / "provider", samples)
    sqlite_uri = "sqlite:///" + (root / "mlflow.db").as_posix()
    qlib.init(provider_uri=root / "provider", region=REG_CN, exp_manager={"class": "MLflowExpManager", "module_path": "qlib.workflow.expm", "kwargs": {"uri": sqlite_uri, "default_exp_name": "qlib-alpha158-fixture"}})
    handler = _reduced_handler(list(samples), "2020-01-01", "2020-05-05", "2020-01-01", "2020-03-20")
    dataset = DatasetH(handler=handler, segments={"train": ("2020-01-01", "2020-03-20"), "valid": ("2020-03-23", "2020-04-10"), "test": ("2020-04-13", "2020-05-05")})
    with R.start(experiment_name="qlib-alpha158-fixture"):
        model = LGBModel(seed=0, num_leaves=4, learning_rate=.1)
        model.fit(dataset, num_boost_round=5, verbose_eval=False)
        prediction = model.predict(dataset, segment="test")
        R.save_objects(prediction=prediction)
    fields, names, feature_hash = reduced_feature_config()
    return {"status": "official_alpha158_reduced_ohlcv_lgb_workflow_smoke_ok", "identity_hash": identity, "feature_hash": feature_hash, "feature_count": len(names), "feature_names": names, "prediction_rows": len(prediction), "qlib_version": qlib.__version__, "temp_artifacts": str(root)}


def real_source_sufficiency() -> dict:
    frozen = state(contract("configs/gen3_trend_cache_quality.json"))
    symbols = fixed_symbols(frozen)
    raw, _, _ = frames(frozen, symbols)
    # `frames` is built from the strict canonical frozen rows. Its schema is
    # exactly the local content capability, so this check cannot overlook an
    # unbound external column.
    fields, names, feature_hash = reduced_feature_config()
    return {"status": "eligible_alpha158_reduced_ohlcv_subset", "symbols": len(symbols), "window": [str(START), str(END)], "available_bound_fields": sorted(raw), "standard_alpha158_vwap_absent": True, "feature_hash": feature_hash, "feature_count": len(names), "feature_names": names, "feature_expressions": fields, "reason": "official Alpha158DL config excludes VWAP explicitly; no amount/VWAP field is synthesized", "non_pit": True, "survivor_bias": True, "no_trial_budget": True}


def run_real_reduced_ohlcv() -> dict:
    """One fixed official Qlib Alpha158-reduced fit; no portfolio/backtest."""
    import qlib
    from qlib.config import REG_CN
    from qlib.contrib.eva.alpha import calc_ic
    from qlib.contrib.model.gbdt import LGBModel
    from qlib.data.dataset import DatasetH
    from qlib.workflow import R

    frozen = state(contract("configs/gen3_trend_cache_quality.json"))
    symbols = fixed_symbols(frozen)
    raw, _, _ = frames(frozen, symbols)
    fields, names, feature_hash = reduced_feature_config()
    output = Path("data/qlib_spikes/alpha158-reduced-ohlcv-fixed200-2015-2021").resolve()
    if output.exists():
        raise ValueError("fixed Qlib spike output already exists")
    source = {}
    for symbol in symbols:
        frame = pd.DataFrame({field: raw[field][symbol] for field in ("open", "high", "low", "close", "volume")})
        frame = frame.loc[(frame.index.date >= START) & (frame.index.date <= END)].dropna()
        _require_frame(frame)
        source[symbol] = frame
    output.mkdir(parents=True)
    provider_hash = write_provider(output / "provider", source)
    sqlite_uri = "sqlite:///" + (output / "mlflow.db").as_posix()
    qlib.init(provider_uri=output / "provider", region=REG_CN, exp_manager={"class": "MLflowExpManager", "module_path": "qlib.workflow.expm", "kwargs": {"uri": sqlite_uri, "default_exp_name": "alpha158-reduced-ohlcv-fixed200"}})
    handler = _reduced_handler(list(symbols), START, END, TRAIN[0], TRAIN[1])
    dataset = DatasetH(handler=handler, segments={"train": TRAIN, "valid": VALID, "test": TEST})
    params = {"seed": 0, "num_leaves": 31, "learning_rate": .05, "feature_fraction": .8, "bagging_fraction": .8, "bagging_freq": 1}
    with R.start(experiment_name="alpha158-reduced-ohlcv-fixed200"):
        model = LGBModel(**params)
        model.fit(dataset, num_boost_round=500, early_stopping_rounds=50, verbose_eval=False)
        prediction = model.predict(dataset, segment="test")
        label = dataset.prepare("test", col_set="label").iloc[:, 0]
        ic, rank_ic = calc_ic(prediction, label, dropna=True)
        R.save_objects(prediction=prediction)
    metrics = {"ic_mean": float(ic.mean()), "rank_ic_mean": float(rank_ic.mean()), "ic_days": int(len(ic)), "rank_ic_days": int(len(rank_ic)), "prediction_rows": int(len(prediction))}
    result = {"schema_version": "qlib-alpha158-reduced-ohlcv/v1", "status": "completed_nonadjudicable_prediction_ic", "provider_hash": provider_hash, "feature_hash": feature_hash, "feature_count": len(names), "feature_names": names, "feature_expressions": fields, "symbols": list(symbols), "source_universe_hash": frozen.universe_hash, "window": [str(START), str(END)], "segments": {"train": TRAIN, "valid": VALID, "test": TEST}, "model": {"class": "qlib.contrib.model.gbdt.LGBModel", "params": params, "num_boost_round": 500, "early_stopping_rounds": 50}, "official_metric_api": "qlib.contrib.eva.alpha.calc_ic", "metrics": metrics, "standard_alpha158": False, "designation": "Alpha158 reduced OHLCV subset", "non_pit": True, "survivor_bias": True, "no_trial_budget": True, "no_lockbox": True, "backtest": "not_run; official Topk backtest has not been admitted by this spike"}
    result["result_hash"] = H(result)
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    return {"status": result["status"], "artifact": str(output / "result.json"), "result_hash": result["result_hash"], "symbols": len(symbols), **metrics}


def _series_hash(values: pd.Series) -> str:
    if not isinstance(values.index, pd.MultiIndex) or tuple(values.index.names) != ("datetime", "instrument"):
        raise ValueError("Qlib prediction/label index must be datetime,instrument")
    digest = hashlib.sha256()
    for (stamp, instrument), value in values.sort_index().items():
        if not isinstance(stamp, pd.Timestamp) or not isinstance(instrument, str):
            raise ValueError("Qlib prediction/label canonical value invalid")
        number = float(value)
        if np.isinf(number):
            raise ValueError("Qlib prediction/label canonical value invalid")
        encoded = "__NAN__" if np.isnan(number) else format(number, ".17g")
        digest.update(f"{stamp.isoformat()}\x1f{instrument}\x1f{encoded}\n".encode())
    return "sha256:" + digest.hexdigest()


def _daily_summary(values: pd.Series) -> dict:
    if values.empty or not np.isfinite(values.to_numpy(dtype=float)).all():
        raise ValueError("Qlib daily IC series invalid")
    out = {"mean": float(values.mean()), "std": float(values.std()), "ir": float(values.mean() / values.std()), "positive_day_ratio": float((values > 0).mean()), "count": int(len(values))}
    by_year = {}
    for year, group in values.groupby(values.index.year):
        by_year[str(year)] = {"mean": float(group.mean()), "count": int(len(group))}
    out["years"] = by_year
    return out


def _daily_hash(values: pd.Series) -> str:
    if not isinstance(values.index, pd.DatetimeIndex):
        raise ValueError("daily IC index must be datetime")
    digest = hashlib.sha256()
    for stamp, value in values.items():
        if not np.isfinite(float(value)):
            raise ValueError("daily IC value invalid")
        digest.update(f"{stamp.isoformat()}\x1f{float(value):.17g}\n".encode())
    return "sha256:" + digest.hexdigest()


def run_diagnostic_reproduction() -> dict:
    """Re-fit the single frozen hypothesis and save only official Qlib diagnostics."""
    import qlib
    from qlib.config import REG_CN
    from qlib.contrib.eva.alpha import calc_ic
    from qlib.contrib.model.gbdt import LGBModel
    from qlib.data.dataset import DatasetH
    from qlib.workflow import R
    from qlib.workflow.record_temp import SigAnaRecord, SignalRecord

    base = Path("data/qlib_spikes/alpha158-reduced-ohlcv-fixed200-2015-2021").resolve()
    source_path = base / "result.json"
    if not source_path.is_file():
        raise ValueError("fixed Qlib source result missing")
    source = json.loads(source_path.read_text(encoding="utf-8"))
    expected_hash = source.pop("result_hash", None)
    if expected_hash != H(source) or source.get("status") != "completed_nonadjudicable_prediction_ic":
        raise ValueError("fixed Qlib source result identity invalid")
    _, names, feature_hash = reduced_feature_config()
    if source.get("feature_hash") != feature_hash or source.get("feature_names") != names:
        raise ValueError("fixed Qlib feature identity mismatch")
    # Earlier attempts remain gitignored failure evidence: first used the
    # experiment object instead of the recorder, then rejected Qlib's expected
    # terminal forward-label NaNs while hashing.
    destination = Path("data/qlib_spikes/alpha158-reduced-ohlcv-fixed200-2015-2021-diagnostic-v3").resolve()
    if destination.exists():
        raise ValueError("fixed Qlib diagnostic output already exists")
    provider_identity = json.loads((base / "provider" / "conversion_identity.json").read_text(encoding="utf-8"))
    if provider_identity.get("identity_hash") != source.get("provider_hash"):
        raise ValueError("fixed Qlib provider identity mismatch")
    destination.mkdir(parents=True)
    sqlite_uri = "sqlite:///" + (destination / "mlflow.db").as_posix()
    qlib.init(provider_uri=base / "provider", region=REG_CN, exp_manager={"class": "MLflowExpManager", "module_path": "qlib.workflow.expm", "kwargs": {"uri": sqlite_uri, "default_exp_name": "alpha158-reduced-ohlcv-fixed200-diagnostic"}})
    handler = _reduced_handler(source["symbols"], START, END, TRAIN[0], TRAIN[1])
    dataset = DatasetH(handler=handler, segments={"train": TRAIN, "valid": VALID, "test": TEST})
    params = source["model"]["params"]
    with R.start(experiment_name="alpha158-reduced-ohlcv-fixed200-diagnostic"):
        recorder = R.get_recorder()
        model = LGBModel(**params)
        model.fit(dataset, num_boost_round=source["model"]["num_boost_round"], early_stopping_rounds=source["model"]["early_stopping_rounds"], verbose_eval=False)
        SignalRecord(model=model, dataset=dataset, recorder=recorder).generate()
        SigAnaRecord(recorder=recorder).generate()
        prediction = recorder.load_object("pred.pkl").iloc[:, 0]
        label = recorder.load_object("label.pkl").iloc[:, 0]
        ic, rank_ic = calc_ic(prediction, label, dropna=True)
    tolerance = 1e-12
    if abs(float(ic.mean()) - source["metrics"]["ic_mean"]) > tolerance or abs(float(rank_ic.mean()) - source["metrics"]["rank_ic_mean"]) > tolerance:
        raise ValueError("deterministic Qlib reproduction differs from fixed result")
    result = {"schema_version": "qlib-alpha158-reduced-ohlcv-diagnostic/v1", "status": "completed_nonadjudicable_diagnostic", "source_result_hash": expected_hash, "provider_hash": source["provider_hash"], "feature_hash": feature_hash, "prediction_hash": _series_hash(prediction), "label_hash": _series_hash(label), "prediction_rows": int(len(prediction)), "label_rows": int(len(label)), "official_records": ["qlib.workflow.record_temp.SignalRecord", "qlib.workflow.record_temp.SigAnaRecord"], "official_metric_api": "qlib.contrib.eva.alpha.calc_ic", "ic": _daily_summary(ic), "rank_ic": _daily_summary(rank_ic), "ic_daily_hash": _daily_hash(ic), "rank_ic_daily_hash": _daily_hash(rank_ic), "reproduction_tolerance": tolerance, "standard_alpha158": False, "designation": "Alpha158 reduced OHLCV subset", "same_fixed_hypothesis": True, "non_pit": True, "survivor_bias": True, "no_trial_budget": True, "no_lockbox": True, "backtest": "not_run"}
    result["result_hash"] = H(result)
    (destination / "diagnostic.json").write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    return {"status": result["status"], "artifact": str(destination / "diagnostic.json"), "result_hash": result["result_hash"], "prediction_hash": result["prediction_hash"], "label_hash": result["label_hash"], "ic": result["ic"], "rank_ic": result["rank_ic"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("fixture-smoke", "check-real-source", "run-real-reduced-ohlcv", "diagnose-reproduction"))
    parser.add_argument("--confirm-read-source", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "fixture-smoke":
            result = official_fixture_smoke()
        elif args.command == "check-real-source":
            if not args.confirm_read_source:
                raise ValueError("check-real-source requires --confirm-read-source")
            result = real_source_sufficiency()
        elif args.command == "run-real-reduced-ohlcv":
            if not args.confirm_read_source:
                raise ValueError("run-real-reduced-ohlcv requires --confirm-read-source")
            result = run_real_reduced_ohlcv()
        else:
            if not args.confirm_read_source:
                raise ValueError("diagnose-reproduction requires --confirm-read-source")
            result = run_diagnostic_reproduction()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["status"].endswith("_ok") or result["status"].startswith(("eligible_", "completed_")) else 2
    except (ValueError, OSError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
