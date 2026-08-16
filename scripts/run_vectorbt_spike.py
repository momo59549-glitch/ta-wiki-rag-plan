"""Run one fixed, non-adjudicable VectorBT integration spike.

It intentionally has no parameter search, candidate registration, lockbox, or
trial-budget integration.  All paths below are merely identity witnesses for
the strict frozen mainboard loader; no manual universe is accepted.
"""
from __future__ import annotations
import argparse, json, os
from hashlib import sha256
from pathlib import Path
import pandas as pd

from packages.research.gen3_local_market import make_local_market_contract
from packages.research.gen3_tradability_audit import load_frozen_mainboard_audit_state, _rows
from packages.research.vectorbt_adapter import SPIKE_END, SPIKE_START, SPIKE_SYMBOL_COUNT, SPIKE_INITIAL_CASH, SPIKE_ORDER_VALUE, run_fixed_wide_spike

ROOT=Path("data")
MAINBOARD_RUN=ROOT/"gen3_mainboard_runs"/"mainboard-supplement-f355b85a02f1ca1ce32dcdd2ae6b901d7f685b778d82ab90798256468a08100d"
TREND_RUN=ROOT/"gen3_market_content_runs"/"market-content-9cacd85cb002278f03b15d4618875b09fc5be1b7ce61cc04e6ffa3a32ebdbf77"
QUALITY_RUN=ROOT/"gen3_quality_runs"/"quality-run-9e0b78370c8008a169977cacfe181434860addfd6a569eeaf3b7816636f987dd"

def _hash(value):
    return "sha256:"+sha256(json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode("utf-8")).hexdigest()

def _contract(path):
    value=json.loads(Path(path).read_text(encoding="utf-8")); fields={"source_id","root","date_column","open_column","high_column","low_column","close_column","volume_column"}
    if not isinstance(value,dict) or set(value)!=fields: raise ValueError("trend contract strict schema")
    return make_local_market_contract(**value)

def _state(contract):
    return load_frozen_mainboard_audit_state(MAINBOARD_RUN,ROOT/"gen3_mainboard_runs",trend_content_run_dir=TREND_RUN,trend_content_allowed_output_root=ROOT/"gen3_market_content_runs",quality_run_dir=QUALITY_RUN,quality_allowed_output_root=ROOT/"gen3_quality_runs",trend_contract=contract)

def _wide_fixed_sample(state):
    # Deterministic availability rule, frozen before any strategy result: take
    # the first 20 ordered mainboard members whose already-verified trend
    # content entry spans the complete fixed window.  It never examines return
    # outcomes, and excludes late supplement listings by construction.
    trend={x.symbol:x for x in state.trend_entries}
    symbols=tuple(symbol for symbol in state.members if symbol in trend and trend[symbol].selected_rows and trend[symbol].min_session<=SPIKE_START and trend[symbol].max_session>=SPIKE_END)[:SPIKE_SYMBOL_COUNT]
    if len(symbols)!=SPIKE_SYMBOL_COUNT: raise ValueError("frozen content lacks 20 full-window fixed sample members")
    fields={"open":{},"high":{},"low":{},"close":{},"volume":{}}
    for symbol in symbols:
        rows,_,_=_rows(state,symbol)
        selected=[r for r in rows if SPIKE_START<=r.session<=SPIKE_END]
        if not selected: raise ValueError("fixed sample lacks requested window")
        for field in fields: fields[field][symbol]=pd.Series({pd.Timestamp(r.session):getattr(r,field) for r in selected})
    frames={field:pd.DataFrame(by_symbol).sort_index() for field,by_symbol in fields.items()}
    # Fixed sample identity is the first 20 frozen symbols; the shared calendar
    # is only a data-shape conversion and never an exchange calendar claim.
    common=frames["close"].dropna().index
    for field in fields:
        frames[field]=frames[field].reindex(common)
        if frames[field].isna().any().any() or not frames[field].gt(0).all().all(): raise ValueError("spike wide frame is incomplete")
    if len(common)<60: raise ValueError("fixed sample has too few common sessions")
    return symbols,frames

def _write_once(path,value):
    path=Path(path);tmp=path.with_name(path.name+".tmp")
    if path.exists() or tmp.exists(): raise ValueError("spike artifact already exists")
    try:
        with open(tmp,"xb") as f:
            f.write(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode("utf-8"));f.flush();os.fsync(f.fileno())
        os.link(tmp,path)
    finally:
        if tmp.exists(): tmp.unlink()

def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument("--confirm-read-source",action="store_true");p.add_argument("--trend-contract-json",default="configs/gen3_trend_cache_quality.json");p.add_argument("--output-root",default="data/vectorbt_spikes")
    try:
        a=p.parse_args(argv)
        if not a.confirm_read_source: raise ValueError("spike requires --confirm-read-source")
        state=_state(_contract(a.trend_contract_json)); symbols,frames=_wide_fixed_sample(state)
        metrics=run_fixed_wide_spike(opens=frames["open"],closes=frames["close"])
        payload={"schema_version":"vectorbt-spike/v2","universe_hash":state.universe_hash,"mainboard_policy_hash":state.policy.policy_hash,"research_start":SPIKE_START.isoformat(),"research_end":SPIKE_END.isoformat(),"symbols":list(symbols),"shared_common_sessions":len(frames["close"]),"fees":0.0003,"slippage":0.0005,"initial_cash":SPIKE_INITIAL_CASH,"order_value":SPIKE_ORDER_VALUE,"size_type":"value","cash_sharing":True,"call_seq":"auto","execution_rule":"signal_at_T_close_to_T_plus_1_open_for_entries_and_exits","strategies":[m.__dict__ for m in metrics],"approximate_tradability":True,"official_tradability_verified":False,"nonadjudicable":True,"no_trial_budget":True,"no_lockbox":True,"notice":"VectorBT does not natively adjudicate A-share halts or price limits; any future exploratory use must apply the separate conservative filter before execution."}
        payload["spike_hash"]=_hash(payload);out=Path(a.output_root).resolve();out.mkdir(parents=True,exist_ok=True);path=out/("vectorbt-spike-"+payload["spike_hash"].split(":",1)[1]+".json");_write_once(path,payload)
        print(json.dumps({"status":"completed_nonadjudicable_spike","artifact":str(path),"spike_hash":payload["spike_hash"],"strategies":[m.strategy for m in metrics]},ensure_ascii=False));return 0
    except (ValueError,OSError,TypeError,json.JSONDecodeError) as exc:
        print(json.dumps({"status":"blocked","error":str(exc)},ensure_ascii=False));return 2
if __name__=="__main__":raise SystemExit(main())
