"""清洗层：规则逐条断言 + 归档落盘。合成数据，不依赖真实归档。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

import archive
from clean import build_processed, clean_inbound
from clean.procurement_inbound import STANDARD_COLUMNS, UPDATED_AT_COLUMN
from helpers import inbound_row, make_temp_dir


def dirty_row(**changes):
    """一行带各类待清洗问题的入库记录（列名与 raw 日档一致）。"""
    fields = dict(
        物料名称="  试剂\t名称\n",            # 尾随空白 + 控制符
        财务内部外供应商类型=None,            # 供应商类型缺失 → 填外部单位
        采购单价=None, 购退单价=88.5,          # 含税单价缺失 ← 购退单价补
        采购类型名称="定向采购合同",           # 去"合同"后缀 → 定向采购
    )
    fields.update(changes)
    return inbound_row(**fields)


def with_standard_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """样例行没覆盖到的标准字段补占位值（真实 raw 全有，这里只为过列校验）。

    「供应商类型」除外：它由清洗从「财务内部外供应商类型」改名而来，预填会撞列。
    """
    numeric = {"数量", "税率"}
    for col in STANDARD_COLUMNS:
        if col != "供应商类型" and col not in frame.columns:
            frame[col] = 0 if col in numeric else "样例"
    return frame


class CleanTests(unittest.TestCase):
    def setUp(self):
        raw = pd.DataFrame([
            dirty_row(),
            dirty_row(物料编码="00456", 采购类型名称="普通采购合同"),
            dirty_row(物料编码="00789", 采购类型名称=None),
        ])
        self.raw = with_standard_columns(raw)

    def test_standard_rules(self):
        cleaned, audit = clean_inbound(self.raw, fetched_at="2026-11-02T14:00:00+08:00")
        # 列：27 标准字段 + updated_at，源列（购退单价/采购类型名称/财务内部外供应商类型）不进底表
        self.assertEqual(list(cleaned.columns), STANDARD_COLUMNS + [UPDATED_AT_COLUMN])
        # 文本脏字符
        self.assertEqual(cleaned.loc[0, "物料名称"], "试剂名称")
        # 供应商类型填外部
        self.assertTrue((cleaned["供应商类型"] == "外部单位").all())
        # 含税单价 ← 购退单价
        self.assertEqual(cleaned.loc[0, "采购单价"], 88.5)
        # 采购模式归一
        self.assertEqual(list(cleaned["采购模式"]), ["定向采购", "其他", "未标注"])
        # 行数金额双守恒（无剔除项）
        self.assertEqual((audit["rows_before"], audit["rows_after"]), (3, 3))
        self.assertAlmostEqual(audit["amount_before_wan"], audit["amount_after_wan"], places=1)
        # 溯源列
        self.assertTrue((cleaned[UPDATED_AT_COLUMN] == "2026-11-02T14:00:00+08:00").all())

    def test_duplicate_rows_kept_by_default(self):
        raw = pd.concat([self.raw, self.raw.iloc[[0]]], ignore_index=True)
        cleaned, audit = clean_inbound(raw)
        self.assertEqual(len(cleaned), 4)
        self.assertEqual(audit["duplicate_rows"], 1)
        self.assertEqual(audit["duplicates_dropped"], 0)
        cleaned, audit = clean_inbound(raw, drop_duplicates=True)
        self.assertEqual(len(cleaned), 3)
        self.assertEqual(audit["duplicates_dropped"], 1)

    def test_bad_date_raises(self):
        raw = pd.concat([self.raw, self.raw.assign(出入库日期="not-a-date")], ignore_index=True)
        with self.assertRaises(ValueError):
            clean_inbound(raw)


class BuildProcessedTests(unittest.TestCase):
    def setUp(self):
        root = make_temp_dir(self)
        p = patch.object(archive, "DATA_ROOT", root)
        p.start()
        self.addCleanup(p.stop)
        self.raw = with_standard_columns(pd.DataFrame(
            [dirty_row(), dirty_row(出入库日期="2026-09-29", 物料编码="00456")]))

    def write_raw(self):
        for day, part in self.raw.groupby("出入库日期"):
            archive.write_snapshot(part.reset_index(drop=True), "raw", "procurement-inbound", day,
                                   check_fetched_at=False)

    def test_build_writes_processed_per_day(self):
        self.write_raw()
        meta = build_processed()
        self.assertEqual(meta["days"], ["2026-09-29", "2026-09-30"])
        self.assertEqual(meta["status"], "synced")
        self.assertEqual(meta["totals"]["rows_after"], 2)
        cleaned = pd.read_parquet(archive.snapshot_path("processed", "procurement-inbound", "2026-09-29"))
        self.assertEqual(list(cleaned.columns), STANDARD_COLUMNS + [UPDATED_AT_COLUMN])
        self.assertEqual(cleaned.loc[0, "供应商类型"], "外部单位")
        # 逐日清洗 = 整窗清洗：金额守恒
        self.assertAlmostEqual(meta["totals"]["amount_before_wan"],
                               meta["totals"]["amount_after_wan"], places=1)

    def test_dry_run_writes_nothing(self):
        self.write_raw()
        meta = build_processed(dry_run=True)
        self.assertEqual(meta["status"], "dry-run")
        self.assertEqual(archive.list_snapshots("procurement-inbound", "processed"), [])

    def test_explicit_layer_reads(self):
        """显式接口：read_raw / read_processed 各读各的层，缺档不静默换层。"""
        self.write_raw()
        from sources.procurement_inbound import read_processed, read_raw
        frame, meta = read_raw("2026-09-29")
        self.assertEqual(meta["layer"], "raw")
        self.assertIn("财务内部外供应商类型", frame.columns)
        with self.assertRaises(archive.DataStoreError):
            read_processed("2026-09-29")          # 还没建底表，直接报错不回退 raw
        build_processed(start="2026-09-29")
        frame, meta = read_processed("2026-09-29")
        self.assertEqual(meta["layer"], "processed")
        self.assertIn("供应商类型", frame.columns)


if __name__ == "__main__":
    unittest.main()
