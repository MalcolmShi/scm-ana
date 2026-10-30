"""存储层：parquet 归档的路径规则与读写。"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from pandas.testing import assert_frame_equal

import archive
from helpers import make_temp_dir

STAMP = "2026-10-28T07:30:00+08:00"


def frame(rows=1):
    return pd.DataFrame({"物料编码": [f"00{i}" for i in range(rows)], "数量": range(rows)})


class ArchiveStorageTests(unittest.TestCase):
    def setUp(self):
        self.root = make_temp_dir(self)
        p = patch.object(archive, "DATA_ROOT", self.root)
        p.start()
        self.addCleanup(p.stop)

    def path(self, layer="raw", source="procurement-inbound", day="2026-09-30") -> Path:
        return archive.snapshot_path(layer, source, day)

    def test_path_layout(self):
        self.assertEqual(self.path().relative_to(self.root).as_posix(),
                         "raw/procurement-inbound/2026/2026-09/2026-09-30/data.parquet")

    def test_write_and_read_roundtrip(self):
        data = frame(3)
        archive.write_snapshot(data, "raw", "procurement-inbound", "2026-09-30")
        loaded, meta = archive.read_snapshot("raw", "procurement-inbound", "2026-09-30")
        assert_frame_equal(loaded, data)
        self.assertEqual(meta["rows"], 3)
        self.assertEqual(meta["snapshot_date"], "2026-09-30")

    def test_row_count_reads_metadata_only(self):
        archive.write_snapshot(frame(5), "raw", "procurement-inbound", "2026-09-30")
        self.assertEqual(archive.row_count("raw", "procurement-inbound", "2026-09-30"), 5)
        with self.assertRaises(archive.DataStoreError):
            archive.row_count("raw", "procurement-inbound", "2026-09-29")

    def test_list_snapshots_and_latest(self):
        for day in ("2026-09-29", "2026-09-30"):
            archive.write_snapshot(frame(1), "raw", "procurement-inbound", day)
        self.assertEqual(archive.list_snapshots("procurement-inbound"), ["2026-09-29", "2026-09-30"])
        self.assertEqual(archive.latest_snapshot_date("procurement-inbound"), "2026-09-30")

    def test_source_meta_must_match(self):
        data = frame(1).assign(_source="other-source", _fetched_at=STAMP)
        with self.assertRaises(archive.DataStoreError):
            archive.write_snapshot(data, "raw", "procurement-inbound", "2026-09-30")

    def test_fetched_at_check_can_be_skipped_for_business_date(self):
        data = frame(1).assign(_source="procurement-inbound", _fetched_at=STAMP)
        with self.assertRaises(archive.DataStoreError):
            archive.write_snapshot(data, "raw", "procurement-inbound", "2026-09-30")
        archive.write_snapshot(data, "raw", "procurement-inbound", "2026-09-30", check_fetched_at=False)
        self.assertTrue(self.path().is_file())

    def test_atomic_write_failure_keeps_previous_file(self):
        archive.write_snapshot(frame(1), "raw", "procurement-inbound", "2026-09-30")
        original = self.path().read_bytes()
        with patch.object(pd.DataFrame, "to_parquet", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                archive.write_snapshot(frame(2), "raw", "procurement-inbound", "2026-09-30")
        self.assertEqual(self.path().read_bytes(), original)
        self.assertEqual(list(self.path().parent.glob("*.part")), [])

    def test_invalid_input_is_rejected(self):
        cases = [
            ("raw", "../other", "2026-10-28"),
            ("raw", "Procurement-Inbound", "2026-10-28"),
            ("raw", "procurement-inbound", "2026-02-30"),
            ("raw", "procurement-inbound", "20261025"),
            ("raw", "procurement-inbound", "2026-10"),
            ("gold", "procurement-inbound", "2026-10-28"),
        ]
        for layer, source, day in cases:
            with self.subTest(layer=layer, source=source, day=day):
                with self.assertRaises(archive.DataStoreError):
                    archive.snapshot_path(layer, source, day)


if __name__ == "__main__":
    unittest.main()
