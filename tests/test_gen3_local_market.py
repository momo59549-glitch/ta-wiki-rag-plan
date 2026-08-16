from datetime import date, datetime, timezone
from dataclasses import replace
import tempfile
import unittest
from pathlib import Path

from packages.research.gen3_local_market import LocalMarketSample, LocalParquetFileContract, inspect_local_market_file, make_local_market_contract


def _contract(root: Path, date_column: str = "date"):
    return make_local_market_contract(source_id="fixture", root=str(root), date_column=date_column, open_column="open", high_column="high", low_column="low", close_column="close", volume_column="volume")


def _write(path: Path, rows, date_column="date"):
    import pyarrow as pa
    import pyarrow.parquet as pq
    columns = {date_column: [row[0] for row in rows], "open": [row[1] for row in rows], "high": [row[2] for row in rows], "low": [row[3] for row in rows], "close": [row[4] for row in rows], "volume": [row[5] for row in rows]}
    pq.write_table(pa.table(columns), path)


class LocalMarketTests(unittest.TestCase):
    def test_date_trade_date_and_bounded_sample(self):
        try: import pyarrow  # noqa: F401
        except ImportError: self.skipTest("pyarrow unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); rows=[(date(2026,1,day),10.,11.,9.,10.5,100) for day in (1,2,3)]
            path=root/"000001.parquet"; _write(path,rows)
            result=inspect_local_market_file(path,_contract(root),2)
            self.assertTrue(result.truncated); self.assertEqual(result.row_count,2); result.verify()
            other=root/"000002.parquet"; _write(other,rows,"trade_date")
            self.assertEqual(inspect_local_market_file(other,_contract(root,"trade_date"),3).row_count,3)
            self.assertEqual({p.name for p in root.iterdir()},{"000001.parquet","000002.parquet"})

    def test_contract_file_and_value_fail_closed(self):
        try: import pyarrow  # noqa: F401
        except ImportError: self.skipTest("pyarrow unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); good=[(date(2026,1,1),10.,11.,9.,10.5,100)]
            path=root/"000001.parquet"; _write(path,good); contract=_contract(root)
            with self.assertRaisesRegex(ValueError,"contract_hash"):
                LocalParquetFileContract(**{**contract.__dict__,"contract_hash":"sha256:"+"0"*64}).verify()
            with self.assertRaisesRegex(ValueError,"computed"):
                make_local_market_contract(**contract.__dict__)
            with self.assertRaisesRegex(ValueError,"multiple canonical"):
                make_local_market_contract(source_id="x",root=str(root),date_column="date",open_column="open",high_column="open",low_column="low",close_column="close",volume_column="volume")
            _write(root/"bad.parquet",good)
            with self.assertRaisesRegex(ValueError,"six-digit"):
                inspect_local_market_file(root/"bad.parquet",contract,1)
            sub=root/"sub"; sub.mkdir(); _write(sub/"000003.parquet",good)
            with self.assertRaisesRegex(ValueError,"direct child"):
                inspect_local_market_file(sub/"000003.parquet",contract,1)
            bad=[(date(2026,1,1),10.,8.,9.,10.5,100)]
            _write(root/"000004.parquet",bad)
            with self.assertRaisesRegex(ValueError,"OHLC"):
                inspect_local_market_file(root/"000004.parquet",contract,1)
            for limit in (True,0,10001):
                with self.assertRaisesRegex(ValueError,"max_rows"):
                    inspect_local_market_file(path,contract,limit)

    def test_null_bool_and_session_type_fail_closed(self):
        try: import pyarrow  # noqa: F401
        except ImportError: self.skipTest("pyarrow unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); contract=_contract(root)
            for name, row, message in (
                ("000001",[(date(2026,1,1),None,11.,9.,10.,1)],"finite"),
                ("000002",[(date(2026,1,1),10.,11.,9.,10.,True)],"finite"),
                ("000003",[(datetime(2026,1,1,tzinfo=timezone.utc),10.,11.,9.,10.,1)],"timezone"),
                ("000004",[("2026-01-01",10.,11.,9.,10.,1)],"session"),
            ):
                _write(root/(name+".parquet"),row)
                with self.assertRaisesRegex(ValueError,message):
                    inspect_local_market_file(root/(name+".parquet"),contract,1)

    def test_sample_direct_tampering_fails(self):
        try: import pyarrow  # noqa: F401
        except ImportError: self.skipTest("pyarrow unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); path=root/"000001.parquet"; _write(path,[(date(2026,1,1),10.,11.,9.,10.,1)])
            sample=inspect_local_market_file(path,_contract(root),1)
            for changed in (
                {"sample_hash":"sha256:"+"0"*64}, {"mapping_hash":"sha256:"+"0"*64},
                {"row_count":2}, {"row_hashes":(sample.row_hashes[0],sample.row_hashes[0])},
                {"row_hashes":("sha256:"+"0"*64,)}, {"min_session":date(2026,1,2)}, {"truncated":1},
            ):
                with self.assertRaises(ValueError):
                    replace(sample,**changed).verify()

    def test_empty_and_bad_sessions_fail_closed(self):
        try: import pyarrow as pa; import pyarrow.parquet as pq
        except ImportError: self.skipTest("pyarrow unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); contract=_contract(root)
            pq.write_table(pa.table({"date":pa.array([],type=pa.date32()),"open":pa.array([],type=pa.float64()),"high":pa.array([],type=pa.float64()),"low":pa.array([],type=pa.float64()),"close":pa.array([],type=pa.float64()),"volume":pa.array([],type=pa.int64())}),root/"000001.parquet")
            with self.assertRaisesRegex(ValueError,"zero"):
                inspect_local_market_file(root/"000001.parquet",contract,1)
            _write(root/"000002.parquet",[(date(2026,1,2),10.,11.,9.,10.,1),(date(2026,1,1),10.,11.,9.,10.,1)])
            with self.assertRaisesRegex(ValueError,"strictly"):
                inspect_local_market_file(root/"000002.parquet",contract,2)
