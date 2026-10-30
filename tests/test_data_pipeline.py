"""离线验证来源读取、原样归档和现有读取入口。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from pandas.testing import assert_frame_equal

import archive
import pipeline
import sources
from analysis.procurement_increment import normalize_columns
from sources import local_excel


def inbound_row(**changes):
    """一行本地导出的原始记录，列名与上游 Excel 保持一致。"""
    return {
        "分析类型": "入库",
        "年份": 2026,
        "月份": 9,
        "板块": "检验",
        "公司编码": "3004",
        "公司名称": "浙江迪安医疗器械有限公司",
        "仓库名称": "合格品仓",
        "出入库类型名称": "PO接收",
        "出入库日期": "2026-09-30",
        "物料编码": "00123",
        "物料名称": "重碳酸盐检测试剂盒",
        "规格": "250测试",
        "主单位": "盒",
        "数量": 12,
        "采购单价": 141.62,
        "采购金额(万)": 0.169944,
        "税率": 0.13,
        "采购不含税单价": 125.327434,
        "采购不含税金额(万)": 0.15039292,
        "供应商名称": "罗氏诊断产品公司",
        "一级分类": "试剂",
        "extra_field": "保留未知字段",
        **changes,
    }


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        p = patch.object(archive, "DATA_ROOT", root)
        p.start()
        self.addCleanup(p.stop)
        self.day = "2026-10-28"
        self.fetched_at = "2026-10-28T07:30:00+08:00"
        self.raw = pd.DataFrame([inbound_row()]).assign(
            _source=pipeline.DEFAULT_SOURCE, _fetched_at=self.fetched_at,
        )

    def path(self, layer, day=None):
        return archive.snapshot_path(layer, pipeline.DEFAULT_SOURCE, day or self.day)

    def sync(self, raw=None, dry_run=False, stamp=None, mode="fetch-day", dates=None):
        if raw is None:
            raw = self.raw
        if stamp:
            raw = raw.assign(_fetched_at=stamp)
        else:
            stamp = self.fetched_at
        meta = {"source": pipeline.DEFAULT_SOURCE, "snapshot_date": archive.archive_date(stamp), "fetched_at": stamp}
        with patch.object(pipeline, "fetch_raw", return_value=(raw, meta)):
            return pipeline.sync_source("采购入库", dry_run=dry_run, mode=mode, dates=dates)

    def test_sync_writes_raw_unchanged(self):
        self.sync()
        raw = pd.read_parquet(self.path("raw"))
        # raw 是原样归档：导出文件的列名和取值都不动，只加来源元数据。
        assert_frame_equal(raw, self.raw)
        self.assertIn("数量", raw.columns)
        self.assertIn("采购金额(万)", raw.columns)
        self.assertNotIn("数量合计", raw.columns)
        self.assertNotIn("采购入库年份", raw.columns)

    def test_read_metadata_and_coverage(self):
        self.sync()
        frame, meta = pipeline.load_source()
        self.assertEqual(meta["layer"], "raw")
        self.assertEqual(meta["snapshot_date"], self.day)
        self.assertEqual(meta["fetched_at"], self.fetched_at)
        self.assertEqual(meta["rows"], 1)
        self.assertEqual(meta["data_month_range"]["months"], ["2026-09"])
        self.assertEqual(frame.loc[0, "数量"], 12)
        self.assertAlmostEqual(frame.loc[0, "采购不含税金额(万)"], 0.15039292)

    def test_processed_takes_priority_when_business_side_writes_it(self):
        self.sync()
        base = pd.read_parquet(self.path("raw"))
        processed = base.assign(数量合计=base["数量"] * 2)
        archive.write_snapshot(processed, "processed", pipeline.DEFAULT_SOURCE, self.day)
        frame, meta = pipeline.load_source()
        self.assertEqual(meta["layer"], "processed")
        self.assertEqual(frame.loc[0, "数量合计"], 24)

    def test_column_selection_keeps_metadata(self):
        self.sync()
        frame, meta = pipeline.load_source(columns=["物料编码"])
        self.assertEqual(list(frame.columns), ["物料编码"])
        self.assertEqual(meta["fetched_at"], self.fetched_at)

    def test_dry_run_and_invalid_input_do_not_replace_files(self):
        self.sync(dry_run=True)
        self.assertFalse(self.path("raw").exists())
        self.sync()
        path = self.path("raw")
        before = path.read_bytes()
        with self.assertRaises(archive.DataStoreError):
            self.sync(pd.DataFrame())
        self.assertEqual(path.read_bytes(), before)

    def test_daily_snapshots_latest_and_exact_history(self):
        self.sync(stamp="2026-10-25T07:30:00+08:00")
        historical = self.path("raw", "2026-10-25").read_bytes()
        newer = self.raw.assign(数量=24)
        self.sync(newer, stamp="2026-10-26T07:30:00+08:00")
        self.assertEqual(self.path("raw", "2026-10-25").read_bytes(), historical)
        self.assertEqual(archive.list_snapshots(pipeline.DEFAULT_SOURCE), ["2026-10-25", "2026-10-26"])
        latest, meta = pipeline.load_source()
        self.assertEqual(meta["snapshot_date"], "2026-10-26")
        self.assertEqual(len(latest), 1)  # 每日全量快照不能拼接
        self.assertEqual(latest.loc[0, "数量"], 24)
        previous, meta = pipeline.load_source(snapshot_date="2026-10-25")
        self.assertEqual(previous.loc[0, "数量"], 12)
        with self.assertRaises(archive.DataStoreError):
            pipeline.load_source(snapshot_date="2026-10-24")

    def test_same_day_rerun_replaces_only_that_day(self):
        self.sync(stamp="2026-10-27T07:30:00+08:00")
        previous_day = self.path("raw", "2026-10-27").read_bytes()
        self.sync()
        self.sync(self.raw.assign(数量=48), stamp="2026-10-28T15:30:00+08:00")
        latest, meta = pipeline.load_source()
        self.assertEqual(latest.loc[0, "数量"], 48)
        self.assertEqual(meta["fetched_at"], "2026-10-28T15:30:00+08:00")
        self.assertEqual(self.path("raw", "2026-10-27").read_bytes(), previous_day)

    def test_business_date_mode_archives_each_day(self):
        raw = pd.concat([
            self.raw,
            self.raw.assign(出入库日期="2026-09-29", 物料编码="00456", 数量=3),
        ], ignore_index=True)
        meta = self.sync(raw, mode="business-date")
        self.assertEqual(meta["days"], ["2026-09-29", "2026-09-30"])
        day29 = pd.read_parquet(self.path("raw", "2026-09-29"))
        day30 = pd.read_parquet(self.path("raw", "2026-09-30"))
        self.assertEqual(len(day29), 1)
        self.assertEqual(len(day30), 1)
        self.assertEqual(day29.loc[0, "物料编码"], "00456")
        self.assertEqual(day29.loc[0, "数量"], 3)
        # 归档日是业务日期，转换时间仍记录在表内。
        self.assertEqual(day29.loc[0, "_fetched_at"], self.fetched_at)
        self.assertNotIn("2026-10-28", archive.list_snapshots(pipeline.DEFAULT_SOURCE))

    def test_business_date_mode_can_filter_dates(self):
        raw = pd.concat([
            self.raw,
            self.raw.assign(出入库日期="2026-09-29", 物料编码="00456"),
        ], ignore_index=True)
        meta = self.sync(raw, mode="business-date", dates=["2026-09-29"])
        self.assertEqual(meta["days"], ["2026-09-29"])
        self.assertFalse(self.path("raw", "2026-09-30").exists())

    def test_generic_archive_supports_other_sources_and_validates_paths(self):
        frame = pd.DataFrame({"adjustment_id": ["P001"]})
        path = archive.write_snapshot(frame, "raw", "price-adjustment", "2026-10-25")
        self.assertEqual(path.relative_to(archive.DATA_ROOT).as_posix(), "raw/price-adjustment/2026/2026-10/2026-10-25/data.parquet")
        loaded, _ = archive.read_snapshot("raw", "price-adjustment")
        assert_frame_equal(loaded, frame)
        for source, day in [("../other", self.day), (pipeline.DEFAULT_SOURCE, "2026-02-30"), (pipeline.DEFAULT_SOURCE, "20261025")]:
            with self.subTest(source=source, day=day), self.assertRaises(archive.DataStoreError):
                archive.snapshot_path("raw", source, day)
        self.assertEqual(archive.archive_date("2026-10-25T18:00:00+00:00"), "2026-10-26")
        with self.assertRaises(archive.DataStoreError):
            archive.write_snapshot(self.raw, "raw", pipeline.DEFAULT_SOURCE, "2026-10-25")

    def test_atomic_write_failure_preserves_previous_day_file(self):
        self.sync()
        original = self.path("raw").read_bytes()
        with patch.object(pd.DataFrame, "to_parquet", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                archive.write_snapshot(self.raw, "raw", pipeline.DEFAULT_SOURCE, self.day)
        self.assertEqual(self.path("raw").read_bytes(), original)
        self.assertEqual(list(self.path("raw").parent.glob("*.part")), [])

    def test_another_source_uses_the_same_sync_and_archive(self):
        class ExampleSource:
            source_id = "test-source"
            aliases = ()
            date_column = None

            def load(self):
                return pd.DataFrame([{"record_id": "X001"}]), {"files": []}

            @staticmethod
            def coverage(frame):
                return {}

        with patch.dict(sources.REGISTRY, {"test-source": ExampleSource}):
            meta = pipeline.sync_source("test-source")
            frame, loaded_meta = pipeline.load_source("test-source")
        self.assertEqual(frame.loc[0, "record_id"], "X001")
        self.assertEqual(loaded_meta["snapshot_date"], meta["snapshot_date"])
        self.assertTrue(archive.snapshot_path("raw", "test-source", meta["snapshot_date"]).exists())


class LocalExcelTests(unittest.TestCase):
    """本地导出文件的读取与合并。"""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def write_excel(self, name, rows):
        path = self.root / name
        pd.DataFrame(rows).to_excel(path, index=False)
        return path

    def test_merges_files_without_changing_columns(self):
        self.write_excel("入库明细（2025）.xlsx", [inbound_row(物料编码="A001")])
        self.write_excel("入库明细（2026）.xlsx", [inbound_row(物料编码="B002")])
        frame, info = local_excel.load_local_inbound(self.root)
        self.assertEqual(len(frame), 2)
        self.assertEqual(info["files"], ["入库明细（2025）.xlsx", "入库明细（2026）.xlsx"])
        self.assertEqual(info["file_rows"], {"入库明细（2025）.xlsx": 1, "入库明细（2026）.xlsx": 1})
        # 列名和取值保持导出文件原样，不加工。
        self.assertIn("数量", frame.columns)
        self.assertNotIn("数量合计", frame.columns)
        self.assertEqual(list(frame.columns), list(inbound_row().keys()))

    def test_missing_directory_and_empty_file_raise(self):
        with self.assertRaises(local_excel.LocalDataError):
            local_excel.list_local_files(self.root / "不存在")
        with self.assertRaises(local_excel.LocalDataError):
            local_excel.list_local_files(self.root)
        self.write_excel("入库明细（空）.xlsx", [])
        with self.assertRaises(local_excel.LocalDataError):
            local_excel.load_local_inbound(self.root)

    def test_pipeline_reads_local_directory(self):
        self.write_excel("入库明细（2026）.xlsx", [inbound_row()])
        with patch.object(local_excel, "LOCAL_DATA_ROOT", self.root):
            frame, meta = pipeline.load_from_source()
        self.assertEqual(frame.loc[0, "数量"], 12)
        self.assertEqual(meta["rows"], 1)


class ColumnMappingTests(unittest.TestCase):
    """分析侧的字段名对齐，不改数值。"""

    def test_raw_names_are_mapped_for_analysis(self):
        frame = normalize_columns(pd.DataFrame([inbound_row()]))
        self.assertIn("数量合计", frame.columns)
        self.assertIn("采购入库年份", frame.columns)
        self.assertIn("采购入库月份", frame.columns)
        self.assertEqual(frame.loc[0, "数量合计"], 12)

    def test_cleaned_names_are_kept_as_is(self):
        base = pd.DataFrame([inbound_row()]).rename(
            columns={"数量": "数量合计", "年份": "采购入库年份", "月份": "采购入库月份"})
        frame = normalize_columns(base)
        self.assertEqual(list(frame.columns), list(base.columns))


if __name__ == "__main__":
    unittest.main()
