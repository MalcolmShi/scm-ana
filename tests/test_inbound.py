"""采购入库原始明细按业务日期写入。"""
import unittest
from unittest.mock import patch
import pandas as pd
import storage
from write_data import write_raw_procurement_inbound, write_raw_procurement_inbound_days
from read_data import read_raw_procurement_inbound
from helpers import inbound_row, make_temp_dir


class InboundTests(unittest.TestCase):
    def setUp(self):
        root=make_temp_dir(self); p=patch.object(storage,'DATABASE',root/'test.duckdb'); p.start(); self.addCleanup(p.stop)

    def test_write_multiple_days_preserves_raw_columns(self):
        frames={'2026-09-29':pd.DataFrame([inbound_row(出入库日期='2026-09-29',物料编码='00456')]),
                '2026-09-30':pd.DataFrame([inbound_row()])}
        write_raw_procurement_inbound_days(frames)
        result=read_raw_procurement_inbound('2026-09')
        self.assertEqual(result['物料编码'].tolist(),['00456','00123'])
        self.assertIn('extra_field',result.columns)

    def test_invalid_dates_fail_without_writes(self):
        frame=pd.DataFrame([inbound_row(出入库日期=None)])
        with self.assertRaisesRegex(ValueError,'日期'):
            write_raw_procurement_inbound('2026-09-30',frame)
        self.assertFalse(storage.DATABASE.exists())
