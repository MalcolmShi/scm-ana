#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分析层口径测试：供应商范围、新引入档位、趋势指数除零、均价列唯一。

用合成数据跑 ``clean_and_filter`` / ``build_report``，不依赖 ``data/`` 归档，
所以任何一次口径改动都会在这里先暴露。
"""
from __future__ import annotations

import unittest
from contextlib import contextmanager
from unittest.mock import patch

import numpy as np
import pandas as pd

from analysis.procurement_increment import (
    Config,
    NEW_MATERIAL_LABEL,
    SUPPLIER_TYPE_COLUMN,
    run_analysis,
)

MONTHS = pd.period_range("2025-10", "2026-09", freq="M")
EXTERNAL = "外部单位"
RELATED = "内部关联单位"


@contextmanager
def reading_from(frame: pd.DataFrame):
    """把取数入口换成内存里的合成数据，测试不碰 data/ 下的真实归档。"""
    meta = {
        "source": "synthetic",
        "data_file": "synthetic",
        "snapshot_date": "2026-09-30",
        "data_month_range": {},
    }
    with patch("analysis.procurement_increment.load_source_data", return_value=(frame, meta)):
        yield


def _rows(material_code: str, name: str, quantities, amounts, supplier: str,
          supplier_type: str = EXTERNAL) -> pd.DataFrame:
    """按月份序列生成一个物料的明细行。"""
    return pd.DataFrame({
        "板块": "检验",
        "物料编码": material_code,
        "物料名称": name,
        "规格": "测试规格",
        "品牌": "测试品牌",
        "五级分类": "测试分类",
        "供应商名称": supplier,
        SUPPLIER_TYPE_COLUMN: supplier_type,
        "年份": [p.year for p in MONTHS],
        "月份": [p.month for p in MONTHS],
        "数量": list(quantities),
        "采购不含税金额(万)": list(amounts),
    })


def _series(base: float, tail, amount: float):
    """前 9 个月 base，最近 3 个月按 tail 递增；金额保持恒定。"""
    return [base] * 9 + list(tail), [amount] * 12


class Fixture:
    """三个物料：持续增长、新引入、关联交易，都过 5 万金额门槛。"""

    def __init__(self) -> None:
        growth_q, growth_a = _series(10, [50, 100, 200], 1.0)   # 趋势 10.67，末月环比 +100%
        new_q, new_a = _series(0, [50, 50, 50], 2.0)            # 前 9 个月为 0，无基期
        related_q, related_a = _series(10, [50, 100, 200], 1.0)  # 同样增长，但供应商是内部关联

        self.frame = pd.concat([
            _rows(1001, "稳定增长物料", growth_q, growth_a, "外部供应商甲"),
            _rows(1002, "新引入物料", new_q, new_a, "外部供应商乙"),
            _rows(1003, "关联交易物料", related_q, related_a, "迪安诊断技术集团", RELATED),
        ], ignore_index=True)

    def run(self, **kwargs):
        with reading_from(self.frame):
            return run_analysis(Config(**kwargs))

    def report(self, **kwargs) -> pd.DataFrame:
        return self.run(**kwargs)["report"]


class SupplierScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Fixture()

    def test_related_party_excluded_by_default(self):
        result = self.fixture.run()
        codes = set(result["report"]["物料编码"])

        self.assertIn(1001, codes)
        self.assertNotIn(1003, codes, "关联交易物料不应进入报告")
        self.assertEqual(result["meta"]["supplier_scope"], "仅外部单位")

    def test_excluded_volume_is_tracked(self):
        """剔除量必须能被报告读到，不能无声消失。"""
        meta = self.fixture.run()["meta"]

        self.assertEqual(meta["supplier_excluded_rows"], 12)
        self.assertAlmostEqual(meta["supplier_excluded_amount"], 12.0, places=6)

    def test_external_only_can_be_switched_off(self):
        codes = set(self.fixture.run(external_only=False)["report"]["物料编码"])

        self.assertIn(1003, codes, "关闭范围过滤后关联物料应重新出现")
        self.assertEqual(self.fixture.run(external_only=False)["meta"]["supplier_scope"], "全部供应商")


class NewMaterialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.report = Fixture().report()
        self.new = self.report.loc[self.report["物料编码"] == 1002].iloc[0]
        self.growth = self.report.loc[self.report["物料编码"] == 1001].iloc[0]

    def test_no_baseline_material_is_labelled_new(self):
        self.assertEqual(self.new["谈判优先级"], NEW_MATERIAL_LABEL)

    def test_new_material_trend_index_is_empty_not_infinite(self):
        """基期为 0 时必须留空：inf 既满足 20% 门槛又会排到榜首。"""
        self.assertTrue(np.isnan(self.new["采购量趋势指数"]))
        self.assertFalse(np.isinf(self.new["采购量趋势指数"]))

    def test_new_material_does_not_take_a_negotiation_slot(self):
        slots = self.report.loc[self.report["谈判优先级"] == "优先谈判", "物料编码"]

        self.assertNotIn(1002, set(slots))

    def test_growing_material_keeps_numeric_index(self):
        # 前3月均值 10，最近3月均值 116.67 → 趋势指数 10.67
        self.assertAlmostEqual(self.growth["采购量趋势指数"], 116.666666 / 10 - 1, places=5)
        self.assertEqual(self.growth["谈判优先级"], "优先谈判")

    def test_new_material_sorts_last(self):
        self.assertEqual(self.report["谈判优先级"].iloc[-1], NEW_MATERIAL_LABEL)

    def test_report_has_no_infinite_values(self):
        numeric = self.report.select_dtypes(include="number")

        self.assertFalse(np.isinf(numeric.to_numpy()).any(), "报告里不应出现 inf")


class MonthlyColumnTests(unittest.TestCase):
    def test_single_price_column_in_yuan(self):
        """曾经同时存在 月加权平均价(万元) 和 月加权平均价(元)，单位冲突。"""
        monthly = Fixture().run()["monthly"]
        price_columns = [c for c in monthly.columns if "加权平均价" in c]

        self.assertEqual(price_columns, ["月加权平均价(元)"])

    def test_price_unit_is_yuan(self):
        monthly = Fixture().run()["monthly"]
        row = monthly.loc[monthly["数量"] > 0].iloc[0]

        # 1 万元 / 10 件 = 1000 元/件
        self.assertAlmostEqual(row["月加权平均价(元)"], (row["采购不含税金额(万)"] * 10000) / row["数量"])


class PrioritySummaryTests(unittest.TestCase):
    def test_all_four_labels_present_in_summary(self):
        summary = Fixture().run()["priority_summary"]
        expected = ["优先谈判", "观察", "不建议", NEW_MATERIAL_LABEL]

        self.assertEqual(list(summary["谈判优先级"]), expected)
        self.assertEqual(int(summary["物料数"].sum()), 2, "默认口径下只有 2 个外部物料")

    def test_missing_tier_is_zero_filled(self):
        summary = Fixture().run()["priority_summary"].set_index("谈判优先级")

        self.assertEqual(int(summary.loc["不建议", "物料数"]), 0)


if __name__ == "__main__":
    unittest.main()
