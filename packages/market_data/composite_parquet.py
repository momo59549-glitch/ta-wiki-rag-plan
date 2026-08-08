"""Read-through composition of immutable local market-data caches."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import pandas as pd

from .local_parquet import LocalParquetMarketData, candles_from_frame


@dataclass(frozen=True, slots=True)
class CompositeParquetMarketData:
    """Resolve each symbol from ordered Parquet datasets without copying data.

    The first dataset wins.  This keeps the pre-existing local cache canonical
    while allowing the independently downloaded Tushare cache to fill gaps.
    """

    root: Path
    datasets: tuple[str, ...] = ("trend_cache", "tushare_daily_cache", "tushare_incremental_cache")

    @property
    def dataset(self) -> str:
        return "+".join(self.datasets)

    def _source_for(self, symbol: str) -> LocalParquetMarketData:
        for dataset in self.datasets:
            source = LocalParquetMarketData(self.root, dataset)
            try:
                source.source_path(symbol)
                return source
            except FileNotFoundError:
                continue
        raise FileNotFoundError(f"找不到行情: {symbol}（已查找 {self.dataset}）")

    def symbols(self, limit: int | None = None) -> list[str]:
        values: set[str] = set()
        for dataset in self.datasets:
            try:
                values.update(LocalParquetMarketData(self.root, dataset).symbols())
            except FileNotFoundError:
                continue
        result = sorted(values)
        return result[:limit] if limit is not None else result

    def source_path(self, symbol: str) -> Path:
        return self._source_for(symbol).source_path(symbol)

    def load(self, symbol: str, *args, **kwargs):
        base_paths = [self.root / dataset / f"{symbol}.parquet" for dataset in self.datasets if dataset != "tushare_incremental_cache"]
        base = next((path for path in base_paths if path.is_file()), None)
        overlay = self.root / "tushare_incremental_cache" / f"{symbol}.parquet"
        paths = ([base] if base else []) + ([overlay] if overlay.is_file() else [])
        if not paths:
            raise FileNotFoundError(f"找不到行情: {symbol}（已查找 {self.dataset}）")
        if len(paths) == 1:
            return LocalParquetMarketData(paths[0].parent.parent, paths[0].parent.name).load(symbol, *args, **kwargs)
        frames = [pd.read_parquet(path) for path in paths]
        frame = pd.concat(frames).sort_index()
        frame = frame.loc[~frame.index.duplicated(keep="last")]
        raw_columns = {"raw_open", "raw_high", "raw_low", "raw_close", "adj_factor"}
        if raw_columns.issubset(frame.columns):
            factor = pd.to_numeric(frame["adj_factor"], errors="coerce")
            scale = factor.dropna().iloc[-1] if not factor.dropna().empty else None
            if scale and scale > 0:
                for column in ("open", "high", "low", "close"):
                    frame[column] = pd.to_numeric(frame[f"raw_{column}"], errors="coerce") * factor / scale
        return candles_from_frame(frame, symbol=symbol, start=kwargs.get("start"), end=kwargs.get("end"))

    def snapshot_id(self, symbols: list[str]) -> str:
        items = []
        for symbol in sorted(set(symbols)):
            paths = [self.root / dataset / f"{symbol}.parquet" for dataset in self.datasets]
            paths = [path for path in paths if path.is_file()]
            if not paths:
                raise FileNotFoundError(symbol)
            for path in paths:
                stat = path.stat()
                items.append(f"{symbol}:{path.parent.name}:{stat.st_size}:{stat.st_mtime_ns}")
        return "sha256:" + sha256((self.dataset + "|" + "|".join(items)).encode("utf-8")).hexdigest()
