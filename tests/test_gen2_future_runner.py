from __future__ import annotations

from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from packages.research.gen2_discovery import canonical_hash
from packages.research.gen2_future_runner import run_future_incremental
from _gen2_closure_fixture import closure_fixture


def _hash(char: str) -> str:
    return "sha256:" + char * 64


def _calendar_hash(index: pd.DatetimeIndex) -> str:
    return canonical_hash({"dates": [item.date().isoformat() for item in index]})


class FakeSource:
    def __init__(self, index: pd.DatetimeIndex, *, extra_calendar: bool = False, extra_asset: bool = False, missing_benchmark: bool = False, available_through: str = "2026-09-01"):
        self.index, self.extra_calendar, self.extra_asset, self.missing_benchmark = index, extra_calendar, extra_asset, missing_benchmark
        self.calls = {"calendar": 0, "asset": 0, "benchmark": 0}
        self.available_through, self.parent_revision_hash = available_through, None
        self.history_override = None

    def identity(self):
        prefix = self.index[self.index.date <= date.fromisoformat(self.available_through)]
        identity = {"schema_version": "gen2-actual-source-revision/v1", "parent_revision_hash": self.parent_revision_hash, "available_from": "2026-09-01" if self.parent_revision_hash is None else (date.fromisoformat(self.parent_through) + pd.offsets.BDay(1)).date().isoformat(), "available_through": self.available_through, "asset_dataset_id": "future-assets-v1", "benchmark_dataset_id": "future-benchmark-v1", "calendar_id": "future-calendar-v1", "pit_lineage_id": "pit-universe-v1", "asset_snapshot_hash": _hash("a"), "asset_content_hash": _hash("b"), "benchmark_snapshot_hash": _hash("c"), "benchmark_content_hash": _hash("d"), "pit_revision_hash": _hash("f"), "calendar_prefix_hash": _calendar_hash(prefix), "prefix_hash": _hash("e") if self.parent_revision_hash is None else _hash("9"), "historical_prefix_hash": canonical_hash({"prefix": "empty"}) if self.parent_revision_hash is None else self.history_override or _hash("e"), "source_completeness_hash": _hash("8"), "created_at": "2026-08-01T00:00:00+00:00"}
        return {**identity, "revision_hash": canonical_hash(identity)}
    def advance(self, through: str):
        prior = self.identity(); self.parent_revision_hash, self.parent_through, self.available_through = prior["revision_hash"], self.available_through, through
    def calendar(self, start, end):
        self.calls["calendar"] += 1
        result = self.index[(self.index.date >= start) & (self.index.date <= end) & (self.index.date <= date.fromisoformat(self.available_through))]
        if self.extra_calendar: result = result.append(pd.DatetimeIndex([pd.Timestamp("2026-10-01")]))
        return result
    def _frame(self, start, end, benchmark=False):
        values = np.arange(len(self.index), dtype=float)
        data = {"open": 100 + values, "high": 101 + values, "low": 99 + values, "close": 100.5 + values, "prev_close": 100 + values, "volume": np.full(len(values), 1000.), "amount": np.full(len(values), 10000.), "is_st": np.zeros(len(values), dtype=bool)}
        if benchmark: data = {"open": 200 + values, "close": 201 + values}
        result = pd.DataFrame(data, index=self.index)
        result = result[(result.index.date >= start) & (result.index.date <= end) & (result.index.date <= date.fromisoformat(self.available_through))]
        if self.extra_asset: result.loc[pd.Timestamp("2026-10-01")] = result.iloc[-1]
        return result
    def asset_frame(self, symbol, start, end): self.calls["asset"] += 1; return self._frame(start, end)
    def benchmark_frame(self, symbol, start, end):
        self.calls["benchmark"] += 1
        value = self._frame(start, end, benchmark=True)
        return value.iloc[:-1] if self.missing_benchmark else value


class FakePit:
    def __init__(self, members=None, *, revision=_hash("f")):
        self.members = members or {}; self.calls = [] ; self.revision = revision
    def identity(self): return {"pit_revision_hash": self.revision}
    def active_on(self, day): self.calls.append(day); return set(self.members.get(day.isoformat(), set()))


def _binding(source: FakeSource, pit: FakePit) -> dict:
    return {"schema_version": "gen2-actual-source-lineage-binding/v1", "asset_dataset_id": "future-assets-v1", "benchmark_dataset_id": "future-benchmark-v1", "calendar_id": "future-calendar-v1", "pit_lineage_id": "pit-universe-v1", "adjustment": "adjusted_ohlc", "required_fields": ["open", "high", "low", "close", "prev_close", "volume", "amount", "is_st"]}


def _inputs(index: pd.DatetimeIndex):
    candidate = {"candidate_semantic_id": _hash("1")}
    protocol = {"protocol_hash": _hash("2"), "periods": {"research_start": "2026-09-01", "validation_start": "2026-09-02", "research_end": "2026-09-30", "final_lockbox_start": "2026-10-01"}, "candidate_space": {"candidates": [candidate]}}
    contract = {"contract_hash": _hash("3"), "benchmark_symbol": "000300", "candidate_semantic_ids": [candidate["candidate_semantic_id"]], "code_snapshot": {"code_snapshot_id": _hash("4")}, "periods": {"validation_start": "2026-09-02", "research_end": "2026-09-30", "final_lockbox_start": "2026-10-01"}, "execution": {"horizons": [5, 10, 20], "commission_bps_per_side": 3., "slippage_bps_per_side": 5.}, "dataset_contract": {"asset_dataset_id": "future-assets-v1", "benchmark_dataset_id": "future-benchmark-v1", "calendar_id": "future-calendar-v1"}, "pit_universe_contract": {"manifest_id": "pit-universe-v1"}}
    return protocol, contract


def _fake_evaluate(candidate, asset, benchmark, *, symbol, pit_active, **kwargs):
    # One completed 5/10/20 record only for actual active signal dates.  This
    # gives the runner a real shard schema without depending on rule grammar.
    rows = []
    for stamp, active in pit_active.items():
        if not bool(active): continue
        for horizon in (5, 10, 20):
            target = asset.index.get_loc(stamp) + horizon
            if target >= len(asset):
                rows.append({"candidate_semantic_id": candidate["candidate_semantic_id"], "symbol": symbol, "signal_date": stamp.date().isoformat(), "horizon": horizon, "status": "tail_purged"})
            else:
                rows.append({"candidate_semantic_id": candidate["candidate_semantic_id"], "symbol": symbol, "signal_date": stamp.date().isoformat(), "entry_date": asset.index[asset.index.get_loc(stamp) + 1].date().isoformat(), "exit_date": asset.index[target].date().isoformat(), "horizon": horizon, "status": "completed", "net_return": .01, "benchmark_return": .002, "excess_return": .008, "cost_round_trip": .0016})
    return rows


class FutureRunnerTests(unittest.TestCase):
    def _run(self, root, source, pit, as_of, *, protocol=None, contract=None, binding=None):
        protocol, contract = (protocol, contract) if protocol is not None else _inputs(source.index)
        closure_path = root / "parent_closure" / "comparison_result.json"
        if not closure_path.is_file(): closure_path, _ = closure_fixture(root)
        with patch("packages.research.gen2_future_runner.verify_stage2_contract"), patch("packages.research.gen2_future_runner.evaluate_future_candidate", side_effect=_fake_evaluate):
            return run_future_incremental(source=source, pit=pit, gen2_protocol=protocol, stage2_contract=contract, ledger={}, parent_protocol_path=Path("parent.json"), parent_closure_result_path=closure_path, actual_binding=binding or _binding(source, pit), as_of=as_of, run_root=root)

    def test_waiting_validates_static_binding_but_reads_no_outcomes(self):
        index = pd.date_range("2026-09-01", "2026-09-30", freq="B"); source, pit = FakeSource(index), FakePit()
        with TemporaryDirectory() as temp:
            outcome = self._run(Path(temp), source, pit, date(2026, 9, 1))
        self.assertEqual(outcome["status"], "waiting")
        self.assertEqual(source.calls, {"calendar": 0, "asset": 0, "benchmark": 0})
        self.assertEqual(pit.calls, [])
        bad = _binding(source, pit); bad["asset_dataset_id"] = "substituted"
        with TemporaryDirectory() as temp, self.assertRaisesRegex(ValueError, "lineage"):
            self._run(Path(temp), source, pit, date(2026, 9, 1), binding=bad)

    def test_maturity_resume_empty_receipt_and_asset_cache(self):
        index = pd.date_range("2026-09-01", "2026-09-30", freq="B")
        members = {"2026-09-02": {"AAA"}, "2026-09-03": set(), "2026-09-04": {"AAA"}}
        source, pit = FakeSource(index, available_through="2026-09-10"), FakePit(members)
        with TemporaryDirectory() as temp:
            root = Path(temp)
            early = self._run(root, source, pit, date(2026, 9, 10))
            self.assertNotIn("2026-09-02", early["committed_dates"])  # 20-bar exit not ready
            self.assertEqual(source.calls["asset"], 1)  # not day x symbol
            self.assertEqual(pit.calls, [date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 4), date(2026, 9, 7), date(2026, 9, 8), date(2026, 9, 9), date(2026, 9, 10)])
            source.calls = {"calendar": 0, "asset": 0, "benchmark": 0}; pit.calls = []
            source.advance("2026-09-30")
            later = self._run(root, source, pit, date(2026, 9, 30))
            self.assertEqual(later["status"], "eligible_for_adjudication")
            self.assertTrue(all(item > date(2026, 9, 10) for item in pit.calls))  # early PIT snapshots were reused
            self.assertIn("2026-09-02", later["committed_dates"])
            self.assertTrue((root / "date_receipts" / "2026-09-03.json").is_file())
            empty = json.loads((root / "date_receipts" / "2026-09-03.json").read_text(encoding="utf-8"))
            self.assertIsNone(empty["outcome_shard_hash"])
            self.assertEqual(source.calls["asset"], 1)
            source.calls = {"calendar": 0, "asset": 0, "benchmark": 0}; pit.calls = []
            again = self._run(root, source, pit, date(2026, 9, 30))
            self.assertEqual(again["events_committed"], 0)
            self.assertEqual(pit.calls, [])

    def test_receipt_manifest_revision_and_future_rows_fail_closed(self):
        index = pd.date_range("2026-09-01", "2026-09-30", freq="B"); members = {"2026-09-02": {"AAA"}}; source, pit = FakeSource(index, available_through="2026-09-30"), FakePit(members)
        with TemporaryDirectory() as temp:
            root = Path(temp); self._run(root, source, pit, date(2026, 9, 30))
            receipt = root / "date_receipts" / "2026-09-02.json"
            payload = json.loads(receipt.read_text(encoding="utf-8")); payload["active_symbols_hash"] = _hash("0"); receipt.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "schema/hash"):
                self._run(root, source, pit, date(2026, 9, 30))
        source, pit = FakeSource(index, extra_asset=True, available_through="2026-09-05"), FakePit()
        with TemporaryDirectory() as temp, self.assertRaisesRegex(ValueError, "future/research-end/lockbox"):
            self._run(Path(temp), source, pit, date(2026, 9, 5))
        source, pit = FakeSource(index, missing_benchmark=True, available_through="2026-09-05"), FakePit()
        with TemporaryDirectory() as temp, self.assertRaisesRegex(ValueError, "exactly"):
            self._run(Path(temp), source, pit, date(2026, 9, 5))
        source, pit = FakeSource(index, available_through="2026-09-29"), FakePit()
        with TemporaryDirectory() as temp, self.assertRaisesRegex(ValueError, "available_through"):
            self._run(Path(temp), source, pit, date(2026, 9, 30))

    def test_calendar_incomplete_and_manifest_tamper_fail_closed(self):
        index = pd.date_range("2026-09-01", "2026-09-29", freq="B"); source, pit = FakeSource(index, available_through="2026-09-30"), FakePit()
        with TemporaryDirectory() as temp, self.assertRaisesRegex(ValueError, "incomplete"):
            self._run(Path(temp), source, pit, date(2026, 9, 30))
        index = pd.date_range("2026-09-01", "2026-09-30", freq="B"); source, pit = FakeSource(index, available_through="2026-09-05"), FakePit()
        with TemporaryDirectory() as temp:
            root=Path(temp); self._run(root, source, pit, date(2026, 9, 5))
            path=root / "run_manifest.json"; payload=json.loads(path.read_text(encoding="utf-8")); payload["contract_hash"]=_hash("0"); path.write_text(json.dumps(payload),encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "manifest"):
                self._run(root, source, pit, date(2026, 9, 5))

    def test_revision_chain_requires_exact_parent_monotonicity_and_prefix_proof(self):
        index = pd.date_range("2026-09-01", "2026-09-30", freq="B"); source, pit = FakeSource(index, available_through="2026-09-10"), FakePit()
        with TemporaryDirectory() as temp:
            root = Path(temp); self._run(root, source, pit, date(2026, 9, 10))
            source.advance("2026-09-30"); source.parent_revision_hash = _hash("0")
            with self.assertRaisesRegex(ValueError, "parent is not current"):
                self._run(root, source, pit, date(2026, 9, 30))
        source, pit = FakeSource(index, available_through="2026-09-10"), FakePit()
        with TemporaryDirectory() as temp:
            root = Path(temp); self._run(root, source, pit, date(2026, 9, 10))
            source.advance("2026-09-30"); source.history_override = _hash("0")
            with self.assertRaisesRegex(ValueError, "historical prefix"):
                self._run(root, source, pit, date(2026, 9, 30))

    def test_receipt_requires_matching_immutable_pit_freeze(self):
        index=pd.date_range("2026-09-01", "2026-09-30", freq="B"); source,pit=FakeSource(index,available_through="2026-09-30"),FakePit({"2026-09-02":{"AAA"}})
        with TemporaryDirectory() as temp:
            root=Path(temp); self._run(root,source,pit,date(2026,9,30))
            (root / "pit_freezes" / "2026-09-02.json").unlink()
            with self.assertRaisesRegex(ValueError,"PIT freeze missing"):
                self._run(root,source,pit,date(2026,9,30))


class FutureRunnerCliTests(unittest.TestCase):
    def test_cli_requires_explicit_manifest_provider_never_market_root(self):
        root = Path(__file__).resolve().parents[1]
        missing = subprocess.run([sys.executable, "scripts/run_gen2_validation.py", "future-run"], cwd=root, capture_output=True, text=True, check=False)
        self.assertNotEqual(missing.returncode, 0); self.assertIn("--gen2-protocol", missing.stderr)
        help_text = subprocess.run([sys.executable, "scripts/run_gen2_validation.py", "future-run", "--help"], cwd=root, capture_output=True, text=True, check=False).stdout
        self.assertNotIn("market-root", help_text)
        self.assertIn("--source-revision-manifest", help_text)
        self.assertIn("--allowed-data-root", help_text)


if __name__ == "__main__":
    unittest.main()
