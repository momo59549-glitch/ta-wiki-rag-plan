"""One-off, pre-registered VectorBT coarse screen on a frozen 200-symbol sample.

This is deliberately a script, not a reusable discovery framework.  It is
non-adjudicable: approximate observed-calendar execution cannot establish A-share
tradability, and its survivors are not candidates, trial results, or lockbox data.
"""
from __future__ import annotations
import argparse, json, math, os
from datetime import date
from hashlib import sha256
from pathlib import Path
import numpy as np
import pandas as pd

from packages.research.gen3_local_market import make_local_market_contract
from packages.research.gen3_tradability_audit import load_frozen_mainboard_audit_state, _rows

DISCOVERY=(date(2015,1,1),date(2018,12,31)); CONFIRM=(date(2019,1,1),date(2021,12,31)); SAMPLE_N=200
# 2015-01-01 is a market holiday.  Completeness is checked at the first
# observable session in the frozen window, not by demanding a row on a closed
# calendar date.
SCREEN_FIRST_SESSION=date(2015,1,5); SCREEN_LAST_SESSION=date(2021,12,31)
INIT_CASH=100_000.; ORDER_VALUE=500.; BASE_FEE=.0003; BASE_SLIP=.0005
ROOT=Path("data"); MAINBOARD=ROOT/"gen3_mainboard_runs"/"mainboard-supplement-f355b85a02f1ca1ce32dcdd2ae6b901d7f685b778d82ab90798256468a08100d"; CONTENT=ROOT/"gen3_market_content_runs"/"market-content-9cacd85cb002278f03b15d4618875b09fc5be1b7ce61cc04e6ffa3a32ebdbf77"; QUALITY=ROOT/"gen3_quality_runs"/"quality-run-9e0b78370c8008a169977cacfe181434860addfd6a569eeaf3b7816636f987dd"

# This sorted tuple is the whole preregistered table.  Do not derive or tune it
# from outcomes.  Semantic hashes are emitted with every result.
CANDIDATES=tuple(sorted((
 ("sma_5_20","sma_cross",{"fast":5,"slow":20}), ("sma_10_30","sma_cross",{"fast":10,"slow":30}), ("sma_20_60","sma_cross",{"fast":20,"slow":60}), ("sma_30_120","sma_cross",{"fast":30,"slow":120}),
 ("ema_5_20","ema_cross",{"fast":5,"slow":20}), ("ema_10_30","ema_cross",{"fast":10,"slow":30}), ("ema_20_60","ema_cross",{"fast":20,"slow":60}), ("ema_30_120","ema_cross",{"fast":30,"slow":120}),
 ("rsi14_30_55","rsi",{"window":14,"entry":30,"exit":55}), ("rsi14_25_60","rsi",{"window":14,"entry":25,"exit":60}), ("rsi21_30_55","rsi",{"window":21,"entry":30,"exit":55}),
 ("mom20","momentum",{"window":20}), ("mom60","momentum",{"window":60}), ("mom120","momentum",{"window":120}),
 ("break20","breakout",{"window":20}), ("break55","breakout",{"window":55}),
 ("boll20_2","boll_reversion",{"window":20,"std":2.0}), ("boll55_2","boll_reversion",{"window":55,"std":2.0}),
 ("volprice20","volume_price",{"window":20,"mult":1.2}), ("volprice55","volume_price",{"window":55,"mult":1.2}),
),key=lambda x:x[0]))

def H(v):return "sha256:"+sha256(json.dumps(v,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
def contract(path):
 v=json.loads(Path(path).read_text(encoding="utf-8"));fields={"source_id","root","date_column","open_column","high_column","low_column","close_column","volume_column"}
 if not isinstance(v,dict) or set(v)!=fields:raise ValueError("trend contract strict schema")
 return make_local_market_contract(**v)
def state(c):return load_frozen_mainboard_audit_state(MAINBOARD,ROOT/"gen3_mainboard_runs",trend_content_run_dir=CONTENT,trend_content_allowed_output_root=ROOT/"gen3_market_content_runs",quality_run_dir=QUALITY,quality_allowed_output_root=ROOT/"gen3_quality_runs",trend_contract=c)
def candidate_table():
 if len({x[0] for x in CANDIDATES})!=len(CANDIDATES) or tuple(x[0] for x in CANDIDATES)!=tuple(sorted(x[0] for x in CANDIDATES)) or not 20<=len(CANDIDATES)<=30:raise ValueError("candidate preregistration invalid")
 return tuple({"semantic_id":i,"family":f,"parameters":p,"semantic_hash":H({"semantic_id":i,"family":f,"parameters":p})} for i,f,p in CANDIDATES)
def fixed_symbols(s):
 by={x.symbol:x for x in s.trend_entries};out=tuple(x for x in s.members if x in by and by[x].selected_rows and by[x].min_session<=SCREEN_FIRST_SESSION and by[x].max_session>=SCREEN_LAST_SESSION)[:SAMPLE_N]
 if len(out)!=SAMPLE_N:raise ValueError("frozen state lacks 200 full-window members")
 return out
def frames(s,symbols):
 fields={x:{} for x in ("open","high","low","close","volume","is_st")}
 for symbol in symbols:
  rows=[r for r in _rows(s,symbol)[0] if DISCOVERY[0]<=r.session<=CONFIRM[1]]
  if not rows:raise ValueError("fixed member lacks full screen range")
  for field in fields:fields[field][symbol]=pd.Series({pd.Timestamp(r.session):getattr(r,field) for r in rows})
 raw={field:pd.DataFrame(value).sort_index() for field,value in fields.items()}
 # Only close valuation is forward-filled.  Signal and execution gates remain
 # based on raw OHLCV observations below; a filled value is never a market row.
 valuation=raw["close"].ffill()
 if not valuation.index.is_unique or not valuation.index.is_monotonic_increasing or valuation.isna().any().any():raise ValueError("valuation wide frame invalid")
 eligible=raw["open"].notna() & raw["volume"].gt(0) & ~raw["is_st"].fillna(True) & ~(raw["open"].eq(raw["high"]) & raw["open"].eq(raw["low"]) & raw["open"].eq(raw["close"]))
 return raw,valuation,eligible
def signals(close,volume,definition):
 _,family,p=definition
 if family in {"sma_cross","ema_cross"}:
  a=(close.rolling(p["fast"],min_periods=p["fast"]).mean() if family=="sma_cross" else close.ewm(span=p["fast"],adjust=False,min_periods=p["fast"]).mean());b=(close.rolling(p["slow"],min_periods=p["slow"]).mean() if family=="sma_cross" else close.ewm(span=p["slow"],adjust=False,min_periods=p["slow"]).mean());return (a>b)&(a.shift(1)<=b.shift(1)),(a<b)&(a.shift(1)>=b.shift(1))
 if family=="rsi":
  d=close.diff();g=d.clip(lower=0).rolling(p["window"],min_periods=p["window"]).mean();l=(-d.clip(upper=0)).rolling(p["window"],min_periods=p["window"]).mean()
  # Wilder-style boundary semantics: an uninterrupted rise is RSI 100, while
  # a flat window is 50 rather than an accidental NaN that locks the signal.
  r=100-100/(1+g/l.replace(0,np.nan));r=r.mask((l==0)&(g>0),100.).mask((l==0)&(g==0),50.)
  return r<p["entry"],r>p["exit"]
 if family=="momentum":
  m=close.pct_change(p["window"],fill_method=None);return m>0,m<0
 if family=="breakout":
  high=close.rolling(p["window"],min_periods=p["window"]).max().shift(1);low=close.rolling(p["window"],min_periods=p["window"]).min().shift(1);return close>high,close<low
 if family=="boll_reversion":
  mid=close.rolling(p["window"],min_periods=p["window"]).mean();sd=close.rolling(p["window"],min_periods=p["window"]).std();return close<mid-p["std"]*sd,close>mid
 if family=="volume_price":
  avg=volume.rolling(p["window"],min_periods=p["window"]).mean();m=close.pct_change(fill_method=None);return (m>0)&(volume>avg*p["mult"]),(m<0)|(volume<avg)
 raise ValueError("unknown preregistered family")
def delay_exits(planned,eligible):
 out=pd.DataFrame(False,index=planned.index,columns=planned.columns)
 for col in planned:
  pending=False
  for i in planned.index:
   pending=pending or bool(planned.at[i,col])
   if pending and bool(eligible.at[i,col]):out.at[i,col]=True;pending=False
 return out
def delay_entries(planned,eligible):
 """Delay a precomputed close signal until the next eligible open."""
 return delay_exits(planned,eligible)
def execution(raw,valuation,eligible,definition,period):
 # The forward-filled valuation close is strictly a portfolio valuation input.
 # Indicators are generated from raw observations so a missing close cannot
 # fabricate a momentum, crossover, or rolling signal.
 entries,exits=signals(raw["close"],raw["volume"],definition); mask=pd.Series((entries.index.date>=period[0])&(entries.index.date<=period[1]),index=entries.index)
 # Signals are computed on the complete 2015--2021 history for indicator
 # warm-up.  Orders, however, are formed first and then cut to the requested
 # period, so a confirmation portfolio starts with fresh cash and never
 # inherits a discovery position.
 planned_entries=entries.fillna(False).shift(1,fill_value=False);planned_exits=exits.fillna(False).shift(1,fill_value=False)
 actual_entries=(planned_entries&eligible).where(mask,False); delayed=delay_exits(planned_exits.where(mask,False),eligible).where(mask,False);actual_entries=actual_entries&~delayed
 return actual_entries,delayed
def period_slice(*frames,period):
 index=frames[0].index;mask=(index.date>=period[0])&(index.date<=period[1])
 if not mask.any():raise ValueError("period has no observed sessions")
 return tuple(x.loc[mask].copy() for x in frames)
def portfolio(opens,valuation,entries,exits,fee,slip):
 import vectorbt as vbt
 price=valuation.copy();event=entries|exits;price[event]=opens[event]
 return vbt.Portfolio.from_signals(close=valuation,entries=entries,exits=exits,price=price,fees=fee,slippage=slip,direction="longonly",init_cash=INIT_CASH,size=ORDER_VALUE,size_type="value",cash_sharing=True,group_by=True,call_seq="auto",freq="1D")
def stats(p):return {"annualized_return":float(p.annualized_return(group_by=True,year_freq="252 days")),"total_return":float(p.total_return(group_by=True)),"max_drawdown":float(p.max_drawdown(group_by=True)),"trades":int(len(p.trades.records))}
def benchmark(raw,valuation,eligible,period,fee,slip):
 mask=pd.Series((valuation.index.date>=period[0])&(valuation.index.date<=period[1]),index=valuation.index);first=eligible.where(mask,False).apply(lambda x:x[x].index[0] if x.any() else pd.NaT);entries=pd.DataFrame(False,index=valuation.index,columns=valuation.columns)
 for col,stamp in first.items():
  if pd.notna(stamp):entries.at[stamp,col]=True
 # The initial equal-weight allocation is also a close signal.  It is sent at
 # the next eligible open, not silently discarded if that next session is
 # gated out.
 entries=delay_entries(entries.shift(1,fill_value=False).where(mask,False),eligible).where(mask,False)
 o,v,e,x=period_slice(raw["open"],valuation,entries,pd.DataFrame(False,index=entries.index,columns=entries.columns),period=period)
 return stats(portfolio(o,v,e,x,fee,slip))
def screen(s):
 symbols=fixed_symbols(s);raw,valuation,eligible=frames(s,symbols);table=candidate_table();periods=(("discovery",DISCOVERY),("confirmation",CONFIRM));bench={name:benchmark(raw,valuation,eligible,span,BASE_FEE,BASE_SLIP) for name,span in periods};result=[]
 for d in table:
  definition=next(x for x in CANDIDATES if x[0]==d["semantic_id"]);period_rows=[]
  for name,span in periods:
   e,x=execution(raw,valuation,eligible,definition,span);o,v,e,x=period_slice(raw["open"],valuation,e,x,period=span);base=stats(portfolio(o,v,e,x,BASE_FEE,BASE_SLIP));stress=stats(portfolio(o,v,e,x,BASE_FEE*2,BASE_SLIP*2));base["benchmark_total_return"]=bench[name]["total_return"];base["excess_total_return"]=base["total_return"]-bench[name]["total_return"];period_rows.append({"period":name,"base":base,"stress_2x":stress})
  gates=[r["base"]["annualized_return"]>.08 and r["base"]["excess_total_return"]>.03 and r["base"]["max_drawdown"]>-.30 and r["stress_2x"]["annualized_return"]>0 and r["base"]["trades"]>=30 for r in period_rows];result.append({**d,"periods":period_rows,"survives_all_preregistered_gates":all(gates)})
 return symbols,len(valuation),bench,result
def write_once(path,value):
 tmp=path.with_name(path.name+".tmp")
 if path.exists() or tmp.exists():raise ValueError("screen artifact already exists")
 try:
  with open(tmp,"xb") as f:f.write(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode());f.flush();os.fsync(f.fileno())
  os.link(tmp,path)
 finally:
  if tmp.exists():tmp.unlink()
def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument("--confirm-read-source",action="store_true");p.add_argument("--trend-contract-json",default="configs/gen3_trend_cache_quality.json");p.add_argument("--output-root",default="data/vectorbt_screens")
 try:
  a=p.parse_args(argv)
  if not a.confirm_read_source:raise ValueError("screen requires --confirm-read-source")
  s=state(contract(a.trend_contract_json));symbols,sessions,bench,rows=screen(s);payload={"schema_version":"vectorbt-screen/v3","universe_hash":s.universe_hash,"mainboard_policy_hash":s.policy.policy_hash,"sample_rule":"ordered first 200 frozen trend entries spanning 2015-01-01..2021-12-31","symbols":list(symbols),"valuation_common_sessions":sessions,"periods":{"discovery":[str(x) for x in DISCOVERY],"confirmation":[str(x) for x in CONFIRM]},"period_portfolios_are_isolated":True,"period_end_positions_mark_to_market":True,"future_exit_cost_not_charged":True,"annualization":"vectorbt_year_freq_252_trading_days","observed_calendar_approximation":True,"fees":BASE_FEE,"slippage":BASE_SLIP,"stress_multiplier":2,"initial_cash":INIT_CASH,"order_value":ORDER_VALUE,"benchmark_kind":"fixed_sample_equal_weight_approximation","benchmarks":bench,"candidates":rows,"survivors":sum(x["survives_all_preregistered_gates"] for x in rows),"nonadjudicable":True,"official_tradability_verified":False,"no_trial_budget":True,"no_lockbox":True,"notice":"No candidate is promoted; valuation close may be forward-filled only for VectorBT valuation, never for signals or execution."};payload["screen_hash"]=H(payload);out=Path(a.output_root).resolve();out.mkdir(parents=True,exist_ok=True);path=out/("vectorbt-screen-"+payload["screen_hash"].split(":")[1]+".json");write_once(path,payload);print(json.dumps({"status":"completed_nonadjudicable_screen","artifact":str(path),"screen_hash":payload["screen_hash"],"candidates":len(rows),"survivors":payload["survivors"]},ensure_ascii=False));return 0
 except (ValueError,OSError,TypeError,json.JSONDecodeError) as e:print(json.dumps({"status":"blocked","error":str(e)},ensure_ascii=False));return 2
if __name__=="__main__":raise SystemExit(main())
