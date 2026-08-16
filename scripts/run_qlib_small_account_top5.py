"""Fixed 30,000 RMB weekly Top-5 Qlib implementation diagnostic.

The strategy is deliberately a small thin subclass: it chooses constrained
orders, while Qlib's Exchange, Position and SimulatorExecutor remain solely
responsible for fills, costs, rounding validation and account accounting.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from scripts.run_qlib_spike import _series_hash
    from scripts.run_qlib_topk_approx import (
        BASE_COST,
        EXECUTE_END,
        PREDICTION,
        STRESS_COST,
        TEST,
        _daily_series_hash,
        _write_json_once,
        _write_pickle_once,
        equal_weight_close_to_close_benchmark,
        load_saved_prediction,
        quote_frame,
        write_topk_provider,
    )
    from scripts.run_vectorbt_candidate_screen import contract, fixed_symbols, frames, state
except ModuleNotFoundError:
    from run_qlib_spike import _series_hash
    from run_qlib_topk_approx import BASE_COST, EXECUTE_END, PREDICTION, STRESS_COST, TEST, _daily_series_hash, _write_json_once, _write_pickle_once, equal_weight_close_to_close_benchmark, load_saved_prediction, quote_frame, write_topk_provider
    from run_vectorbt_candidate_screen import contract, fixed_symbols, frames, state


ACCOUNT = 30_000.0
RESERVE_CASH = 5_000.0
RISK_CAPITAL = 25_000.0
TOPK = 5
PER_POSITION_CAP = RISK_CAPITAL / TOPK
TRADE_UNIT = 100
OUTPUT = Path("data/qlib_spikes/small-account-top5-weekly-fixed-original-holdout-2019-2021-v2")
V1_OUTPUT = Path("data/qlib_spikes/small-account-top5-weekly-fixed-original-holdout-2019-2021-v1")
CORRECTION = Path("data/qlib_spikes/small-account-top5-weekly-fixed-original-holdout-2019-2021-v1-reporting-correction-v1.json")
HOLDOUT_RESULT = Path("data/qlib_spikes/cross-sectional-holdout-fixed-next200-2019-2021-v4/result.json")
HOLDOUT_PREDICTION = HOLDOUT_RESULT.parent / "holdout_prediction.pkl"


def H(value: object) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _fee(value: float, costs: dict) -> float:
    return max(value * float(costs["open_cost"]), float(costs["min_cost"]))


def affordable_lot(price: float, available_cash: float, costs: dict) -> tuple[float, bool]:
    """Return an official 100-share order amount and whether minimum fee binds."""
    if not math.isfinite(price) or price <= 0 or not math.isfinite(available_cash):
        return 0.0, False
    cap = min(PER_POSITION_CAP, available_cash - RESERVE_CASH)
    lot = math.floor(cap / price / TRADE_UNIT) * TRADE_UNIT
    while lot > 0:
        value = lot * price
        cost = _fee(value, costs)
        if value + cost <= PER_POSITION_CAP and value + cost <= available_cash - RESERVE_CASH:
            return float(lot), cost == float(costs["min_cost"])
        lot -= TRADE_UNIT
    return 0.0, False


def first_observed_session_of_week(current: pd.Timestamp, previous: pd.Timestamp | None) -> bool:
    return previous is None or current.isocalendar()[:2] != previous.isocalendar()[:2]


def _holdout_prediction() -> tuple[pd.Series, dict]:
    value = json.loads(HOLDOUT_RESULT.read_text(encoding="utf-8"))
    claimed = value.pop("result_hash", None)
    if claimed != H(value) or value.get("status") != "completed_cross_sectional_holdout_nonadjudicable":
        raise ValueError("frozen holdout result identity invalid")
    prediction = pd.read_pickle(HOLDOUT_PREDICTION)
    if isinstance(prediction, pd.DataFrame):
        if list(prediction.columns) != ["score"]:
            raise ValueError("holdout prediction schema invalid")
        prediction = prediction.iloc[:, 0]
    if not isinstance(prediction, pd.Series) or _series_hash(prediction) != value.get("prediction_hash"):
        raise ValueError("holdout prediction hash invalid")
    return prediction, {"holdout_result_hash": claimed, "prediction_hash": value["prediction_hash"]}


def _metrics(portfolio_metrics: dict, indicator_metrics: dict, costs: dict) -> tuple[dict, pd.DataFrame, object, object]:
    from qlib.contrib.evaluate import risk_analysis

    report, positions = portfolio_metrics["1day"]
    indicator = indicator_metrics["1day"][0]
    required = {"account", "cash", "return", "cost", "turnover", "bench"}
    if not isinstance(report, pd.DataFrame) or not required.issubset(report.columns):
        raise ValueError("Qlib small-account report schema invalid")
    net = report["return"] - report["cost"]
    risk = {str(index): float(row["risk"]) for index, row in risk_analysis(net, freq="day", mode="product").iterrows()}
    excess = net - report["bench"]
    excess_risk = {str(index): float(row["risk"]) for index, row in risk_analysis(excess, freq="day", mode="product").iterrows()}
    holdings, cash = [], []
    if not isinstance(positions, dict):
        raise ValueError("Qlib positions record schema invalid")
    for position in positions.values():
        holdings.append(len(position.get_stock_list()))
        cash.append(float(position.get_cash()))
    if not holdings or min(cash) < -1e-8:
        raise ValueError("Qlib account cash/position invariant invalid")
    annual = {}
    for year, rows in report.groupby(report.index.year):
        strategy = float((1 + rows["return"] - rows["cost"]).prod() - 1)
        benchmark = float((1 + rows["bench"]).prod() - 1)
        annual[str(year)] = {"strategy_net_return": strategy, "equal_weight_benchmark_return": benchmark, "excess_return_arithmetic": strategy - benchmark, "sessions": int(len(rows))}
    if tuple(annual) != ("2019", "2020", "2021"):
        raise ValueError("small-account annual window invalid")
    if not isinstance(indicator, pd.DataFrame):
        raise ValueError("Qlib indicator record schema invalid")
    if "count" in indicator.index:
        trades = float(indicator.loc["count"].sum())
    elif "count" in indicator.columns:
        trades = float(indicator["count"].sum())
    else:
        raise ValueError("Qlib official indicator lacks trade count")
    return ({
        "official_report_api": "qlib.backtest.backtest",
        "annualization": "Qlib_official_risk_analysis_daily",
        "annualized_return": risk.get("annualized_return"),
        "total_return_from_official_final_account": float(report["account"].iloc[-1] / ACCOUNT - 1),
        "max_drawdown": risk.get("max_drawdown"),
        "official_excess": {"annualized_return": excess_risk.get("annualized_return"), "information_ratio": excess_risk.get("information_ratio"), "max_drawdown": excess_risk.get("max_drawdown"), "daily_excess_hash": _daily_series_hash(excess)},
        "annual": annual,
        "median_holdings": float(np.median(holdings)),
        "min_cash": min(cash),
        "mean_cash_ratio": float(report["cash"].mean() / ACCOUNT),
        "official_trade_count": trades,
        "total_cost_reported": float(report["total_cost"].iloc[-1]) if "total_cost" in report else None,
        "cost": costs,
    }, report, positions, indicator)


def official_trade_count(indicator: object) -> float:
    """Read Qlib's saved indicator count without rerunning a backtest."""
    if not isinstance(indicator, pd.DataFrame):
        raise ValueError("Qlib indicator record schema invalid")
    values = indicator.loc["count"] if "count" in indicator.index else indicator["count"] if "count" in indicator.columns else None
    if values is None:
        raise ValueError("Qlib official indicator lacks trade count")
    count = float(values.sum())
    if not math.isfinite(count) or count < 0:
        raise ValueError("Qlib official trade count invalid")
    return count


def reporting_correction() -> dict:
    """Publish missing indicator counts by strict read-only reference to v1."""
    if CORRECTION.exists():
        raise ValueError("small-account reporting correction already exists")
    source = json.loads((V1_OUTPUT / "result.json").read_text(encoding="utf-8"))
    source_hash = source.pop("result_hash", None)
    if source_hash != H(source) or source.get("status") != "completed_nonadjudicable_implementation_trial_6":
        raise ValueError("small-account v1 result identity invalid")
    corrected = {}
    for sample_name, sample in source["samples"].items():
        corrected_metrics = {}
        for cost_id, metric in sample["metrics"].items():
            indicator_path = V1_OUTPUT / sample_name / f"{cost_id}_indicator.pkl"
            recorded_hash = metric.get("official_record_hashes", {}).get("indicator")
            digest = hashlib.sha256(indicator_path.read_bytes()).hexdigest()
            if recorded_hash != "sha256:" + digest:
                raise ValueError("small-account v1 indicator record hash invalid")
            indicator = pickle.loads(indicator_path.read_bytes())
            count = official_trade_count(indicator)
            before = {key: value for key, value in metric.items() if key != "official_trade_count"}
            after = {**before, "official_trade_count": count}
            if H(before) != H({key: value for key, value in after.items() if key != "official_trade_count"}):
                raise ValueError("non-trade metric mutation during correction")
            corrected_metrics[cost_id] = {"v1_non_trade_metrics_hash": H(before), "corrected_non_trade_metrics_hash": H({key: value for key, value in after.items() if key != "official_trade_count"}), "metrics": after}
        corrected[sample_name] = {"passes_preregistered_feasibility_gate": sample["passes_preregistered_feasibility_gate"], "metrics": corrected_metrics}
    result = {"schema_version": "qlib-small-account-top5-reporting-correction/v1", "status": "completed_read_only_reporting_correction_same_implementation_trial_6", "source_result": str((V1_OUTPUT / "result.json").resolve()), "source_result_hash": source_hash, "correction": "official indicator count was present as a column, not an index; only official_trade_count is added", "samples": corrected, "no_market_read": True, "no_backtest_rerun": True, "same_trial": 6}
    result["correction_hash"] = H(result)
    _write_json_once(CORRECTION, result)
    return {"status": result["status"], "artifact": str(CORRECTION), "correction_hash": result["correction_hash"]}


def _passes(metrics: dict) -> bool:
    base, stress = metrics["base"], metrics["stress_2x"]
    values = (base["annualized_return"], base["max_drawdown"], stress["annualized_return"], base["official_excess"]["annualized_return"], stress["official_excess"]["annualized_return"])
    return bool(
        all(value is not None and math.isfinite(value) for value in values)
        and base["annualized_return"] > .08
        and base["max_drawdown"] > -.30
        and stress["annualized_return"] > 0
        and base["official_excess"]["annualized_return"] > 0
        and stress["official_excess"]["annualized_return"] > 0
        and base["median_holdings"] >= 4
        and min(base["min_cash"], stress["min_cash"]) >= -1e-8
    )


def weekly_top5_strategy(signal: pd.Series, costs: dict):
    """Build a narrow official TopkDropoutStrategy subclass, not an engine."""
    from qlib.backtest.decision import Order, OrderDir, TradeDecisionWO
    from qlib.contrib.strategy.signal_strategy import TopkDropoutStrategy

    class WeeklyTop5SmallAccountStrategy(TopkDropoutStrategy):
        def __init__(self):
            super().__init__(topk=TOPK, n_drop=1, signal=signal, only_tradable=True, forbid_all_trade_at_limit=True)
            self.initial_rebalance_done = False
            self.high_price_skips = 0
            self.minimum_fee_bound_planned_buys = 0
            self.weekly_events: list[dict] = []

        def _buy_order(self, code, start, end, cash):
            price = self.trade_exchange.get_deal_price(code, start, end, OrderDir.BUY)
            if price is None or not math.isfinite(float(price)):
                return None
            amount, min_fee_binds = affordable_lot(float(price), cash, costs)
            if amount <= 0:
                self.high_price_skips += 1
                return None
            factor = self.trade_exchange.get_factor(code, start, end)
            amount = self.trade_exchange.round_amount_by_trade_unit(amount, factor, code, start, end)
            if amount <= 0:
                self.high_price_skips += 1
                return None
            value = float(price) * amount
            if value + _fee(value, costs) > min(PER_POSITION_CAP, cash - RESERVE_CASH) + 1e-8:
                self.high_price_skips += 1
                return None
            if min_fee_binds:
                self.minimum_fee_bound_planned_buys += 1
            return Order(stock_id=code, amount=amount, direction=Order.BUY, start_time=start, end_time=end, factor=factor)

        def generate_trade_decision(self, execute_result=None):
            step = self.trade_calendar.get_trade_step()
            start, end = self.trade_calendar.get_step_time(step)
            try:
                previous_start, _ = self.trade_calendar.get_step_time(step, shift=1)
            except (IndexError, ValueError):
                previous_start = None
            if not first_observed_session_of_week(pd.Timestamp(start), None if previous_start is None else pd.Timestamp(previous_start)):
                return TradeDecisionWO([], self)
            if previous_start is None:
                return TradeDecisionWO([], self)
            pred = self.signal.get_signal(start_time=previous_start, end_time=previous_start)
            if isinstance(pred, pd.DataFrame):
                pred = pred.iloc[:, 0]
            if pred is None or len(pred) == 0:
                return TradeDecisionWO([], self)
            pred = pred.dropna().sort_values(ascending=False)
            current = copy.deepcopy(self.trade_position)
            held = list(current.get_stock_list())
            ranked = list(pred.index)
            sells = []
            initial = not self.initial_rebalance_done
            if initial:
                self.initial_rebalance_done = True
                buy_limit = TOPK
            elif len(held) < TOPK:
                buy_limit = 1
            else:
                held_scores = pred.reindex(held).fillna(-np.inf)
                worst = held_scores.sort_values().index[0]
                candidates = [code for code in ranked if code not in held]
                if not candidates or pred[candidates[0]] <= held_scores[worst]:
                    self.weekly_events.append({"session": str(pd.Timestamp(start).date()), "kind": "hold"})
                    return TradeDecisionWO([], self)
                sell = Order(stock_id=worst, amount=current.get_stock_amount(worst), direction=Order.SELL, start_time=start, end_time=end)
                if not self.trade_exchange.check_order(sell):
                    self.weekly_events.append({"session": str(pd.Timestamp(start).date()), "kind": "sell_blocked"})
                    return TradeDecisionWO([], self)
                self.trade_exchange.deal_order(sell, position=current)
                sells = [sell]
                held.remove(worst)
                buy_limit = 1
            buys = []
            for code in ranked:
                if len(buys) >= buy_limit or code in held:
                    continue
                if not self.trade_exchange.is_stock_tradable(code, start, end, direction=OrderDir.BUY):
                    continue
                order = self._buy_order(code, start, end, current.get_cash())
                if order is None:
                    continue
                # Simulate against a copy only to reserve cash while Qlib later
                # remains the sole actual settlement/fill authority.
                self.trade_exchange.deal_order(order, position=current)
                buys.append(order)
                held.append(code)
            if sells and not buys:
                self.weekly_events.append({"session": str(pd.Timestamp(start).date()), "kind": "replacement_unaffordable"})
                return TradeDecisionWO([], self)
            self.weekly_events.append({"session": str(pd.Timestamp(start).date()), "kind": "initial" if initial else "weekly", "sells": len(sells), "buys": len(buys)})
            return TradeDecisionWO(sells + buys, self)

    return WeeklyTop5SmallAccountStrategy()


def _run_sample(output: Path, name: str, symbols: tuple[str, ...], prediction: pd.Series, identity: dict) -> dict:
    import qlib
    from qlib.backtest import backtest
    from qlib.config import REG_CN

    frozen = state(contract("configs/gen3_trend_cache_quality.json"))
    raw, _, _ = frames(frozen, symbols)
    source = {symbol: quote_frame(raw, symbol) for symbol in symbols}
    provider = output / name / "provider"
    provider_hash = write_topk_provider(provider, source)
    qlib.init(provider_uri=provider, region=REG_CN, exp_manager={"class": "MLflowExpManager", "module_path": "qlib.workflow.expm", "kwargs": {"uri": "sqlite:///" + (output / name / "mlflow.db").as_posix(), "default_exp_name": f"qlib-small-account-{name}"}})
    benchmark, benchmark_identity = equal_weight_close_to_close_benchmark({field: values.loc[:, list(symbols)] for field, values in raw.items()})
    executor = {"class": "SimulatorExecutor", "module_path": "qlib.backtest.executor", "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True}}
    exchange = {"codes": list(symbols), "deal_price": "$open", "trade_unit": TRADE_UNIT, "limit_threshold": ("$limit_buy", "$limit_sell"), "volume_threshold": None}
    metrics = {}
    for cost_id, costs in (("base", BASE_COST), ("stress_2x", STRESS_COST)):
        strategy = weekly_top5_strategy(prediction, costs)
        portfolio, indicator = backtest(start_time=TEST[0], end_time=EXECUTE_END, strategy=strategy, executor=executor, benchmark=benchmark, account=ACCOUNT, exchange_kwargs={**exchange, **costs})
        metric, report, positions, indicator_record = _metrics(portfolio, indicator, costs)
        metric["strategy_constraints"] = {"weekly_events": strategy.weekly_events, "high_price_skips": strategy.high_price_skips, "minimum_fee_bound_planned_buys": strategy.minimum_fee_bound_planned_buys, "initial_build_max_buys": TOPK, "subsequent_max_sells": 1, "subsequent_max_buys": 1}
        metric["official_record_hashes"] = {"report": _write_pickle_once(output / name / f"{cost_id}_report.pkl", report), "positions": _write_pickle_once(output / name / f"{cost_id}_positions.pkl", positions), "indicator": _write_pickle_once(output / name / f"{cost_id}_indicator.pkl", indicator_record)}
        metrics[cost_id] = metric
    return {"sample": name, "symbols": list(symbols), "prediction": identity, "provider_hash": provider_hash, "benchmark": benchmark_identity, "metrics": metrics, "passes_preregistered_feasibility_gate": _passes(metrics)}


def run() -> dict:
    if OUTPUT.exists():
        raise ValueError("small-account Top5 output already exists")
    frozen = state(contract("configs/gen3_trend_cache_quality.json"))
    original = fixed_symbols(frozen)
    original_prediction, original_identity = load_saved_prediction()
    holdout_prediction, holdout_identity = _holdout_prediction()
    holdout_symbols = tuple(sorted(set(holdout_prediction.index.get_level_values("instrument"))))
    if len(original) != 200 or len(holdout_symbols) != 200 or set(original) & set(holdout_symbols):
        raise ValueError("frozen original/holdout small-account sample identity invalid")
    OUTPUT.mkdir(parents=True)
    result = {
        "schema_version": "qlib-small-account-top5-weekly/v1",
        "status": "completed_nonadjudicable_implementation_trial_6",
        "trial_counting": "This is implementation trial 6. If formally counted in the global 42 budget, it consumes one additional trial; it is not in the lockbox or candidate ledger.",
        "configuration": {"account_rmb": ACCOUNT, "topk": TOPK, "risk_capital_rmb": RISK_CAPITAL, "reserve_cash_rmb": RESERVE_CASH, "per_position_all_including_cost_cap_rmb": PER_POSITION_CAP, "trade_unit": TRADE_UNIT, "schedule": "first observed exchange session of ISO week; initial weekly signal date may build up to five; later dates at most one sell and one buy", "signal_timing": "previous observed session T close prediction -> next ISO-week first observed session T+1 open", "base_cost": BASE_COST, "stress_cost": STRESS_COST},
        "source": "trend_cache_adjusted", "universe_hash": frozen.universe_hash,
        "samples": {"original200": _run_sample(OUTPUT, "original200", original, original_prediction, original_identity), "holdout200": _run_sample(OUTPUT, "holdout200", holdout_symbols, holdout_prediction, holdout_identity)},
        "non_pit": True, "survivor_biased": True, "not_temporal_oos": True, "nonadjudicable": True, "no_lockbox": True, "no_candidate": True, "no_2022_plus": True,
        "limitations": ["observed tradability gates are not official suspension, historical ST, or price-limit evidence", "equal-weight benchmark is costless survivor-biased diagnostic, not an index", "current manifest reconstruction is non-PIT"],
    }
    result["result_hash"] = H(result)
    _write_json_once(OUTPUT / "result.json", result)
    return {"status": result["status"], "artifact": str(OUTPUT / "result.json"), "result_hash": result["result_hash"], "samples": {name: sample["passes_preregistered_feasibility_gate"] for name, sample in result["samples"].items()}}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-read-source", action="store_true")
    parser.add_argument("--reporting-correction", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.reporting_correction:
            print(json.dumps(reporting_correction(), ensure_ascii=False, sort_keys=True))
            return 0
        if not args.confirm_read_source:
            raise ValueError("small-account Top5 requires --confirm-read-source")
        print(json.dumps(run(), ensure_ascii=False, sort_keys=True))
        return 0
    except (ValueError, OSError, TypeError, IndexError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
