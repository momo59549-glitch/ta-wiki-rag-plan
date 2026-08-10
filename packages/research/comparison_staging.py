"""Bounded-memory compact staging for preregistered candidate comparison."""
from __future__ import annotations

from collections import OrderedDict, defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterator, Mapping

from packages.research.comparison_panel import ShardedPanel
from packages.research.json_store import write_json, write_jsonl
from packages.research.run_artifacts import canonical_hash, file_hash, load_commits


STAGING_SCHEMA = "candidate-comparison-staging/v1"


class _PlanWriter:
    def __init__(self, root: Path, max_open: int = 32):
        self.root = root; self.max_open = max_open; self.handles: OrderedDict[Path, Any] = OrderedDict(); self.paths: set[Path] = set()

    def append(self, candidate: str, horizon: int, entry_date: str, row: Mapping[str, Any]) -> None:
        path = self.root / "plans" / candidate / str(horizon) / f"{entry_date}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True); self.paths.add(path)
        handle = self.handles.pop(path, None)
        if handle is None: handle = path.open("a", encoding="utf-8", newline="\n")
        self.handles[path] = handle
        handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
        if len(self.handles) > self.max_open:
            _, oldest = self.handles.popitem(last=False); oldest.close()

    def close(self) -> list[Path]:
        for handle in self.handles.values(): handle.close()
        self.handles.clear(); return sorted(self.paths)


def _jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip(): yield json.loads(line)


def build_compact_staging(protocol: Mapping[str, Any], validated: Mapping[str, Mapping[str, Any]], panel: ShardedPanel, staging_dir: Path) -> dict[str, Any]:
    """Join only one committed source batch at a time and publish sealed staging."""
    staging_dir = staging_dir.resolve()
    if staging_dir.exists(): raise FileExistsError("comparison staging already exists; refusing mixed retry")
    staging_dir.mkdir(parents=True)
    horizons = [int(item) for item in protocol["analysis"]["horizons"]]
    cooldown = int(protocol["analysis"]["cooldown_trading_bars"])
    delay = int(protocol["execution"]["max_exit_delay_bars"])
    writer = _PlanWriter(staging_dir)
    candidate_manifests = []
    diagnostics = {"peak_batch_observations": 0, "peak_batch_outcomes": 0, "peak_compact_events": 0,
                   "outcome_stream_buffer_rows": 1, "peak_panel_symbol_indexes": 1}
    try:
        for candidate in sorted(validated):
            identity = validated[candidate]; run_dir = Path(identity["research_run"])
            artifact = json.loads((run_dir / "artifact_manifest.json").read_text(encoding="utf-8"))
            commits = load_commits(run_dir, artifact["execution_identity_hash"])
            if [item["commit_hash"] for item in commits] != identity["artifact_commit_hashes"]:
                raise ValueError(f"staging source commits differ from frozen case: {candidate}")
            last_selected: dict[str, int] = {}; shards = []; selected_total = 0; tail_counts = {str(h): 0 for h in horizons}; tail_samples = {str(h): [] for h in horizons}
            selection_digest = sha256(); previous_event_key = None
            for commit in commits:
                observations = {}
                for item in _jsonl(run_dir / commit["observations"]["path"]):
                    day = str(item["observed_at"])[:10]
                    if protocol["oos"]["start"] <= day <= protocol["oos"]["end"]:
                        observations[str(item["id"])] = {"symbol": str(item["symbol"]), "date": day, "horizons": {}}
                diagnostics["peak_batch_observations"] = max(diagnostics["peak_batch_observations"], len(observations))
                outcome_rows = 0
                for outcome in _jsonl(run_dir / commit["outcomes"]["path"]):
                    outcome_rows += 1; oid = str(outcome.get("observation_id")); horizon = int(outcome.get("horizon_bars", -1))
                    event = observations.get(oid)
                    if event is not None and horizon in horizons and outcome.get("sample_split") == "out_of_sample" and outcome.get("net_excess_return") is not None:
                        event["horizons"][str(horizon)] = {"regime": str(outcome.get("market_regime", "unknown")), "net_excess_return": float(outcome["net_excess_return"])}
                diagnostics["peak_batch_outcomes"] = max(diagnostics["peak_batch_outcomes"], outcome_rows)
                compact = []
                by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
                for event in observations.values():
                    if event["horizons"]: by_symbol[event["symbol"]].append(event)
                for symbol, events in sorted(by_symbol.items()):
                    rows, date_index = panel.load_symbol(symbol)
                    for event in sorted(events, key=lambda item: item["date"]):
                        ordinal = date_index.get(event["date"])
                        if ordinal is None: raise ValueError(f"event absent from comparison panel: {symbol} {event['date']}")
                        if symbol in last_selected and ordinal - last_selected[symbol] < cooldown: continue
                        last_selected[symbol] = ordinal; compact.append(event)
                        for horizon_text in sorted(event["horizons"], key=int):
                            horizon = int(horizon_text); deadline = ordinal + horizon + delay
                            if deadline >= len(rows):
                                tail_counts[str(horizon)] += 1
                                if len(tail_samples[str(horizon)]) < int(protocol["execution"]["audit_sample_limit"]): tail_samples[str(horizon)].append({"symbol": symbol, "signal_date": event["date"]})
                                continue
                            entry = rows[ordinal + 1]
                            writer.append(candidate, horizon, str(entry["date"]), {
                                "candidate": candidate, "horizon": horizon, "symbol": symbol, "signal_date": event["date"],
                                "entry_date": str(entry["date"]), "entry_open": float(entry["open"]),
                                "entry_tradeable": bool(entry["tradeable_open"]), "entry_reason_codes": entry.get("open_reason_codes", []),
                                "scheduled_exit_date": str(rows[ordinal + horizon]["date"]), "deadline_date": str(rows[deadline]["date"]),
                                "selection_rank": sha256(f"{protocol['analysis']['seed']}|{candidate}|{symbol}|{event['date']}".encode()).hexdigest(),
                            })
                diagnostics["peak_compact_events"] = max(diagnostics["peak_compact_events"], len(compact))
                for item in compact:
                    key = (item["symbol"], item["date"])
                    if previous_event_key is not None and key <= previous_event_key:
                        raise ValueError(f"source batches are not globally symbol/date ordered: {candidate}")
                    previous_event_key = key
                shard_path = staging_dir / "events" / candidate / f"batch_{int(commit['batch_index']):06d}.jsonl"
                write_jsonl(shard_path, compact)
                for item in compact: selection_digest.update((json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n").encode())
                selected_total += len(compact)
                shards.append({"batch_index": int(commit["batch_index"]), "path": shard_path.relative_to(staging_dir).as_posix(), "rows": len(compact), "sha256": file_hash(shard_path), "source_commit_hash": commit["commit_hash"]})
            candidate_manifests.append({"candidate": candidate, "case_id": identity["case_id"], "protocol_id": identity["protocol_id"],
                                        "artifact_commit_hashes": identity["artifact_commit_hashes"], "events": selected_total,
                                        "selection_hash": "sha256:" + selection_digest.hexdigest(), "tail_purged_counts": tail_counts,
                                        "tail_purged_samples": tail_samples, "shards": shards})
    finally:
        plan_paths = writer.close()
    plan_shards = []
    for path in plan_paths:
        relative = path.relative_to(staging_dir).as_posix(); parts = path.relative_to(staging_dir / "plans").parts
        with path.open("r", encoding="utf-8") as handle: count = sum(1 for line in handle if line.strip())
        plan_shards.append({"candidate": parts[0], "horizon": int(parts[1]), "entry_date": path.stem, "path": relative, "rows": count, "sha256": file_hash(path)})
    identity_payload = {"schema_version": STAGING_SCHEMA, "comparison_id": protocol["comparison_id"], "comparison_hash": protocol["comparison_hash"],
                        "comparison_code_snapshot_id": protocol["comparison_code_snapshot"]["code_snapshot_id"],
                        "panel_id": panel.manifest["panel_id"], "candidates": candidate_manifests, "plan_shards": plan_shards, "diagnostics": diagnostics}
    manifest = {**identity_payload, "staging_hash": canonical_hash(identity_payload), "created_at": datetime.now(timezone.utc)}
    write_json(staging_dir / "staging_manifest.json", manifest); return manifest


def verify_staging(protocol: Mapping[str, Any], panel: ShardedPanel, staging_dir: Path) -> dict[str, Any]:
    path = staging_dir / "staging_manifest.json"
    if not path.is_file(): raise ValueError("comparison staging manifest missing")
    payload = json.loads(path.read_text(encoding="utf-8"))
    identity = {key: payload[key] for key in ("schema_version", "comparison_id", "comparison_hash", "comparison_code_snapshot_id", "panel_id", "candidates", "plan_shards", "diagnostics")}
    if (payload.get("staging_hash") != canonical_hash(identity) or payload.get("schema_version") != STAGING_SCHEMA
            or payload.get("comparison_id") != protocol["comparison_id"] or payload.get("comparison_hash") != protocol["comparison_hash"]
            or payload.get("comparison_code_snapshot_id") != protocol["comparison_code_snapshot"]["code_snapshot_id"]
            or payload.get("panel_id") != panel.manifest["panel_id"]):
        raise ValueError("comparison staging identity mismatch")
    expected_candidates = {item["candidate"]: (item["case_id"], item["protocol_id"], item["artifact_commit_hashes"]) for item in protocol["candidates"]}
    actual_candidates = {item["candidate"]: (item["case_id"], item["protocol_id"], item["artifact_commit_hashes"]) for item in payload["candidates"]}
    if actual_candidates != expected_candidates:
        raise ValueError("comparison staging candidate commits mismatch")
    for candidate in payload["candidates"]:
        for shard in candidate["shards"]:
            if file_hash(staging_dir / shard["path"]) != shard["sha256"]: raise ValueError("comparison event staging shard hash mismatch")
    for shard in payload["plan_shards"]:
        if file_hash(staging_dir / shard["path"]) != shard["sha256"]: raise ValueError("comparison plan staging shard hash mismatch")
    return payload


def iter_candidate_events(staging_dir: Path, manifest: Mapping[str, Any], candidate: str) -> Iterator[dict[str, Any]]:
    record = next(item for item in manifest["candidates"] if item["candidate"] == candidate)
    for shard in sorted(record["shards"], key=lambda item: item["batch_index"]): yield from _jsonl(staging_dir / shard["path"])
