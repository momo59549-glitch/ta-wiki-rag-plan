"""One-shot, read-only recovery of exact v2 historical-coverage gaps.

This intentionally is not a general data-composition layer.  It may inspect
only the symbol-years already inadequate in the immutable v2 audit, and may
emit rows only for their exact missing observed-calendar sessions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import pyarrow.dataset as ds

try:
    from scripts.run_survivorship_coverage_audit import (
        ADEQUATE_RATIO,
        MAX_INTERNAL_MISSING,
        MAX_ZERO_COVERAGE_RATIO,
        MIN_NONACTIVE_ADEQUATE_RATIO,
        MIN_YEAR_ADEQUATE_RATIO,
        RetrospectiveMember,
        classify_member_year,
        retrospective_members,
    )
    from scripts.run_vectorbt_candidate_screen import contract, state
except ModuleNotFoundError:
    from run_survivorship_coverage_audit import ADEQUATE_RATIO, MAX_INTERNAL_MISSING, MAX_ZERO_COVERAGE_RATIO, MIN_NONACTIVE_ADEQUATE_RATIO, MIN_YEAR_ADEQUATE_RATIO, RetrospectiveMember, classify_member_year, retrospective_members
    from run_vectorbt_candidate_screen import contract, state


ROOT = Path("data")
V2 = ROOT / "qlib_spikes" / "survivorship-coverage-audit-2019-2021-v2.json"
OBSERVED_CALENDAR = ROOT / "gen3_tradability_audits" / "tradability-audit-69c14830116a17d250f3100754e7ac670293019242266e4851caafab02870a75" / "observed_calendar.json"
OUTPUT = ROOT / "survivorship_gap_overlays" / "gap-recovery-overlay-v2.json"
WINDOW = (date(2019, 1, 1), date(2021, 12, 31))
TOLERANCE = 1e-6
MIN_OVERLAP = 20
CANDIDATE_ROOTS = (
    ("local_cache", Path(r"H:\股票模型\Model\data\local_cache"), "date"),
    ("tushare_incremental_cache", Path(r"H:\股票模型\Model\data\tushare_incremental_cache"), "trade_date"),
    ("tushare_daily_cache", Path(r"H:\股票模型\Model\data\tushare_daily_cache"), "trade_date"),
)


def H(value: object) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            raise ValueError("aware market session rejected")
        value = value.date()
    if type(value) is not date:
        raise ValueError("market session must be date")
    return value


def _number(value: object, label: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    value = float(value)
    if not math.isfinite(value) or value < 0 if nonnegative else not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label} invalid")
    return value


def read_rows(path: Path, date_column: str) -> dict[date, tuple[float, float, float, float, float]]:
    """Read only 2019--2021 OHLCV, with duplicate/date/OHLC fail-closed checks."""
    schema = ds.dataset(path, format="parquet").schema
    expected = {date_column, "open", "high", "low", "close", "volume"}
    if not expected.issubset(set(schema.names)):
        raise ValueError("candidate schema lacks required OHLCV")
    table = ds.dataset(path, format="parquet").to_table(
        columns=[date_column, "open", "high", "low", "close", "volume"],
        filter=(ds.field(date_column) >= WINDOW[0]) & (ds.field(date_column) <= WINDOW[1]),
    )
    payload = table.to_pydict()
    rows: dict[date, tuple[float, float, float, float, float]] = {}
    for index, raw_session in enumerate(payload[date_column]):
        session = _as_date(raw_session)
        raw_values = tuple(payload[key][index] for key in ("open", "high", "low", "close", "volume"))
        # Some local cache files materialize an all-null row after delisting.
        # It is an absent observation, never an overlay candidate or overlap.
        if all(value is None or (isinstance(value, float) and math.isnan(value)) for value in raw_values):
            continue
        values = tuple(_number(value, key, nonnegative=(key == "volume")) for key, value in zip(("open", "high", "low", "close", "volume"), raw_values))
        open_, high, low, close, _ = values
        if high < max(open_, low, close) or low > min(open_, high, close):
            raise ValueError("OHLC bounds invalid")
        if session in rows:
            raise ValueError("duplicate session")
        rows[session] = values
    return rows


def compatible_price_basis(trend: dict[date, tuple[float, float, float, float, float]], candidate: dict[date, tuple[float, float, float, float, float]]) -> dict:
    """Accept only a time-invariant OHLC scale against adjusted trend prices."""
    overlap = sorted(set(trend) & set(candidate))
    if len(overlap) < MIN_OVERLAP:
        return {"accepted": False, "reason": "insufficient_overlap", "overlap_sessions": len(overlap)}
    close_ratios = [candidate[stamp][3] / trend[stamp][3] for stamp in overlap]
    median = sorted(close_ratios)[len(close_ratios) // 2]
    close_dispersion = max(abs(value / median - 1.0) for value in close_ratios)
    ohlc_dispersion = max(
        abs((candidate[stamp][field] / trend[stamp][field]) / (candidate[stamp][3] / trend[stamp][3]) - 1.0)
        for stamp in overlap
        for field in range(4)
    )
    accepted = close_dispersion <= TOLERANCE and ohlc_dispersion <= TOLERANCE
    return {
        "accepted": accepted,
        "reason": "accepted_constant_ohlc_scale" if accepted else "incompatible_adjustment_basis",
        "overlap_sessions": len(overlap),
        "close_ratio_median": median,
        "close_ratio_relative_dispersion": close_dispersion,
        "ohlc_relative_dispersion": ohlc_dispersion,
        "tolerance": TOLERANCE,
    }


def _strict_v2() -> dict:
    value = json.loads(V2.read_text(encoding="utf-8"))
    claimed = value.pop("audit_hash", None)
    if claimed != H(value) or value.get("schema_version") != "survivorship-coverage-audit/v2":
        raise ValueError("v2 audit hash/schema invalid")
    if value.get("status") != "blocked_insufficient_historical_coverage":
        raise ValueError("v2 audit status is not eligible for targeted recovery")
    return value | {"audit_hash": claimed}


def _calendar() -> dict[int, set[date]]:
    value = json.loads(OBSERVED_CALENDAR.read_text(encoding="utf-8"))
    if value.get("calendar_kind") != "observed_session_calendar_approximation" or not isinstance(value.get("sessions"), list):
        raise ValueError("frozen observed calendar invalid")
    sessions = {_as_date(item) if not isinstance(item, str) else date.fromisoformat(item) for item in value["sessions"]}
    return {year: {stamp for stamp in sessions if stamp.year == year} for year in (2019, 2020, 2021)}


def _trend_paths() -> dict[str, Path]:
    frozen = state(contract("configs/gen3_trend_cache_quality.json"))
    corpus_paths = {H(item.payload()): Path(item.file_path) for item in frozen.trend_snapshot.files}
    result = {}
    for entry in frozen.trend_entries:
        path = corpus_paths.get(entry.corpus_entry_hash)
        if path is None or entry.symbol in result:
            raise ValueError("frozen trend attribution invalid")
        result[entry.symbol] = path
    return result


def _targets(v2: dict, members: dict[str, RetrospectiveMember], calendars: dict[int, set[date]], trend_rows: dict[str, dict[date, tuple[float, float, float, float, float]]]) -> dict[tuple[str, int], set[date]]:
    targets: dict[tuple[str, int], set[date]] = {}
    for text_year, report in v2["years"].items():
        year = int(text_year)
        for item in report["inadequate_or_zero_members"]:
            symbol = item["symbol"]
            member = members.get(symbol)
            if member is None:
                raise ValueError("v2 target lacks manifest member")
            interval = member.interval_for_year(year)
            expected = {stamp for stamp in calendars[year] if interval is not None and interval[0] <= stamp <= interval[1]}
            missing = expected - set(trend_rows.get(symbol, {}))
            if missing:
                targets[(symbol, year)] = missing
    return targets


def _post_v3(v2: dict, members: dict[str, RetrospectiveMember], calendars: dict[int, set[date]], trend_rows: dict[str, dict], overlay: dict[str, dict]) -> tuple[dict, dict[str, bool]]:
    reports: dict[str, dict] = {}
    passes: dict[str, bool] = {}
    for text_year, base in v2["years"].items():
        year = int(text_year)
        result = {key: value for key, value in base.items() if key not in {"inadequate_or_zero_members", "structural_partial_due_listing_or_delisting", "unexplained_internal_gaps"}}
        remaining = []
        adequate_delta = active_delta = nonactive_delta = zero_delta = 0
        for old in base["inadequate_or_zero_members"]:
            symbol = old["symbol"]
            member = members[symbol]
            observed = set(trend_rows.get(symbol, {})) | {date.fromisoformat(row["session"]) for row in overlay.get(symbol, {}).values()}
            new = classify_member_year(member, year, {stamp for stamp in observed if stamp.year == year}, calendars[year])
            if new["adequate"] and not old["adequate"]:
                adequate_delta += 1
                if member.active_to is None:
                    active_delta += 1
                else:
                    nonactive_delta += 1
            if old["category"] == "zero_coverage" and new["category"] != "zero_coverage":
                zero_delta -= 1
            if not new["adequate"]:
                remaining.append(new)
        result["adequate_members"] += adequate_delta
        result["active_adequate_members"] += active_delta
        result["nonactive_adequate_members"] += nonactive_delta
        result["zero_coverage_members"] += zero_delta
        result["adequate_ratio"] = result["adequate_members"] / result["historical_members"]
        result["nonactive_adequate_ratio"] = result["nonactive_adequate_members"] / result["nonactive_or_delisted_members"] if result["nonactive_or_delisted_members"] else 1.0
        result["zero_coverage_ratio"] = result["zero_coverage_members"] / result["historical_members"]
        result["remaining_inadequate_or_zero_members"] = remaining
        reports[text_year] = result
        passes[text_year] = result["adequate_ratio"] >= MIN_YEAR_ADEQUATE_RATIO and result["nonactive_adequate_ratio"] >= MIN_NONACTIVE_ADEQUATE_RATIO and result["zero_coverage_ratio"] <= MAX_ZERO_COVERAGE_RATIO
    return reports, passes


def recover() -> dict:
    v2 = _strict_v2()
    members = {item.symbol: item for item in retrospective_members(ROOT / "universes" / "a_share_history.jsonl")}
    calendars = _calendar()
    trend_paths = _trend_paths()
    target_symbols = {item["symbol"] for report in v2["years"].values() for item in report["inadequate_or_zero_members"]}
    trend_rows = {symbol: read_rows(path, "date") for symbol, path in trend_paths.items() if symbol in target_symbols}
    targets = _targets(v2, members, calendars, trend_rows)
    overlay: dict[str, dict] = {}
    source_reports: list[dict] = []
    for symbol in sorted({symbol for symbol, _ in targets}):
        needed = set().union(*(sessions for (item, _), sessions in targets.items() if item == symbol))
        base = trend_rows.get(symbol, {})
        selected = False
        for source_id, root, date_column in CANDIDATE_ROOTS:
            path = root / f"{symbol}.parquet"
            if not path.is_file():
                source_reports.append({"symbol": symbol, "source_id": source_id, "status": "not_present"})
                continue
            try:
                candidate = read_rows(path, date_column)
                compatibility = compatible_price_basis(base, candidate)
            except (OSError, ValueError, TypeError) as exc:
                source_reports.append({"symbol": symbol, "source_id": source_id, "status": "rejected", "reason": str(exc)})
                continue
            source_reports.append({"symbol": symbol, "source_id": source_id, "path": str(path.resolve()), "source_content_hash": _sha256_file(path), **compatibility})
            if not compatibility["accepted"]:
                continue
            for stamp in sorted(needed & set(candidate)):
                if stamp in base:
                    raise ValueError("overlay may not duplicate trend session")
                values = candidate[stamp]
                row = {"symbol": symbol, "session": stamp.isoformat(), "open": values[0], "high": values[1], "low": values[2], "close": values[3], "volume": values[4], "source_id": source_id, "source_path": str(path.resolve()), "source_content_hash": source_reports[-1]["source_content_hash"]}
                row["row_hash"] = H(row)
                overlay[f"{symbol}:{stamp.isoformat()}"] = row
            selected = True
            break
        if not selected:
            source_reports.append({"symbol": symbol, "status": "no_compatible_local_source"})
    reports, threshold_passes = _post_v3(v2, members, calendars, trend_rows, overlay)
    result = {
        "schema_version": "survivorship-gap-recovery/v1",
        "status": "coverage_sufficient_for_next_review" if all(threshold_passes.values()) else "blocked_insufficient_historical_coverage_after_local_recovery",
        "v2_audit_hash": v2["audit_hash"],
        "frozen_price_basis_rule": {"minimum_overlap_sessions": MIN_OVERLAP, "constant_close_ratio_relative_dispersion_at_most": TOLERANCE, "per_date_ohlc_ratio_relative_to_close_at_most": TOLERANCE, "reject_time_varying_scale": True},
        "observed_session_calendar_approximation": True,
        "target_scope": "only v2 inadequate_or_zero symbol-years and their exact expected missing sessions",
        "overlay_rows": [overlay[key] for key in sorted(overlay)],
        "source_compatibility_reports": source_reports,
        "post_overlay_v3_years": reports,
        "threshold_passes": threshold_passes,
        "no_source_mutation": True,
        "no_network": True,
        "no_2022_plus_market_rows": True,
        "non_pit": True,
        "no_model_fit": True,
        "no_backtest": True,
    }
    result["overlay_hash"] = H(result)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-read-source", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not args.confirm_read_source:
            raise ValueError("gap recovery requires --confirm-read-source")
        if OUTPUT.exists():
            raise ValueError("gap recovery output already exists")
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        value = recover()
        OUTPUT.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False), encoding="utf-8")
        print(json.dumps({"status": value["status"], "artifact": str(OUTPUT), "overlay_rows": len(value["overlay_rows"]), "overlay_hash": value["overlay_hash"]}, ensure_ascii=False, sort_keys=True))
        return 0
    except (ValueError, OSError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
