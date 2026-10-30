"""采购入库：Excel 导出导入、按窗口取数、字段画像。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

import archive
import read_data
from analysis.procurement_increment import normalize_columns
from helpers import inbound_row, make_temp_dir
from sources import procurement_inbound as inbound

STAMP = "2026-10-28T07:30:00+08:00"


class InboundTestCase(unittest.TestCase):
    """公共准备：临时归档根目录 + 一行样例记录。"""

    def setUp(self):
        root = make_temp_dir(self)
        p = patch.object(archive, "DATA_ROOT", root)
        p.start()
        self.addCleanup(p.stop)
        self.raw = pd.DataFrame([inbound_row()])

    def path(self, layer="raw", day="2026-09-30"):
        return archive.snapshot_path(layer, inbound.SOURCE_ID, day)

    def sync(self, raw=None, dates=None, dry_run=False):
        """把 raw 当成导出文件内容，走一遍导入流程。"""
        raw = self.raw if raw is None else raw
        info = {"files": ["入库明细（2026）.xlsx"],
                "file_rows": {"入库明细（2026）.xlsx": len(raw)},
                "rows": len(raw), "columns": list(raw.columns)}
        with patch.object(inbound, "read_export", return_value=(raw, info)):
            return inbound.sync_export(dates=dates, dry_run=dry_run)


class ImportTests(InboundTestCase):
    def test_one_file_per_business_date(self):
        raw = pd.concat([
            self.raw,
            self.raw.assign(出入库日期="2026-09-29", 物料编码="00456", 数量=3),
        ], ignore_index=True)
        meta = self.sync(raw)
        self.assertEqual(meta["days"], ["2026-09-29", "2026-09-30"])
        day29 = pd.read_parquet(self.path(day="2026-09-29"))
        day30 = pd.read_parquet(self.path(day="2026-09-30"))
        self.assertEqual((len(day29), len(day30)), (1, 1))
        self.assertEqual(day29.loc[0, "物料编码"], "00456")
        # 目录是业务日期，转换时间写在表内
        self.assertEqual(day29.loc[0, "_fetched_at"], meta["fetched_at"])
        self.assertEqual(day29.loc[0, "_source"], inbound.SOURCE_ID)

    def test_rows_without_business_date_are_skipped(self):
        raw = pd.concat([self.raw, self.raw.assign(出入库日期=None, 物料编码="00999")],
                        ignore_index=True)
        meta = self.sync(raw)
        self.assertEqual(meta["days"], ["2026-09-30"])
        self.assertEqual(meta["skipped_rows"], 1)
        self.assertEqual(len(pd.read_parquet(self.path())), 1)

    def test_dry_run_writes_nothing(self):
        self.sync(dry_run=True)
        self.assertFalse(self.path().exists())

    def test_dates_filter(self):
        raw = pd.concat([
            self.raw,
            self.raw.assign(出入库日期="2026-09-29", 物料编码="00456"),
        ], ignore_index=True)
        meta = self.sync(raw, dates=["2026-09-29"])
        self.assertEqual(meta["days"], ["2026-09-29"])
        self.assertFalse(self.path(day="2026-09-30").exists())

    def test_raw_keeps_export_columns(self):
        self.sync()
        raw = pd.read_parquet(self.path())
        self.assertIn("数量", raw.columns)
        self.assertIn("采购金额(万)", raw.columns)
        self.assertNotIn("数量合计", raw.columns)
        self.assertNotIn("采购入库年份", raw.columns)

    def test_same_day_rerun_replaces_that_day(self):
        self.sync()
        first = self.path().read_bytes()
        self.sync(self.raw.assign(数量=48))
        self.assertNotEqual(first, self.path().read_bytes())
        self.assertEqual(len(pd.read_parquet(self.path())), 1)

    def test_read_export_merges_files_and_reports_rows(self):
        root = make_temp_dir(self)
        pd.DataFrame([inbound_row(物料编码="A001")]).to_excel(root / "入库明细（2025）.xlsx", index=False)
        pd.DataFrame([inbound_row(物料编码="B002")]).to_excel(root / "入库明细（2026）.xlsx", index=False)
        frame, info = inbound.read_export(root)
        self.assertEqual(len(frame), 2)
        self.assertEqual(info["files"], ["入库明细（2025）.xlsx", "入库明细（2026）.xlsx"])
        self.assertEqual(info["file_rows"], {"入库明细（2025）.xlsx": 1, "入库明细（2026）.xlsx": 1})
        self.assertEqual(list(frame.columns), list(inbound_row().keys()))

    def test_read_export_rejects_missing_or_empty(self):
        root = make_temp_dir(self)
        with self.assertRaises(inbound.LocalDataError):
            inbound.list_excel_files(root / "不存在")
        with self.assertRaises(inbound.LocalDataError):
            inbound.list_excel_files(root)
        pd.DataFrame([]).to_excel(root / "入库明细（空）.xlsx", index=False)
        with self.assertRaises(inbound.LocalDataError):
            inbound.read_export(root)


class ReadTests(InboundTestCase):
    def test_single_argument_reads_whole_period(self):
        raw = pd.concat([
            self.raw,
            self.raw.assign(出入库日期="2026-08-31", 物料编码="00001"),
            self.raw.assign(出入库日期="2026-10-01", 物料编码="00002"),
        ], ignore_index=True)
        self.sync(raw)
        self.assertEqual(len(read_data.read_inbound("2026-09-30")), 1)   # 当天
        self.assertEqual(len(read_data.read_inbound("2026-09")), 1)      # 当月
        self.assertEqual(len(read_data.read_inbound("2026")), 3)         # 当年
        self.assertEqual(len(read_data.read_inbound()), 3)               # 全部

    def test_two_arguments_read_range(self):
        raw = pd.concat([
            self.raw,
            self.raw.assign(出入库日期="2026-08-31", 物料编码="00001"),
            self.raw.assign(出入库日期="2026-09-29", 物料编码="00456"),
            self.raw.assign(出入库日期="2026-10-01", 物料编码="00002"),
        ], ignore_index=True)
        self.sync(raw)
        self.assertEqual(len(read_data.read_inbound("2026-09-29", "2026-09-30")), 2)
        self.assertEqual(len(read_data.read_inbound("2026-09", "2026-10")), 3)
        self.assertEqual(len(read_data.read_inbound("2026-01", "2026-09-15")), 1)  # 精度可混用
        with self.assertRaises(archive.DataStoreError):                            # 窗口内无数据
            read_data.read_inbound("2026-01", "2026-01")

    def test_window_is_row_level_not_folder_level(self):
        self.sync()
        day30 = pd.read_parquet(self.path())
        mixed = pd.concat([day30, self.raw.assign(出入库日期="2026-09-29", 物料编码="00789")],
                          ignore_index=True)
        archive.write_snapshot(mixed, "raw", inbound.SOURCE_ID, "2026-09-30", check_fetched_at=False)

        only_30 = read_data.read_inbound("2026-09-30")
        self.assertEqual(only_30["出入库日期"].unique().tolist(), ["2026-09-30"])
        self.assertNotIn("00789", only_30["物料编码"].tolist())
        # 不带日期列的取数，依然按出入库日期裁剪
        narrowed = read_data.read_inbound("2026-09-30", columns=["物料编码"])
        self.assertEqual(list(narrowed.columns), ["物料编码"])
        self.assertEqual(narrowed["物料编码"].tolist(), ["00123"])

    def test_meta_traces_the_window(self):
        self.sync()
        frame, meta = read_data.read_inbound_meta("2026-09")
        self.assertEqual(len(frame), 1)
        self.assertEqual(meta["window_start"], "2026-09-01")
        self.assertEqual(meta["window_end"], "2026-09-30")
        self.assertEqual(meta["days"], ["2026-09-30"])
        self.assertEqual(meta["layer"], "raw")
        self.assertEqual(meta["data_month_range"]["start"], "2026-09")

    def test_processed_layer_wins_when_present(self):
        self.sync()
        base = pd.read_parquet(self.path())
        archive.write_snapshot(base.assign(数量合计=base["数量"] * 2), "processed",
                               inbound.SOURCE_ID, "2026-09-30", check_fetched_at=False)
        frame, meta = read_data.read_inbound_meta("2026-09")
        self.assertEqual(meta["layer"], "processed")
        self.assertEqual(meta["layers"], {"processed": 1})
        self.assertEqual(frame.loc[0, "数量合计"], 24)

    def test_archive_overview_counts_rows(self):
        raw = pd.concat([
            self.raw,
            self.raw.assign(出入库日期="2026-09-29", 物料编码="00456"),
        ], ignore_index=True)
        self.sync(raw)
        overview = read_data.archive_overview()
        self.assertEqual(overview["归档日"].tolist(), ["2026-09-29", "2026-09-30"])
        self.assertEqual(int(overview["行数"].sum()), 2)
        self.assertEqual(overview["层"].unique().tolist(), ["raw"])

    def test_select_days(self):
        raw = pd.concat([
            self.raw.assign(出入库日期="2026-08-31", 物料编码="00001"),
            self.raw,
        ], ignore_index=True)
        self.sync(raw)
        self.assertEqual(read_data.select_days("2026-09"), ["2026-09-30"])
        self.assertEqual(read_data.select_days("2026-09-01", "2026-09-30"), ["2026-09-30"])
        self.assertEqual(len(read_data.select_days()), 2)

    def test_bad_bounds_raise_readable_errors(self):
        self.sync()
        for value in ("2026-13", "2026-9", "abc", "2026-09-31"):
            with self.subTest(value=value), self.assertRaises(archive.DataStoreError):
                read_data.read_inbound(value)
        with self.assertRaises(archive.DataStoreError):
            read_data.read_inbound("2026-10", "2026-01")

    def test_window_resolution(self):
        self.assertEqual(read_data.read_inbound.__name__, "read_inbound")
        self.assertEqual(inbound.resolve_window(), ("0000-01-01", "9999-12-31"))
        self.assertEqual(inbound.resolve_window("2026-09-30"), ("2026-09-30", "2026-09-30"))
        self.assertEqual(inbound.resolve_window("2026-09"), ("2026-09-01", "2026-09-30"))
        self.assertEqual(inbound.resolve_window("2026"), ("2026-01-01", "2026-12-31"))
        self.assertEqual(inbound.resolve_window("", "2026-09"), ("0000-01-01", "2026-09-30"))


class ProfileTests(InboundTestCase):
    def test_field_profile_lists_every_column(self):
        frame = pd.DataFrame([inbound_row(), inbound_row(供应商名称=None)])
        profile = inbound.field_profile(frame)
        self.assertEqual(profile["字段"].tolist(), list(frame.columns))
        supplier = profile.set_index("字段").loc["供应商名称"]
        self.assertEqual(supplier["非空数"], 1)
        self.assertEqual(supplier["非空率"], 0.5)
        self.assertEqual(supplier["分组"], "供应商")
        self.assertEqual(profile.set_index("字段").loc["数量", "分组"], "数量与金额")

    def test_month_range_prefers_business_date(self):
        frame = pd.DataFrame([inbound_row(), inbound_row(出入库日期="2026-08-31")])
        coverage = inbound.month_range(frame)
        self.assertEqual((coverage["start"], coverage["end"], coverage["count"]), ("2026-08", "2026-09", 2))


class ColumnMappingTests(unittest.TestCase):
    """分析侧的字段名对齐，只改名不改值。"""

    def test_raw_names_are_mapped(self):
        frame = normalize_columns(pd.DataFrame([inbound_row()]))
        self.assertIn("数量合计", frame.columns)
        self.assertIn("采购入库年份", frame.columns)
        self.assertIn("采购入库月份", frame.columns)
        self.assertEqual(frame.loc[0, "数量合计"], 12)

    def test_cleaned_names_are_kept(self):
        base = pd.DataFrame([inbound_row()]).rename(
            columns={"数量": "数量合计", "年份": "采购入库年份", "月份": "采购入库月份"})
        self.assertEqual(list(normalize_columns(base).columns), list(base.columns))


if __name__ == "__main__":
    unittest.main()
