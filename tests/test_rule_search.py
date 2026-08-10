import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np
import pandas as pd

from packages.contracts import Candle, RuleDefinition
from packages.market_data import LocalParquetMarketData
from packages.research.indicators import candles_to_frame, compute_indicators
from packages.research.rule_search import (
    SearchConfig,
    build_search_protocol,
    build_search_space,
    screen_candidates,
    vectorized_evaluate,
)
from packages.research.rule_search import _base_columns
from packages.rule_dsl import compile_rule
from packages.rule_engine import evaluate


def _random_frame(rows: int = 260, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 10.0 + np.cumsum(rng.normal(0.0, 0.25, rows))
    open_ = close * (1.0 + rng.normal(0.0, 0.004, rows))
    high = np.maximum(open_, close) * (1.0 + rng.uniform(0.0, 0.01, rows))
    low = np.minimum(open_, close) * (1.0 - rng.uniform(0.0, 0.01, rows))
    frame = pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.integers(1_000, 10_000, rows),
            "amount": rng.integers(1_000_000, 10_000_000, rows),
            "prev_close": pd.Series(close).shift(1),
            "is_st": False,
        },
        index=pd.date_range("2020-01-01", periods=rows, name="date"),
    )
    return frame


def _candles(frame: pd.DataFrame) -> list[Candle]:
    candles = []
    for position, (ts, row) in enumerate(frame.iterrows()):
        candles.append(
            Candle(
                timestamp=ts,
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=float(row.volume),
                amount=float(row.amount),
                prev_close=None if pd.isna(row.prev_close) else float(row.prev_close),
                is_st=bool(row.is_st),
                available_at=ts,
            )
        )
    return candles


class VectorizedConformanceTests(unittest.TestCase):
    def test_vectorized_matches_engine_with_indicators(self):
        frame = _random_frame(rows=300, seed=11)
        candles = _candles(frame)
        definitions = []
        seen_families = set()
        for item in build_search_space():
            if item.id not in seen_families:
                definitions.append(item)
                seen_families.add(item.id)
        self.assertGreaterEqual(len(definitions), 20)
        columns = _base_columns(frame)
        for definition in definitions:
            rule = compile_rule(definition)
            indicators = compute_indicators(frame, needs=rule.required_indicators)
            columns.update(indicators)
            vectorized = vectorized_evaluate(rule.normalized_expression, columns, definition.parameters).to_numpy(dtype=bool, copy=True)
            # The screening path applies the compiler's warmup gate and never
            # emits the final bar because it has no next-session entry.
            vectorized[:rule.max_lookback] = False
            vectorized[-1] = False
            engine = np.zeros(len(candles), dtype=bool)
            for index in range(rule.max_lookback, len(candles) - 1):
                result = evaluate(candles, index, rule, indicators={key: indicators[key].tolist() for key in rule.required_indicators})
                engine[index] = result.status == "matched"
            self.assertTrue(
                np.array_equal(vectorized[: len(engine)], engine),
                msg=f"{definition.id}@{definition.version} 向量化与引擎结果不一致",
            )

    def test_vectorized_matches_engine_for_nary_multiplication(self):
        frame = _random_frame(rows=20, seed=19)
        candles = _candles(frame)
        definition = RuleDefinition(
            id="nary_mul",
            version="test",
            name_zh="三元乘法",
            expression={"gt": [{"mul": [2.0, 3.0, 4.0]}, 10.0]},
        )
        rule = compile_rule(definition)
        vectorized = vectorized_evaluate(rule.normalized_expression, _base_columns(frame), definition.parameters)
        self.assertTrue(vectorized.all())
        self.assertTrue(evaluate(candles, 0, rule).matched)

    def test_windowed_fallback_matches_precomputed_for_sma_roc(self):
        frame = _random_frame(rows=120, seed=3)
        candles = _candles(frame)
        definition = build_search_space()[150]  # a roc_threshold candidate
        rule = compile_rule(definition)
        indicators = compute_indicators(frame, needs=rule.required_indicators)
        precomputed = {key: indicators[key].tolist() for key in rule.required_indicators}
        for index in range(rule.max_lookback, len(candles) - 1):
            with_columns = evaluate(candles, index, rule, indicators=precomputed)
            fallback = evaluate(candles, index, rule)
            self.assertEqual(with_columns.status == "matched", fallback.status == "matched", msg=f"index {index}")


class SearchSpaceTests(unittest.TestCase):
    def test_space_is_bounded_and_unique(self):
        definitions = build_search_space()
        self.assertEqual(len(definitions), 178)
        hashes = [compile_rule(item).semantic_hash for item in definitions]
        self.assertEqual(len(set(hashes)), len(hashes))
        families = {item.id for item in definitions}
        self.assertIn("ma_cross", families)
        self.assertIn("macd_cross", families)
        self.assertIn("rsi_level", families)
        self.assertIn("bollinger", families)
        self.assertIn("volume_surge", families)
        indicator_rules = [compile_rule(item) for item in definitions if item.id in {"ma_cross", "macd_cross", "rsi_level"}]
        self.assertTrue(all(rule.required_indicators for rule in indicator_rules))


class ScreenEndToEndTests(unittest.TestCase):
    def test_horizon_one_exit_price_matches_pipeline_entry_day_close(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            trend = root / "trend_cache"
            benchmark = root / "etf_cache"
            trend.mkdir()
            benchmark.mkdir()
            dates = pd.date_range("2020-01-01", periods=5, name="date")
            stock = pd.DataFrame(
                {
                    "open": [10.0, 10.0, 10.0, 10.0, 10.0],
                    "high": [10.5, 11.5, 12.5, 100.5, 10.5],
                    "low": [9.5, 9.5, 9.5, 9.5, 9.5],
                    "close": [10.0, 11.0, 12.0, 100.0, 10.0],
                    "volume": [1000.0] * 5,
                    "amount": [1_000_000.0] * 5,
                },
                index=dates,
            )
            stock.to_parquet(trend / "000001.parquet")
            pd.DataFrame(
                {
                    "open": [100.0] * 5,
                    "high": [100.0] * 5,
                    "low": [100.0] * 5,
                    "close": [100.0] * 5,
                    "volume": [1000.0] * 5,
                    "amount": [1_000_000.0] * 5,
                },
                index=dates,
            ).to_parquet(benchmark / "000001.parquet")
            manifest = root / "universe.jsonl"
            manifest.write_text(json.dumps({"symbol": "000001", "active_from": "2020-01-01", "source": "test"}) + "\n", encoding="utf-8")
            definition = RuleDefinition(
                id="single_event",
                version="test",
                name_zh="单次事件",
                expression={"eq": [{"metric": {"name": "close", "offset": 0}}, 11.0]},
            )
            output = root / "screen"
            config = SearchConfig(
                horizons=(1,),
                start=pd.Timestamp("2020-01-01").date(),
                end=pd.Timestamp("2020-01-05").date(),
                out_of_sample_start=pd.Timestamp("2020-01-02").date(),
                lockbox_start=pd.Timestamp("2020-01-10").date(),
                benchmark_symbol="000001",
                commission_bps_per_side=0.0,
                slippage_bps_per_side=0.0,
                skip_untradeable=False,
                min_out_of_sample_observations=1,
            )
            screen_candidates(LocalParquetMarketData(root), ["000001"], [definition], config, output, universe_manifest=manifest)
            semantic_hash = compile_rule(definition).semantic_hash
            record = json.loads((output / "candidates" / f"{semantic_hash.removeprefix('sha256:')[:16]}.json").read_text(encoding="utf-8"))
            group = next(item for item in record["statistics"]["groups"] if item["horizon_bars"] == 1)
            # T=2020-01-02, entry=2020-01-03 open 10, one-bar exit is that
            # same 2020-01-03 close 12, not the following close 100.
            self.assertAlmostEqual(group["mean_return"], 0.20)

    def test_screen_writes_round_and_applies_cross_candidate_fdr(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            trend = root / "trend_cache"
            trend.mkdir()
            benchmark = root / "etf_cache"
            benchmark.mkdir()
            start = pd.Timestamp("2020-01-01").date()
            end = pd.Timestamp("2020-12-31").date()
            for symbol in ("000001", "000002", "000003"):
                frame = _random_frame(rows=260, seed=int(symbol))
                frame.index = pd.date_range(start, periods=len(frame), name="date")
                frame.to_parquet(trend / f"{symbol}.parquet")
            benchmark_frame = _random_frame(rows=260, seed=99)
            benchmark_frame.index = pd.date_range(start, periods=len(benchmark_frame), name="date")
            benchmark_frame.to_parquet(benchmark / "000001.parquet")
            manifest = root / "universe.jsonl"
            manifest.write_text(
                "\n".join(json.dumps({"symbol": symbol, "active_from": "2020-01-01", "source": "test"}) for symbol in ("000001", "000002", "000003")) + "\n",
                encoding="utf-8",
            )
            definitions = build_search_space()[:6]
            config = SearchConfig(
                horizons=(1, 3, 5),
                start=start,
                end=end,
                out_of_sample_start=pd.Timestamp("2020-07-01").date(),
                lockbox_start=pd.Timestamp("2021-01-01").date(),
                benchmark_symbol="000001",
                min_out_of_sample_observations=2,
            )
            source = LocalParquetMarketData(root)
            output = root / "search"
            protocol = build_search_protocol(definitions, ["000001", "000002", "000003"], config, output, universe_manifest=manifest)
            self.assertTrue(protocol["search_id"].startswith("search_"))
            summary = screen_candidates(source, ["000001", "000002", "000003"], definitions, config, output, universe_manifest=manifest)
            self.assertEqual(summary["candidates_total"], 6)
            self.assertIn("best", summary)
            round_payload = json.loads((output / "round.json").read_text(encoding="utf-8"))
            self.assertEqual(round_payload["schema_version"], "rule-search-round/v1")
            self.assertTrue(all(item["status"] in {"passed_screen", "rejected"} for item in round_payload["candidates"]))
            for candidate in round_payload["candidates"]:
                self.assertTrue(candidate["semantic_hash"].startswith("sha256:"))
                self.assertIn("statistics", json.loads((output / "candidates" / f"{candidate['semantic_hash'].removeprefix('sha256:')[:16]}.json").read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
