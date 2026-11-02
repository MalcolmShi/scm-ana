"""清洗层：规则逐条断言 + 归档落盘。合成数据，不依赖真实归档。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from clean.procurement_inbound import clean_inbound
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

    def test_negative_price_uses_return_price_when_available(self):
        raw = self.raw.copy()
        raw.loc[0, ["采购单价", "购退单价"]] = [-10, 88.5]
        raw.loc[1, ["采购单价", "购退单价"]] = [-10, None]
        cleaned, audit = clean_inbound(raw)
        self.assertEqual(cleaned.loc[0, "采购单价"], 88.5)
        self.assertEqual(cleaned.loc[1, "采购单价"], -10)
        self.assertEqual(audit["price_filled"], 2)

    def test_missing_finance_supplier_column_fails(self):
        with self.assertRaisesRegex(KeyError, "财务内部外供应商类型"):
            clean_inbound(self.raw.drop(columns="财务内部外供应商类型"))



if __name__ == "__main__":
    unittest.main()
