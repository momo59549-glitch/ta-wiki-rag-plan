"""只读接入 Model/data 的 Parquet 行情，不复制或修改源数据。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from hashlib import sha256
from pathlib import Path
import re

import pandas as pd

from packages.contracts import Candle


class DataQualityError(ValueError):
    pass


_SYMBOL = re.compile(r"^[0-9]{6}$")
_REQUIRED = ("open", "high", "low", "close")


@dataclass(frozen=True, slots=True)
class LocalParquetMarketData:
    root: Path
    dataset: str = "trend_cache"

    @property
    def dataset_dir(self) -> Path:
        path = self.root / self.dataset
        if not path.is_dir():
            raise FileNotFoundError(f"行情目录不存在: {path}")
        return path

    def symbols(self, limit: int | None = None) -> list[str]:
        values = sorted(
            path.stem for path in self.dataset_dir.glob("*.parquet")
            if _SYMBOL.fullmatch(path.stem)
        )
        return values[:limit] if limit is not None else values

    def source_path(self, symbol: str) -> Path:
        if not _SYMBOL.fullmatch(symbol):
            raise ValueError(f"证券代码非法: {symbol}")
        path = self.dataset_dir / f"{symbol}.parquet"
        if not path.is_file():
            raise FileNotFoundError(f"找不到行情: {path}")
        return path

    def snapshot_id(self, symbols: list[str]) -> str:
        """基于文件身份生成轻量快照；不对 GB 级数据重复全量哈希。"""
        items = []
        for symbol in sorted(set(symbols)):
            path = self.source_path(symbol)
            stat = path.stat()
            items.append(f"{symbol}:{stat.st_size}:{stat.st_mtime_ns}")
        payload = f"{self.dataset}|" + "|".join(items)
        return "sha256:" + sha256(payload.encode("utf-8")).hexdigest()

    def load(
        self,
        symbol: str,
        start: date | None = None,
        end: date | None = None,
    ) -> list[Candle]:
        path = self.source_path(symbol)
        frame = pd.read_parquet(path)
        missing = set(_REQUIRED) - set(frame.columns)
        if missing:
            raise DataQualityError(f"{symbol} 缺少列: {sorted(missing)}")
        if not isinstance(frame.index, pd.DatetimeIndex):
            if "date" not in frame.columns:
                raise DataQualityError(f"{symbol} 没有 DatetimeIndex/date")
            frame = frame.set_index(pd.to_datetime(frame.pop("date")))
        frame = frame.sort_index()
        if frame.index.has_duplicates:
            raise DataQualityError(f"{symbol} 存在重复交易日")
        if start:
            frame = frame.loc[frame.index.date >= start]
        if end:
            frame = frame.loc[frame.index.date <= end]
        numeric = frame.loc[:, list(_REQUIRED)].apply(pd.to_numeric, errors="coerce")
        valid = numeric.notna().all(axis=1)
        valid &= (numeric["open"] > 0) & (numeric["high"] > 0)
        valid &= (numeric["low"] > 0) & (numeric["close"] > 0)
        valid &= numeric["high"] >= numeric[["open", "close"]].max(axis=1)
        valid &= numeric["low"] <= numeric[["open", "close"]].min(axis=1)
        frame = frame.loc[valid]
        numeric = numeric.loc[valid]
        candles: list[Candle] = []
        for ts, row in numeric.iterrows():
            stamp = datetime.combine(ts.date(), time(15, 0), timezone.utc)
            volume = frame.at[ts, "volume"] if "volume" in frame.columns else None
            volume_value = None if pd.isna(volume) else float(volume)
            amount_column = "amount" if "amount" in frame.columns else "成交额" if "成交额" in frame.columns else None
            amount = frame.at[ts, amount_column] if amount_column else None
            amount_value = None if pd.isna(amount) else float(amount)
            # OHLC is adjusted in trend_cache while raw_prev_close is not;
            # daily-limit checks must compare prices on one adjustment basis.
            previous = frame.loc[:ts, "close"].iloc[-2] if frame.index.get_loc(ts) > 0 else None
            previous_value = None if pd.isna(previous) else float(previous)
            is_st = bool(frame.at[ts, "is_st"]) if "is_st" in frame.columns else False
            candles.append(Candle(
                timestamp=stamp, open=float(row.open), high=float(row.high), low=float(row.low), close=float(row.close),
                volume=volume_value, amount=amount_value, prev_close=previous_value, is_st=is_st,
            ))
        return candles
