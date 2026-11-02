"""验证新口径 notebook 的增长指标与分类边界。"""
import json
import unittest

import numpy as np
import pandas as pd

from paths import PROJECT_ROOT


class IncrementNotebookTests(unittest.TestCase):
    def test_metrics_and_priority_follow_confirmed_baseline_rules(self):
        path = PROJECT_ROOT / "notebooks/采购增量分析/采购增量分析.ipynb"
        cells = json.loads(path.read_text())["cells"]
        months = pd.period_range("2025-10", "2026-09", freq="M")
        matrix = pd.DataFrame([
            [10] * 9 + [50, 100, 200],
            [10] * 12,
            [0] * 9 + [50, 50, 50],
            [5] * 3 + [8] * 3 + [12] * 3 + [14] * 3,
            [20] * 9 + [23] * 3,
            [10] * 9 + [1, 53, -19.6],
            [10] * 9 + [1, 53, 0],
        ], index=pd.Index(["growth", "flat", "no-baseline", "climb", "watch", "negative", "zero"], name="物料编码"), columns=months)
        rows = []
        for material, quantities in matrix.iterrows():
            for month, quantity in quantities.items():
                rows.append({"物料编码": material, "物料名称": material, "规格": "规格", "主单位": "件",
                             "品牌": "品牌", "五级分类": "分类", "出入库日期": month.to_timestamp(),
                             "供应商名称": "外部供应商", "数量": quantity, "采购金额(万)": 1.0})
        rows[1]["品牌"] = "另一品牌"
        scope = {"pd": pd, "np": np, "数量矩阵": matrix, "df1": pd.DataFrame(rows), "结束月": months[-1],
                 "近期非正采购": matrix.iloc[:, -3:].le(0).any(axis=1)}
        for marker in ('指标 = pd.DataFrame(index=数量矩阵.index)', '上期 = 数量矩阵.shift(axis=1)',
                       '全年 = 数量矩阵.sum(axis=1)', '供应商金额 = (df1.groupby(', '属性字段 = ['):
            source = next(''.join(cell['source']) for cell in cells if cell['cell_type'] == 'code'
                          and marker in ''.join(cell['source']))
            exec(compile(source, str(path), 'exec'), scope)
            if marker != '属性字段 = [':
                self.assertTrue(set(scope['df1'].columns).issubset(scope['明细'].columns))
                self.assertEqual(len(scope['明细']), len(scope['df1']))
        report = scope['报表'].set_index('物料编码')
        self.assertNotIn('negative', report.index)
        self.assertNotIn('zero', report.index)
        self.assertAlmostEqual(report.loc['growth', '采购量趋势指数'], (50 + 100 + 200) / 3 / 10 - 1)
        self.assertEqual(report.loc['growth', '谈判优先级'], '优先谈判')
        self.assertEqual(report.loc['flat', '谈判优先级'], '不建议')
        self.assertTrue(pd.isna(report.loc['no-baseline', '采购量趋势指数']))
        self.assertEqual(report.loc['no-baseline', '谈判优先级'], '不建议')
        self.assertEqual(report.loc['climb', '谈判优先级'], '优先谈判')
        self.assertEqual(report.loc['watch', '谈判优先级'], '观察')
        self.assertEqual(report.loc['growth', '最近12个月含税金额万'], 12.0)
        self.assertEqual(report.loc['growth', '品牌'], ['品牌', '另一品牌'])
        self.assertTrue(set(scope['df1'].columns).issubset(report.reset_index().columns))
        self.assertLess(report.reset_index().columns.get_loc('谈判优先级'),
                        report.reset_index().columns.get_loc('供应商名称'))


if __name__ == '__main__':
    unittest.main()
