from datetime import date
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd

from packages.market_data.tushare_daily import sync_tushare_incremental


class TushareIncrementalTests(unittest.TestCase):
    def test_trade_date_is_resumable_and_writes_overlay(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = root / "universe.jsonl"
            manifest.write_text(json.dumps({"symbol": "000001", "ts_code": "000001.SZ", "name": "平安银行"}, ensure_ascii=False) + "\n", encoding="utf-8")

            def fake_request(token, api_name, params, timeout, retries):
                if api_name == "trade_cal":
                    return [{"cal_date": "20260805", "is_open": 1}]
                if api_name == "daily":
                    return [{"ts_code": "000001.SZ", "trade_date": "20260805", "open": 11, "high": 12, "low": 10, "close": 11.5, "pre_close": 10.8, "vol": 100, "amount": 200}]
                return [{"ts_code": "000001.SZ", "trade_date": "20260805", "adj_factor": 3.0}]

            kwargs = dict(manifest_path=manifest, output_dataset_dir=root / "overlay", checkpoint_path=root / "checkpoint.json", progress_path=root / "progress.json", start=date(2026, 8, 5), end=date(2026, 8, 5), token="test", delay_seconds=0)
            with patch("packages.market_data.tushare_daily._request", side_effect=fake_request) as request:
                first = sync_tushare_incremental(**kwargs)
                second = sync_tushare_incremental(**kwargs)
            frame = pd.read_parquet(root / "overlay" / "000001.parquet")
            self.assertEqual(first["completed"], 1)
            self.assertEqual(second["completed"], 1)
            self.assertEqual(len(frame), 1)
            self.assertEqual(request.call_count, 4)


if __name__ == "__main__":
    unittest.main()
