from __future__ import annotations

from datetime import date
from hashlib import sha256
import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from packages.research.gen2_discovery import canonical_hash
from packages.research.gen2_file_provider import LocalParquetFutureSource, ManifestPitProvider, write_source_revision_manifest


def _ref(root: Path, path: Path) -> dict:
    return {"schema_version": "gen2-local-file-ref/v1", "path": path.relative_to(root).as_posix(), "size": path.stat().st_size, "sha256": "sha256:" + sha256(path.read_bytes()).hexdigest()}


def _fixture(root: Path) -> Path:
    idx = pd.date_range("2026-09-01", periods=3, freq="B"); values=np.arange(3., dtype=float)
    asset = pd.DataFrame({"date":idx,"open":100+values,"high":101+values,"low":99+values,"close":100.5+values,"prev_close":100+values,"volume":1000.,"amount":10000.,"is_st":False})
    bench = pd.DataFrame({"date":idx,"open":200+values,"close":201+values})
    asset.to_parquet(root / "asset.parquet", index=False); bench.to_parquet(root / "benchmark.parquet", index=False)
    (root / "calendar.json").write_text(json.dumps({"schema_version":"gen2-local-calendar-manifest/v1","dates":[x.date().isoformat() for x in idx]}),encoding="utf-8")
    (root / "pit.json").write_text(json.dumps({"schema_version":"gen2-local-pit-manifest/v1","memberships":{"2026-09-02":["AAA"]}}),encoding="utf-8")
    manifest={"schema_version":"gen2-local-source-revision-manifest/v1","parent_revision_hash":None,"parent_available_through":None,"available_from":"2026-09-01","available_through":"2026-09-03","asset_dataset_id":"assets","benchmark_dataset_id":"benchmark","calendar_id":"calendar","pit_lineage_id":"pit","asset_files":[{"symbol":"AAA","file":_ref(root,root/"asset.parquet")}],"benchmark_file":_ref(root,root/"benchmark.parquet"),"calendar_manifest":_ref(root,root/"calendar.json"),"pit_manifest":_ref(root,root/"pit.json"),"historical_prefix_hash":canonical_hash({"prefix":"empty"}),"created_at":"2026-08-01T00:00:00+00:00"}
    path=root/"revision.json"; path.write_text(json.dumps(manifest),encoding="utf-8"); return path


class FileProviderTests(unittest.TestCase):
    def test_explicit_parquet_manifest_normalizes_and_has_no_identity_reads(self):
        with TemporaryDirectory() as temp:
            root=Path(temp); manifest=_fixture(root); source=LocalParquetFutureSource(manifest,allowed_data_root=root)
            with patch("packages.research.gen2_file_provider.pd.read_parquet") as read:
                source.identity(); self.assertFalse(read.called)
            cal=source.calendar(date(2026,9,1),date(2026,9,3)); self.assertEqual(len(cal),3)
            self.assertEqual(list(source.asset_frame("AAA",date(2026,9,1),date(2026,9,3)).columns)[0],"open")
            self.assertEqual(ManifestPitProvider(source).active_on(date(2026,9,2)),{"AAA"})

    def test_hash_and_path_escape_fail_closed(self):
        with TemporaryDirectory() as temp:
            root=Path(temp); manifest=_fixture(root); payload=json.loads(manifest.read_text(encoding="utf-8")); payload["asset_files"][0]["file"]["sha256"]="sha256:"+"0"*64; manifest.write_text(json.dumps(payload),encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"size/hash"):
                LocalParquetFutureSource(manifest,allowed_data_root=root).calendar(date(2026,9,1),date(2026,9,3))
        with TemporaryDirectory() as temp:
            root=Path(temp); manifest=_fixture(root); payload=json.loads(manifest.read_text(encoding="utf-8")); payload["benchmark_file"]["path"]="../outside.parquet"; manifest.write_text(json.dumps(payload),encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"escapes"):
                LocalParquetFutureSource(manifest,allowed_data_root=root)

    def test_numeric_pit_and_write_once_guards(self):
        with TemporaryDirectory() as temp:
            root=Path(temp); manifest=_fixture(root); payload=json.loads(manifest.read_text(encoding="utf-8"))
            frame=pd.read_parquet(root/"asset.parquet"); frame.loc[0,"high"]=1.; frame.to_parquet(root/"asset.parquet",index=False); payload["asset_files"][0]["file"]=_ref(root,root/"asset.parquet"); manifest.write_text(json.dumps(payload),encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"structural"):
                LocalParquetFutureSource(manifest,allowed_data_root=root).calendar(date(2026,9,1),date(2026,9,3))
        with TemporaryDirectory() as temp:
            root=Path(temp); manifest=_fixture(root); payload=json.loads(manifest.read_text(encoding="utf-8")); pit=root/"pit.json"; pit.write_text(json.dumps({"schema_version":"gen2-local-pit-manifest/v1","memberships":{"2026-09-02":["UNKNOWN"]}}),encoding="utf-8"); payload["pit_manifest"]=_ref(root,pit); manifest.write_text(json.dumps(payload),encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"unknown"):
                LocalParquetFutureSource(manifest,allowed_data_root=root).calendar(date(2026,9,1),date(2026,9,3))
            output=root/"write-once.json"; write_source_revision_manifest(output,payload)
            with self.assertRaises(FileExistsError): write_source_revision_manifest(output,payload)

    def test_second_revision_checks_actual_old_content_prefix(self):
        with TemporaryDirectory() as temp:
            root=Path(temp); first_path=_fixture(root); first=LocalParquetFutureSource(first_path,allowed_data_root=root); first.calendar(date(2026,9,1),date(2026,9,3)); parent=first.identity()
            base=json.loads(first_path.read_text(encoding="utf-8")); day=pd.Timestamp("2026-09-04")
            for name in ("asset.parquet","benchmark.parquet"):
                frame=pd.read_parquet(root/name); extra=frame.iloc[[-1]].copy(); extra.loc[:,"date"]=day; frame=pd.concat([frame,extra],ignore_index=True); frame.to_parquet(root/name,index=False)
            cal=root/"calendar.json"; cp=json.loads(cal.read_text(encoding="utf-8")); cp["dates"].append("2026-09-04"); cal.write_text(json.dumps(cp),encoding="utf-8")
            second=deepcopy(base); second.update({"parent_revision_hash":parent["revision_hash"],"parent_available_through":"2026-09-03","available_from":"2026-09-04","available_through":"2026-09-04","historical_prefix_hash":parent["prefix_hash"]})
            second["asset_files"][0]["file"]=_ref(root,root/"asset.parquet"); second["benchmark_file"]=_ref(root,root/"benchmark.parquet"); second["calendar_manifest"]=_ref(root,cal)
            second_path=root/"second.json"; second_path.write_text(json.dumps(second),encoding="utf-8")
            LocalParquetFutureSource(second_path,allowed_data_root=root).calendar(date(2026,9,1),date(2026,9,4))
            frame=pd.read_parquet(root/"asset.parquet"); frame.loc[0,"volume"]+=10; frame.to_parquet(root/"asset.parquet",index=False); second["asset_files"][0]["file"]=_ref(root,root/"asset.parquet"); second_path.write_text(json.dumps(second),encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"historical prefix"):
                LocalParquetFutureSource(second_path,allowed_data_root=root).calendar(date(2026,9,1),date(2026,9,4))


if __name__ == "__main__": unittest.main()
