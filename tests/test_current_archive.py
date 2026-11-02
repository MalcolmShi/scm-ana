"""新口径分析、清洗衔接与多表按周期归档。"""
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd
from pandas.testing import assert_frame_equal

from analysis.procurement_increment import analyze
from clean.procurement_inbound import clean_inbound
from test_clean_inbound import dirty_row, with_standard_columns
from helpers import make_temp_dir
from scripts.archive_procurement_increment import resolve_end_month, validate_months


class MonthlyScheduleTests(unittest.TestCase):
    def test_last_month_uses_shanghai_time_and_crosses_year(self):
        self.assertEqual(str(resolve_end_month(now=datetime(2026, 1, 1, tzinfo=timezone.utc))), '2025-12')
        self.assertEqual(str(resolve_end_month(now=datetime(2026, 1, 31, 17, tzinfo=timezone.utc))), '2026-01')
        self.assertEqual(str(resolve_end_month('2026-09')), '2026-09')
        for bad in ['2026-9', '2026-13', '2026-09-30']:
            with self.assertRaises(ValueError):
                resolve_end_month(bad)

    def test_missing_month_is_not_treated_as_zero(self):
        months = pd.period_range('2025-10', '2026-09', freq='M')
        days = [str(month.start_time.date()) for month in months]
        validate_months(days, pd.Period('2026-09'))
        with self.assertRaisesRegex(ValueError, '2026-10'):
            validate_months(days, pd.Period('2026-10'))


class CurrentAnalysisTests(unittest.TestCase):
    def test_cleaned_rows_reach_confirmed_analysis(self):
        months = pd.period_range('2025-10', '2026-09', freq='M')
        rows = []
        for code, recent in [('positive', [20, 30, 40]), ('zero', [20, 30, 0]), ('negative', [20, 30, -1])]:
            for month, amount in zip(months, [10] * 9 + recent):
                rows.append(dirty_row(物料编码=code, 出入库日期=str(month.start_time.date()), 数量=amount,
                                      板块='检验', 采购金额_万=1))
        raw = with_standard_columns(pd.DataFrame(rows))
        raw['采购金额(万)'] = 1.0
        clean, _ = clean_inbound(raw)
        tables, meta = analyze(clean, '2026-09')
        self.assertEqual(set(tables['detail']['物料编码']), {'positive'})
        self.assertEqual(set(tables['monthly']['物料编码']), {'positive', 'zero', 'negative'})
        self.assertEqual(len(tables['monthly']), 36)
        self.assertEqual(meta['excluded_materials'], 2)
        self.assertEqual(tables['detail'].iloc[0]['谈判优先级'], '优先谈判')

