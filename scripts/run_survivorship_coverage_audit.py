"""Bounded v2 retrospective listing-date coverage audit.

This is deliberately not a PIT universe reconstruction, model fit, Qlib run,
or portfolio calculation.  It reads only 2019--2021 market sessions and treats
the current stock-basic manifest as retrospective listing-date evidence only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pyarrow.dataset as ds

try:
    from scripts.run_vectorbt_candidate_screen import contract, fixed_symbols, state
except ModuleNotFoundError:
    from run_vectorbt_candidate_screen import contract, fixed_symbols, state

from packages.research.gen3_mainboard_universe import PREFIXES, MainboardMember


YEARS = (2019, 2020, 2021)
WINDOW = (date(2019, 1, 1), date(2021, 12, 31))
ADEQUATE_RATIO = 0.99
MAX_INTERNAL_MISSING = 3
MIN_YEAR_ADEQUATE_RATIO = 0.95
MIN_NONACTIVE_ADEQUATE_RATIO = 0.90
MAX_ZERO_COVERAGE_RATIO = 0.005
ROOT = Path("data")
MANIFEST = ROOT / "universes" / "a_share_history.jsonl"
OUTPUT = ROOT / "qlib_spikes" / "survivorship-coverage-audit-2019-2021-v2.json"


def H(value: object) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _corpus_entry_path_map(snapshot) -> dict[str, Path]:
    """Rebind ContentFileEntry hashes to the strict snapshot's actual paths."""
    mapping: dict[str, Path] = {}
    for entry in snapshot.files:
        digest = H(entry.payload())
        if digest in mapping:
            raise ValueError("snapshot corpus identity duplicate")
        mapping[digest] = Path(entry.file_path)
    return mapping


@dataclass(frozen=True)
class RetrospectiveMember:
    symbol: str
    active_from: date
    active_to: date | None
    list_status: str

    def interval_for_year(self, year: int) -> tuple[date, date] | None:
        start, end = max(self.active_from, date(year, 1, 1)), min(self.active_to or date(year, 12, 31), date(year, 12, 31))
        return (start, end) if start <= end else None


def _date(value: object, label: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"manifest {label} must be ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"manifest {label} must be ISO date") from exc


def retrospective_members(path: Path) -> tuple[RetrospectiveMember, ...]:
    """Parse the actual 10-field manifest and retain only true SH/SZ mainboard."""
    raw = path.read_bytes()
    required = {"source", "symbol", "ts_code", "name", "exchange", "market", "list_status", "active_from", "active_to", "fetched_at"}
    out: list[RetrospectiveMember] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        item = json.loads(line.decode("utf-8"))
        if not isinstance(item, dict) or set(item) != required or item["source"] != "tushare.stock_basic":
            raise ValueError("manifest strict schema/source invalid")
        active_to = None if item["active_to"] is None else _date(item["active_to"], "active_to")
        try:
            parsed_at = datetime.fromisoformat(item["fetched_at"].replace("Z", "+00:00")) if isinstance(item["fetched_at"], str) else None
        except ValueError as exc:
            raise ValueError("manifest fetched_at invalid") from exc
        member = MainboardMember(item["symbol"], item["ts_code"], item["name"], item["exchange"], item["market"], item["list_status"], _date(item["active_from"], "active_from"), active_to, parsed_at)
        member.verify()
        if member.symbol in seen:
            raise ValueError("manifest duplicate symbol")
        seen.add(member.symbol)
        if member.symbol[:3] in PREFIXES:
            if member.market != "主板":
                raise ValueError("mainboard-looking prefix has non-mainboard market")
            out.append(RetrospectiveMember(member.symbol, member.active_from, member.active_to, member.list_status))
    if not out:
        raise ValueError("manifest has no mainboard records")
    return tuple(sorted(out, key=lambda item: item.symbol))


def _session_gaps(expected: tuple[date, ...], observed: set[date]) -> tuple[int, int, int]:
    """Return first-edge, last-edge, and internal missing session counts."""
    positions = [index for index, stamp in enumerate(expected) if stamp in observed]
    if not positions:
        return len(expected), 0, 0
    first, last = positions[0], positions[-1]
    return first, len(expected) - last - 1, sum(stamp not in observed for stamp in expected[first : last + 1])


def classify_member_year(member: RetrospectiveMember, year: int, observed: set[date], calendar: set[date]) -> dict:
    """Classify against the member's legal listing/delisting interval, not year-wide coverage."""
    interval = member.interval_for_year(year)
    if interval is None:
        raise ValueError("member is not historical in requested year")
    start, end = interval
    expected = tuple(sorted(stamp for stamp in calendar if start <= stamp <= end))
    inside = {stamp for stamp in observed if start <= stamp <= end}
    if any(not (date(year, 1, 1) <= stamp <= date(year, 12, 31)) for stamp in observed):
        raise ValueError("observed sessions escape year")
    observed_count = len(inside & set(expected))
    expected_count = len(expected)
    ratio = observed_count / expected_count if expected_count else 1.0
    first_missing, last_missing, internal_missing = _session_gaps(expected, inside)
    structural = start > date(year, 1, 1) or end < date(year, 12, 31)
    zero = expected_count > 0 and observed_count == 0
    adequate = (not zero and ratio >= ADEQUATE_RATIO and internal_missing <= MAX_INTERNAL_MISSING)
    if zero:
        category = "zero_coverage"
    elif structural and observed_count == expected_count:
        category = "structural_partial_due_listing_or_delisting"
    elif internal_missing:
        category = "unexplained_internal_gaps"
    elif first_missing or last_missing:
        category = "unexplained_edge_gaps"
    else:
        category = "full_year_coverage"
    return {
        "symbol": member.symbol,
        "interval_start": start.isoformat(),
        "interval_end": end.isoformat(),
        "expected_sessions": expected_count,
        "observed_sessions": observed_count,
        "observed_expected_ratio": ratio,
        "first_gap_sessions": first_missing,
        "last_gap_sessions": last_missing,
        "internal_missing_sessions": internal_missing,
        "structural_partial": structural,
        "adequate": adequate,
        "category": category,
    }


def classify_year(members: tuple[RetrospectiveMember, ...], year: int, observed: dict[str, set[date]], calendar: set[date]) -> dict:
    rows = [classify_member_year(member, year, observed.get(member.symbol, set()), calendar) for member in members if member.interval_for_year(year) is not None]
    active = [row for row, member in zip(rows, (item for item in members if item.interval_for_year(year) is not None)) if member.active_to is None]
    nonactive = [row for row, member in zip(rows, (item for item in members if item.interval_for_year(year) is not None)) if member.active_to is not None]
    if len(rows) != len(active) + len(nonactive):
        raise ValueError("active classification invariant invalid")
    adequate = [row for row in rows if row["adequate"]]
    zero = [row for row in rows if row["category"] == "zero_coverage"]
    insufficient = [row for row in rows if not row["adequate"]]
    structural = [row for row in rows if row["category"] == "structural_partial_due_listing_or_delisting"]
    internal = [row for row in rows if row["category"] == "unexplained_internal_gaps"]
    total = len(rows)
    nonactive_adequate_ratio = sum(row["adequate"] for row in nonactive) / len(nonactive) if nonactive else 1.0
    return {
        "historical_members": total,
        "observed_calendar_sessions": len(calendar),
        "adequate_members": len(adequate),
        "adequate_ratio": len(adequate) / total if total else 0.0,
        "active_members": len(active),
        "active_adequate_members": sum(row["adequate"] for row in active),
        "nonactive_or_delisted_members": len(nonactive),
        "nonactive_adequate_members": sum(row["adequate"] for row in nonactive),
        "nonactive_adequate_ratio": nonactive_adequate_ratio,
        "zero_coverage_members": len(zero),
        "zero_coverage_ratio": len(zero) / total if total else 0.0,
        "structural_partial_due_listing_or_delisting": structural,
        "unexplained_internal_gaps": internal,
        "inadequate_or_zero_members": insufficient,
    }


def _sessions_before_2022(path: Path, date_column: str) -> set[date]:
    """Predicate restricts scanner reads to the requested 2019--2021 rows."""
    table = ds.dataset(path, format="parquet").to_table(
        columns=[date_column],
        filter=(ds.field(date_column) >= WINDOW[0]) & (ds.field(date_column) <= WINDOW[1]),
    )
    values: set[date] = set()
    for value in table.column(date_column).to_pylist():
        if isinstance(value, datetime):
            if value.tzinfo is not None:
                raise ValueError("market session must be naive/date")
            value = value.date()
        if type(value) is not date or not (WINDOW[0] <= value <= WINDOW[1]):
            raise ValueError("market session predicate invariant invalid")
        values.add(value)
    return values


def audit() -> dict:
    frozen = state(contract("configs/gen3_trend_cache_quality.json"))
    members = retrospective_members(MANIFEST)
    corpus_paths = _corpus_entry_path_map(frozen.trend_snapshot)
    attribution: dict[str, Path] = {}
    for entry in frozen.trend_entries:
        path = corpus_paths.get(entry.corpus_entry_hash)
        if path is None or entry.symbol in attribution:
            raise ValueError("frozen trend content attribution invalid")
        attribution[entry.symbol] = path
    member_symbols = {member.symbol for member in members if any(member.interval_for_year(year) for year in YEARS)}
    observed = {symbol: _sessions_before_2022(path, "date") for symbol, path in attribution.items() if symbol in member_symbols}
    reports: dict[str, dict] = {}
    year_members: dict[int, tuple[RetrospectiveMember, ...]] = {}
    for year in YEARS:
        members_for_year = tuple(member for member in members if member.interval_for_year(year) is not None)
        year_members[year] = members_for_year
        calendar = {stamp for symbol in {member.symbol for member in members_for_year} for stamp in observed.get(symbol, set()) if stamp.year == year}
        reports[str(year)] = classify_year(members_for_year, year, {symbol: {stamp for stamp in values if stamp.year == year} for symbol, values in observed.items()}, calendar)
    original = set(fixed_symbols(frozen))
    holdout_result = json.loads((ROOT / "qlib_spikes" / "cross-sectional-holdout-fixed-next200-2019-2021-v4" / "result.json").read_text(encoding="utf-8"))
    holdout = set(holdout_result["selection"]["holdout_symbols"])
    threshold_passes = {
        str(year): (
            report["adequate_ratio"] >= MIN_YEAR_ADEQUATE_RATIO
            and report["nonactive_adequate_ratio"] >= MIN_NONACTIVE_ADEQUATE_RATIO
            and report["zero_coverage_ratio"] <= MAX_ZERO_COVERAGE_RATIO
        )
        for year, report in ((year, reports[str(year)]) for year in YEARS)
    }
    result = {
        "schema_version": "survivorship-coverage-audit/v2",
        "status": "coverage_sufficient_for_next_review" if all(threshold_passes.values()) else "blocked_insufficient_historical_coverage",
        "kind": "retrospective_listing_date_reconstruction_not_PIT",
        "manifest_path": str(MANIFEST.resolve()),
        "manifest_content_hash": "sha256:" + hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
        "thresholds_pre_registered": {
            "adequate_observed_expected_ratio_at_least": ADEQUATE_RATIO,
            "adequate_internal_missing_sessions_at_most": MAX_INTERNAL_MISSING,
            "year_adequate_ratio_at_least": MIN_YEAR_ADEQUATE_RATIO,
            "nonactive_adequate_ratio_at_least": MIN_NONACTIVE_ADEQUATE_RATIO,
            "zero_coverage_ratio_at_most": MAX_ZERO_COVERAGE_RATIO,
        },
        "years": reports,
        "threshold_passes": threshold_passes,
        "original200_survivor_membership": {str(year): len(original & {member.symbol for member in year_members[year] if member.active_to is None}) for year in YEARS},
        "holdout200_survivor_membership": {str(year): len(holdout & {member.symbol for member in year_members[year] if member.active_to is None}) for year in YEARS},
        "source_attribution_counts": {"trend": len(attribution), "supplement": 0},
        "observed_session_calendar_approximation": True,
        "no_2022_plus_market_rows": True,
        "no_model_fit": True,
        "no_backtest": True,
        "non_pit": True,
        "limitation": "Observed-session union is a local approximation, not an official trading calendar. Manifest active dates are current retrospective revision data, not PIT membership evidence.",
    }
    result["audit_hash"] = H(result)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-read-source", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not args.confirm_read_source:
            raise ValueError("coverage audit requires --confirm-read-source")
        if OUTPUT.exists():
            raise ValueError("coverage audit output already exists")
        result = audit()
        OUTPUT.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False), encoding="utf-8")
        print(json.dumps({"status": result["status"], "artifact": str(OUTPUT), "audit_hash": result["audit_hash"]}, ensure_ascii=False, sort_keys=True))
        return 0
    except (ValueError, OSError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
