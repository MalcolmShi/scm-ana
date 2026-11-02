"""采购来源：Excel 输入及原始入库。"""
import unittest
from unittest.mock import patch
import pandas as pd
import storage
from sources.procurement_inbound import read_export, split_by_business_date, sync_export
from helpers import inbound_row, make_temp_dir


class InboundTests(unittest.TestCase):
    def setUp(self):
        root=make_temp_dir(self);p=patch.object(storage,'DATABASE',root/'test.duckdb');p.start();self.addCleanup(p.stop)

    def test_import_records_and_preserves_fields(self):
        frame=pd.DataFrame([inbound_row(),inbound_row(出入库日期='2026-09-29',物料编码='00456')])
        info={'files':['example.xlsx'],'file_rows':{'example.xlsx':2}}
        with patch('sources.procurement_inbound.read_export',return_value=(frame,info)):
            result=sync_export()
        self.assertEqual(result['days'],['2026-09-29','2026-09-30'])
        saved=storage.read_partitions('raw','procurement_inbound','2026-09-29','2026-09-30')
        self.assertEqual(saved['物料编码'].tolist(),['00456','00123'])
        self.assertIn('extra_field',saved)

    def test_bad_business_date_fails_before_writing(self):
        frame=pd.DataFrame([inbound_row(出入库日期=None)])
        with patch('sources.procurement_inbound.read_export',return_value=(frame,{'files':[],'file_rows':{}})):
            with self.assertRaisesRegex(ValueError,'有效出入库日期'):
                sync_export()
        self.assertFalse(storage.DATABASE.exists())

    def test_excel_multiple_files(self):
        root=make_temp_dir(self)
        pd.DataFrame([inbound_row(物料编码='A')]).to_excel(root/'入库明细1.xlsx',index=False)
        pd.DataFrame([inbound_row(物料编码='B')]).to_excel(root/'入库明细2.xlsx',index=False)
        frame,info=read_export(root)
        self.assertEqual(frame['物料编码'].tolist(),['A','B'])
        self.assertEqual(info['rows'],2)
