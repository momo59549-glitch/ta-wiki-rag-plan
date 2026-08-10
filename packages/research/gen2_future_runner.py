"""Append-only runner for a preregistered Gen2 future validation window.

This is deliberately a library entry point.  Providers are injected by the
application and are bound to a concrete, immutable source revision.  Progress
is derived from signed-by-content *per-date receipts*, never from a mutable
checkpoint.  It therefore remains safe to resume after an interruption.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping, Protocol

import pandas as pd

from packages.research.gen2_discovery import canonical_hash
from packages.research.gen2_validation import (
    commit_future_observation_shard,
    evaluate_future_candidate,
    verify_stage2_contract,
)
from packages.research.json_store import write_json

RUN_SCHEMA = "gen2-future-run/v3"
BINDING_SCHEMA = "gen2-actual-source-lineage-binding/v1"
REVISION_SCHEMA = "gen2-actual-source-revision/v1"
RECEIPT_SCHEMA = "gen2-future-date-receipt/v1"
PIT_FREEZE_SCHEMA = "gen2-future-pit-freeze/v1"
_HASH_PREFIX = "sha256:"
_BINDING_FIELDS = {
    "schema_version", "asset_dataset_id", "benchmark_dataset_id", "calendar_id",
    "pit_lineage_id", "adjustment", "required_fields",
}
_REVISION_FIELDS = {
    "schema_version", "revision_hash", "parent_revision_hash", "available_from", "available_through",
    "asset_dataset_id", "benchmark_dataset_id", "calendar_id", "pit_lineage_id",
    "asset_snapshot_hash", "asset_content_hash", "benchmark_snapshot_hash", "benchmark_content_hash",
    "pit_revision_hash", "calendar_prefix_hash", "prefix_hash", "historical_prefix_hash",
    "source_completeness_hash", "created_at",
}


class FutureSource(Protocol):
    def identity(self) -> Mapping[str, Any]: ...
    def calendar(self, start: date, end: date) -> pd.DatetimeIndex: ...
    def asset_frame(self, symbol: str, start: date, end: date) -> pd.DataFrame: ...
    def benchmark_frame(self, symbol: str, start: date, end: date) -> pd.DataFrame: ...


class PitProvider(Protocol):
    def identity(self) -> Mapping[str, Any]: ...
    def active_on(self, day: date) -> set[str]: ...


def _is_hash(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 71 and value.startswith(_HASH_PREFIX) and all(c in "0123456789abcdef" for c in value[7:])


def _date_value(value: Any, label: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be ISO date") from exc


def _calendar_hash(calendar: pd.DatetimeIndex) -> str:
    return canonical_hash({"dates": [stamp.date().isoformat() for stamp in calendar]})


def _active_hash(symbols: set[str]) -> str:
    return canonical_hash({"symbols": sorted(symbols)})


def _keys_hash(rows: list[Mapping[str, Any]]) -> str:
    keys = sorted([[row["candidate_semantic_id"], row["symbol"], row["signal_date"], row["horizon"]] for row in rows])
    return canonical_hash({"keys": keys})


def _scan_revision_chain(root: Path, *, actual_binding: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Read all immutable revision manifests and reject a branch or a fork."""
    revisions: dict[str, dict[str, Any]] = {}
    directory = root / "source_revisions"
    if not directory.exists():
        return revisions
    if not directory.is_dir():
        raise ValueError("source revision path is not a directory")
    for path in sorted(directory.glob("*.json")):
        payload = _read_json(path)
        if set(payload) != _REVISION_FIELDS or payload.get("schema_version") != REVISION_SCHEMA:
            raise ValueError("stored source revision schema invalid")
        identity = {key: value for key, value in payload.items() if key != "revision_hash"}
        revision_hash = payload.get("revision_hash")
        if not _is_hash(revision_hash) or canonical_hash(identity) != revision_hash or path.stem != revision_hash[7:]:
            raise ValueError("stored source revision hash/path invalid")
        if any(payload[key] != actual_binding[key] for key in ("asset_dataset_id", "benchmark_dataset_id", "calendar_id", "pit_lineage_id")):
            raise ValueError("stored source revision lineage conflict")
        if revision_hash in revisions:
            raise ValueError("duplicate stored source revision")
        revisions[revision_hash] = payload
    if not revisions:
        return revisions
    children: dict[str, list[str]] = {key: [] for key in revisions}
    roots: list[str] = []
    for revision_hash, payload in revisions.items():
        parent = payload["parent_revision_hash"]
        if parent is None:
            roots.append(revision_hash)
        elif parent not in revisions or not _is_hash(parent):
            raise ValueError("source revision parent missing/invalid")
        else:
            children[parent].append(revision_hash)
    if len(roots) != 1 or any(len(value) > 1 for value in children.values()):
        raise ValueError("source revision chain fork/root conflict")
    seen: set[str] = set(); current = roots[0]
    while current not in seen:
        seen.add(current)
        next_items = children[current]
        if not next_items:
            break
        current = next_items[0]
    if len(seen) != len(revisions):
        raise ValueError("source revision chain cycle/disconnect")
    return revisions


def _revision_tip(revisions: Mapping[str, Mapping[str, Any]]) -> str | None:
    parents = {payload["parent_revision_hash"] for payload in revisions.values() if payload["parent_revision_hash"] is not None}
    tips = sorted(set(revisions) - parents)
    if len(tips) > 1:
        raise ValueError("source revision chain fork")
    return tips[0] if tips else None


def _persist_current_revision(root: Path, revision: Mapping[str, Any], *, actual_binding: Mapping[str, Any], as_of: date) -> dict[str, dict[str, Any]]:
    """Append a revision only when it extends the unique previously verified tip."""
    available_through = _date_value(revision["available_through"], "available_through")
    if available_through > as_of:
        raise ValueError("source revision available_through cannot exceed as_of")
    revisions = _scan_revision_chain(root, actual_binding=actual_binding)
    revision_hash = str(revision["revision_hash"])
    existing = revisions.get(revision_hash)
    if existing is not None:
        if existing != dict(revision):
            raise ValueError("stored source revision collision/tamper")
        return revisions
    tip = _revision_tip(revisions)
    parent = revision["parent_revision_hash"]
    if tip is None:
        if parent is not None or revision["historical_prefix_hash"] != canonical_hash({"prefix": "empty"}):
            raise ValueError("first source revision parent/history invalid")
    else:
        previous = revisions[tip]
        if parent != tip:
            raise ValueError("source revision parent is not current chain tip")
        if available_through <= _date_value(previous["available_through"], "previous available_through"):
            raise ValueError("source revision available_through must advance")
        if _date_value(revision["available_from"], "available_from") <= _date_value(previous["available_through"], "previous available_through"):
            raise ValueError("source revision available_from must follow parent")
        if revision["historical_prefix_hash"] != previous["prefix_hash"]:
            raise ValueError("source revision historical prefix proof mismatch")
    _write_once(root / "source_revisions" / f"{revision_hash[7:]}.json", revision)
    revisions[revision_hash] = dict(revision)
    return revisions


def _actual_binding(value: Mapping[str, Any], source: FutureSource, pit: PitProvider, stage2_contract: Mapping[str, Any]) -> dict[str, Any]:
    """Verify stable lineage and return this batch's immutable revision."""
    if set(value) != _BINDING_FIELDS or value.get("schema_version") != BINDING_SCHEMA:
        raise ValueError("actual source binding schema invalid")
    if value.get("adjustment") != "adjusted_ohlc" or value.get("required_fields") != ["open", "high", "low", "close", "prev_close", "volume", "amount", "is_st"]:
        raise ValueError("actual source field contract invalid")
    if not all(isinstance(value.get(key), str) and value[key] for key in ("asset_dataset_id", "benchmark_dataset_id", "calendar_id", "pit_lineage_id")):
        raise ValueError("actual source logical lineage invalid")
    dataset, pit_contract = stage2_contract["dataset_contract"], stage2_contract["pit_universe_contract"]
    if (value["asset_dataset_id"] != dataset["asset_dataset_id"] or value["benchmark_dataset_id"] != dataset["benchmark_dataset_id"]
            or value["calendar_id"] != dataset["calendar_id"] or value["pit_lineage_id"] != pit_contract["manifest_id"]):
        raise ValueError("actual source logical lineage differs from Stage2 contract")
    source_id, pit_id = dict(source.identity()), dict(pit.identity())
    if set(source_id) != _REVISION_FIELDS or source_id.get("schema_version") != REVISION_SCHEMA:
        raise ValueError("actual source revision schema invalid")
    identity = {key: value for key, value in source_id.items() if key != "revision_hash"}
    if source_id.get("revision_hash") != canonical_hash(identity):
        raise ValueError("actual source revision hash invalid")
    if not all(_is_hash(source_id.get(key)) for key in ("revision_hash", "asset_snapshot_hash", "asset_content_hash", "benchmark_snapshot_hash", "benchmark_content_hash", "pit_revision_hash", "calendar_prefix_hash", "prefix_hash", "historical_prefix_hash", "source_completeness_hash")):
        raise ValueError("actual source revision needs sha256 identities")
    start, end = _date_value(source_id.get("available_from"), "available_from"), _date_value(source_id.get("available_through"), "available_through")
    if start > end:
        raise ValueError("actual source revision available range invalid")
    stamp = datetime.fromisoformat(str(source_id.get("created_at")))
    if stamp.tzinfo is None:
        raise ValueError("actual source revision created_at must be timezone-aware")
    for key in ("asset_dataset_id", "benchmark_dataset_id", "calendar_id", "pit_lineage_id"):
        if source_id.get(key) != value[key]:
            raise ValueError("actual source revision logical lineage mismatch")
    if pit_id.get("pit_revision_hash") != source_id["pit_revision_hash"]:
        raise ValueError("PIT manifest binding mismatch")
    return source_id


def _write_once(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(f"write-once artifact exists: {path}")
    write_json(path, payload)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid immutable artifact: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"immutable artifact must be an object: {path}")
    return value


def _validate_date_frame(frame: pd.DataFrame, *, label: str, start: date, end: date, lockbox: date) -> None:
    if not isinstance(frame, pd.DataFrame) or not isinstance(frame.index, pd.DatetimeIndex) or not frame.index.is_monotonic_increasing or not frame.index.is_unique:
        raise ValueError(f"{label} dates invalid")
    if any(stamp.date() < start or stamp.date() > end or stamp.date() >= lockbox for stamp in frame.index):
        raise ValueError(f"{label} returned future/research-end/lockbox row")


def _validate_calendar(calendar: Any, *, start: date, end: date, lockbox: date) -> pd.DatetimeIndex:
    if not isinstance(calendar, pd.DatetimeIndex) or not calendar.is_monotonic_increasing or not calendar.is_unique:
        raise ValueError("source calendar invalid")
    if any(stamp.date() < start or stamp.date() > end or stamp.date() >= lockbox for stamp in calendar):
        raise ValueError("source calendar returned future/research-end/lockbox row")
    return calendar


def _validate_receipt_shard(root: Path, receipt: Mapping[str, Any], *, contract_hash: str) -> None:
    shard_hash = receipt["outcome_shard_hash"]
    if shard_hash is None:
        if receipt["outcome_keys_hash"] != _keys_hash([]):
            raise ValueError("empty receipt outcome identity invalid")
        return
    if not _is_hash(shard_hash):
        raise ValueError("receipt shard hash invalid")
    shard_path = root / "shards" / f"{shard_hash[7:]}.json"
    sidecar_path = root / "commits" / f"{shard_hash[7:]}.json"
    if not shard_path.is_file() or not sidecar_path.is_file():
        raise ValueError("receipt committed shard missing")
    shard, sidecar = _read_json(shard_path), _read_json(sidecar_path)
    identity = {key: value for key, value in shard.items() if key not in {"shard_hash", "committed_at"}}
    if shard.get("shard_hash") != shard_hash or canonical_hash(identity) != shard_hash:
        raise ValueError("receipt shard tampered")
    if (sidecar.get("schema_version") != "gen2-observation-commit/v1" or sidecar.get("shard_hash") != shard_hash
            or sidecar.get("contract_hash") != contract_hash or not isinstance(sidecar.get("keys"), list)):
        raise ValueError("receipt commit sidecar invalid")
    rows = shard.get("rows")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("receipt shard rows invalid")
    keys = [[row.get("candidate_semantic_id"), row.get("symbol"), row.get("signal_date"), row.get("horizon")] for row in rows]
    if sorted(sidecar["keys"]) != sorted(keys) or receipt["outcome_keys_hash"] != _keys_hash(rows):
        raise ValueError("receipt shard keys differ")
    if any(row.get("signal_date") != receipt["signal_date"] for row in rows):
        raise ValueError("receipt shard contains another signal date")


def _receipt_identity(*, day: str, completion: str, active_symbols_hash: str, outcome_shard_hash: str | None, outcome_keys_hash: str, stage2_contract: Mapping[str, Any], gen2_protocol: Mapping[str, Any], actual_binding_hash: str, revision: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": RECEIPT_SCHEMA, "signal_date": day, "completion": completion,
        "contract_hash": stage2_contract["contract_hash"], "gen2_protocol_hash": gen2_protocol["protocol_hash"],
        "actual_binding_hash": actual_binding_hash, "code_snapshot_id": stage2_contract["code_snapshot"].get("code_snapshot_id"),
        "source_revision_hash": revision["revision_hash"], "pit_revision_hash": revision["pit_revision_hash"],
        "calendar_prefix_hash": revision["calendar_prefix_hash"],
        "active_symbols_hash": active_symbols_hash, "outcome_shard_hash": outcome_shard_hash,
        "outcome_keys_hash": outcome_keys_hash, "final_lockbox_read": False,
    }


def _pit_freeze_identity(*, day: str, symbols: set[str], stage2_contract: Mapping[str, Any], gen2_protocol: Mapping[str, Any], actual_binding_hash: str, revision: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": PIT_FREEZE_SCHEMA, "signal_date": day,
        "contract_hash": stage2_contract["contract_hash"], "gen2_protocol_hash": gen2_protocol["protocol_hash"],
        "actual_binding_hash": actual_binding_hash, "source_revision_hash": revision["revision_hash"], "pit_revision_hash": revision["pit_revision_hash"],
        "symbols": sorted(symbols), "active_symbols_hash": _active_hash(symbols), "final_lockbox_read": False,
    }


def _scan_pit_freezes(root: Path, *, stage2_contract: Mapping[str, Any], gen2_protocol: Mapping[str, Any], actual_binding_hash: str, revisions: Mapping[str, Mapping[str, Any]], validation_start: date, research_end: date) -> dict[str, set[str]]:
    frozen: dict[str, set[str]] = {}
    directory = root / "pit_freezes"
    if not directory.exists():
        return frozen
    if not directory.is_dir():
        raise ValueError("PIT freeze path is not a directory")
    for path in sorted(directory.glob("*.json")):
        payload = _read_json(path)
        identity = {key: value for key, value in payload.items() if key not in {"freeze_hash", "created_at"}}
        day, symbols = identity.get("signal_date"), identity.get("symbols")
        if (not isinstance(symbols, list) or any(not isinstance(symbol, str) or not symbol for symbol in symbols)
                or symbols != sorted(set(symbols)) or path.stem != day
                or payload.get("freeze_hash") != canonical_hash(identity)):
            raise ValueError("PIT freeze schema/hash/path invalid")
        day_value = _date_value(day, "PIT freeze signal_date")
        if day_value < validation_start or day_value > research_end or day in frozen:
            raise ValueError("PIT freeze outside range/conflict")
        revision = revisions.get(identity.get("source_revision_hash"))
        if revision is None:
            raise ValueError("PIT freeze source revision missing")
        expected = _pit_freeze_identity(day=day, symbols=set(symbols), stage2_contract=stage2_contract, gen2_protocol=gen2_protocol, actual_binding_hash=actual_binding_hash, revision=revision)
        if identity != expected:
            raise ValueError("PIT freeze binding/revision conflict")
        frozen[day] = set(symbols)
    return frozen


def _write_pit_freeze(root: Path, identity: Mapping[str, Any]) -> None:
    _write_once(root / "pit_freezes" / f"{identity['signal_date']}.json", {**identity, "freeze_hash": canonical_hash(identity), "created_at": datetime.now(timezone.utc).isoformat()})


def _scan_receipts(root: Path, *, stage2_contract: Mapping[str, Any], gen2_protocol: Mapping[str, Any], actual_binding_hash: str, revisions: Mapping[str, Mapping[str, Any]], validation_start: date, research_end: date) -> dict[str, dict[str, Any]]:
    receipts: dict[str, dict[str, Any]] = {}
    directory = root / "date_receipts"
    if not directory.exists():
        return receipts
    if not directory.is_dir():
        raise ValueError("date receipt path is not a directory")
    for path in sorted(directory.glob("*.json")):
        receipt = _read_json(path)
        identity = {key: value for key, value in receipt.items() if key not in {"receipt_hash", "created_at"}}
        day = identity.get("signal_date")
        template_revision = next(iter(revisions.values()), {"revision_hash": "x", "pit_revision_hash": "x", "calendar_prefix_hash": "x"})
        if (set(receipt) != set(_receipt_identity(day="x", completion="matured", active_symbols_hash="x", outcome_shard_hash=None, outcome_keys_hash="x", stage2_contract=stage2_contract, gen2_protocol=gen2_protocol, actual_binding_hash=actual_binding_hash, revision=template_revision)) | {"receipt_hash", "created_at"}
                or receipt.get("receipt_hash") != canonical_hash(identity) or path.stem != day):
            raise ValueError("date receipt schema/hash/path invalid")
        day_value = _date_value(day, "receipt signal_date")
        if day_value < validation_start or day_value > research_end or day in receipts:
            raise ValueError("date receipt outside range/conflict")
        revision = revisions.get(identity.get("source_revision_hash"))
        if revision is None:
            raise ValueError("date receipt source revision missing")
        expected = _receipt_identity(day=day, completion=receipt.get("completion"), active_symbols_hash=receipt.get("active_symbols_hash"), outcome_shard_hash=receipt.get("outcome_shard_hash"), outcome_keys_hash=receipt.get("outcome_keys_hash"), stage2_contract=stage2_contract, gen2_protocol=gen2_protocol, actual_binding_hash=actual_binding_hash, revision=revision)
        if identity != expected or receipt.get("completion") not in {"matured", "tail_purged"} or not _is_hash(receipt.get("active_symbols_hash")):
            raise ValueError("date receipt binding/revision conflict")
        _validate_receipt_shard(root, receipt, contract_hash=stage2_contract["contract_hash"])
        receipts[day] = receipt
    return receipts


def _write_receipt(root: Path, identity: Mapping[str, Any]) -> None:
    path = root / "date_receipts" / f"{identity['signal_date']}.json"
    payload = {**identity, "receipt_hash": canonical_hash(identity), "created_at": datetime.now(timezone.utc).isoformat()}
    _write_once(path, payload)


def _run_manifest(root: Path, *, stage2_contract: Mapping[str, Any], gen2_protocol: Mapping[str, Any], actual_binding: Mapping[str, Any]) -> None:
    path = root / "run_manifest.json"
    identity = {
        "schema_version": RUN_SCHEMA, "contract_hash": stage2_contract["contract_hash"],
        "gen2_protocol_hash": gen2_protocol["protocol_hash"], "actual_binding": dict(actual_binding),
        "actual_binding_hash": canonical_hash(actual_binding), "code_snapshot_id": stage2_contract["code_snapshot"].get("code_snapshot_id"),
        "final_lockbox_read": False,
    }
    if path.exists():
        manifest = _read_json(path)
        stored = {key: value for key, value in manifest.items() if key != "created_at"}
        if stored != identity:
            raise ValueError("existing future run manifest binding/revision conflict")
    else:
        _write_once(path, {**identity, "created_at": datetime.now(timezone.utc).isoformat()})


def run_future_incremental(*, source: FutureSource, pit: PitProvider, gen2_protocol: Mapping[str, Any], stage2_contract: Mapping[str, Any], ledger: Mapping[str, Any], parent_protocol_path: Path, parent_closure_result_path: Path, actual_binding: Mapping[str, Any], as_of: date, run_root: Path, project_root: Path | None = None) -> dict[str, Any]:
    """Commit mature signal dates only; later calls resume by verifying receipts."""
    # These checks intentionally precede even the waiting return.  A caller
    # cannot claim a valid waiting run with a substituted protocol or snapshot.
    verify_stage2_contract(stage2_contract, gen2_protocol=gen2_protocol, ledger=ledger, parent_protocol_path=parent_protocol_path, parent_closure_result_path=parent_closure_result_path, project_root=project_root)
    _actual_binding(actual_binding, source, pit, stage2_contract)
    periods = gen2_protocol["periods"]
    research_start, validation_start, research_end, lockbox = (_date_value(periods[key], key) for key in ("research_start", "validation_start", "research_end", "final_lockbox_start"))
    if as_of >= lockbox:
        raise ValueError("final lockbox must not be requested")
    root = run_root.resolve(); root.mkdir(parents=True, exist_ok=True)
    _run_manifest(root, stage2_contract=stage2_contract, gen2_protocol=gen2_protocol, actual_binding=actual_binding)
    if as_of < validation_start:
        return {"status": "waiting", "final_lockbox_read": False, "outcome_reads": 0, "committed_dates": []}

    end = min(as_of, research_end)
    calendar = _validate_calendar(source.calendar(research_start, end), start=research_start, end=end, lockbox=lockbox)
    # A file-backed provider validates all explicitly listed files while
    # serving its first outcome input.  Re-read its identity afterwards so
    # the persisted revision is bound to verified, not merely declared, data.
    revision = _actual_binding(actual_binding, source, pit, stage2_contract)
    revisions = _persist_current_revision(root, revision, actual_binding=actual_binding, as_of=as_of)
    if as_of >= research_end:
        if _date_value(revision["available_through"], "available_through") < research_end:
            raise ValueError("source available_through does not cover research_end")
        if not len(calendar) or calendar[-1].date() != research_end or _calendar_hash(calendar) != revision["calendar_prefix_hash"]:
            raise ValueError("frozen calendar incomplete or binding mismatch at research_end")
    elif _calendar_hash(calendar) != revision["calendar_prefix_hash"]:
        raise ValueError("source calendar prefix binding mismatch")
    validation_days = [stamp for stamp in calendar if validation_start <= stamp.date() <= end]
    binding_hash = canonical_hash(actual_binding)
    receipts = _scan_receipts(root, stage2_contract=stage2_contract, gen2_protocol=gen2_protocol, actual_binding_hash=binding_hash, revisions=revisions, validation_start=validation_start, research_end=research_end)
    pit_freezes = _scan_pit_freezes(root, stage2_contract=stage2_contract, gen2_protocol=gen2_protocol, actual_binding_hash=binding_hash, revisions=revisions, validation_start=validation_start, research_end=research_end)
    for day_text, receipt in receipts.items():
        frozen = pit_freezes.get(day_text)
        if frozen is None or _active_hash(frozen) != receipt["active_symbols_hash"]:
            raise ValueError("date receipt PIT freeze missing/hash conflict")
    calendar_text = {stamp.date().isoformat() for stamp in validation_days}
    if not set(receipts).issubset(calendar_text):
        raise ValueError("receipt date absent from frozen calendar")
    pending = [stamp for stamp in validation_days if stamp.date().isoformat() not in receipts]
    max_horizon = max(int(value) for value in stage2_contract["execution"]["horizons"])
    positions = {stamp.date().isoformat(): pos for pos, stamp in enumerate(calendar)}

    # PIT membership is fetched exactly once for each pending signal date.
    active_by_day: dict[str, set[str]] = {}
    for stamp in pending:
        day_text = stamp.date().isoformat()
        if day_text in pit_freezes:
            active_by_day[day_text] = pit_freezes[day_text]
            continue
        active = pit.active_on(stamp.date())
        if not isinstance(active, set) or any(not isinstance(symbol, str) or not symbol for symbol in active):
            raise ValueError("PIT provider must return a set of nonempty symbols per signal date")
        active_by_day[day_text] = set(active)
        _write_pit_freeze(root, _pit_freeze_identity(day=day_text, symbols=active_by_day[day_text], stage2_contract=stage2_contract, gen2_protocol=gen2_protocol, actual_binding_hash=binding_hash, revision=revision))
    if not pending:
        complete = as_of >= research_end and {stamp.date().isoformat() for stamp in validation_days} == set(receipts)
        return {"status": "eligible_for_adjudication" if complete else "accumulating_not_adjudicable", "committed_dates": sorted(receipts), "events_committed": 0, "final_lockbox_read": False, "portfolio_confirmation_required": True}
    all_symbols = sorted(set().union(*active_by_day.values())) if active_by_day else []
    benchmark_symbol = stage2_contract["benchmark_symbol"]
    benchmark = source.benchmark_frame(benchmark_symbol, research_start, end)
    _validate_date_frame(benchmark, label="benchmark", start=research_start, end=end, lockbox=lockbox)
    if not benchmark.index.equals(calendar):
        raise ValueError("benchmark dates must exactly equal frozen calendar")
    assets: dict[str, pd.DataFrame] = {}
    for symbol in all_symbols:
        asset = source.asset_frame(symbol, research_start, end)
        _validate_date_frame(asset, label=f"asset:{symbol}", start=research_start, end=end, lockbox=lockbox)
        if any(field not in asset for field in actual_binding["required_fields"]):
            raise ValueError("asset frame lacks bound execution fields")
        # A mature date cannot be acknowledged from a sparse per-symbol frame:
        # otherwise an unfinished 20-bar exit could be misrecorded as an empty
        # event day.  Suspensions must be represented by an explicit source
        # row and the downstream execution gate decides tradability.
        if not asset.index.equals(calendar):
            raise ValueError("asset dates must exactly equal frozen calendar")
        assets[symbol] = asset

    candidates = gen2_protocol["candidate_space"]["candidates"]
    events_committed = 0
    for stamp in pending:
        day_text, position = stamp.date().isoformat(), positions[stamp.date().isoformat()]
        terminal_tail = as_of >= research_end and position + max_horizon >= len(calendar)
        if not terminal_tail and position + max_horizon >= len(calendar):
            # Do not create a receipt: a later execution must revisit this day.
            continue
        rows: list[dict[str, Any]] = []
        for symbol in sorted(active_by_day[day_text]):
            asset = assets[symbol]
            pit_active = pd.Series([symbol in active_by_day.get(index.date().isoformat(), set()) for index in asset.index], index=asset.index, dtype="boolean")
            for candidate in candidates:
                evaluated = evaluate_future_candidate(candidate, asset, benchmark, symbol=symbol, pit_active=pit_active, contract=stage2_contract, gen2_protocol=gen2_protocol, ledger=ledger, parent_protocol_path=parent_protocol_path, parent_closure_result_path=parent_closure_result_path, project_root=project_root)
                rows.extend(row for row in evaluated if row["signal_date"] == day_text and (row["status"] == "completed" or terminal_tail))
        if rows:
            committed = commit_future_observation_shard(root, rows, contract=stage2_contract)
            shard_hash: str | None = committed["shard_hash"]
        else:
            shard_hash = None
        completion = "tail_purged" if terminal_tail else "matured"
        identity = _receipt_identity(day=day_text, completion=completion, active_symbols_hash=_active_hash(active_by_day[day_text]), outcome_shard_hash=shard_hash, outcome_keys_hash=_keys_hash(rows), stage2_contract=stage2_contract, gen2_protocol=gen2_protocol, actual_binding_hash=binding_hash, revision=revision)
        _write_receipt(root, identity)
        receipts[day_text] = {**identity, "receipt_hash": canonical_hash(identity)}
        events_committed += len(rows)

    expected = {stamp.date().isoformat() for stamp in validation_days}
    complete = as_of >= research_end and expected == set(receipts)
    return {
        "status": "eligible_for_adjudication" if complete else "accumulating_not_adjudicable",
        "committed_dates": sorted(receipts), "events_committed": events_committed,
        "final_lockbox_read": False, "portfolio_confirmation_required": True,
    }
