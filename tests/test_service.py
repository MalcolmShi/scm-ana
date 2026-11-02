"""看板下钻：逐笔范围、月份过滤、分页和结果过期。"""
import unittest
from unittest.mock import patch
import pandas as pd
import storage
from services import procurement_increment as service
from helpers import make_temp_dir


class DrilldownTests(unittest.TestCase):
    def setUp(self):
        root=make_temp_dir(self); p=patch.object(storage,'DATABASE',root/'test.duckdb');p.start();self.addCleanup(p.stop)
        self.day='2026-09-15'
        rows=[{'出入库日期':self.day,'物料编码':'M1','物料名称':'试剂','规格':'规格','主单位':'盒','供应商名称':'甲',
               '公司名称':'公司A','数量':2.,'采购单价':10.,'采购金额(万)':1.,'板块':'检验','供应商类型':'外部单位'},
              {'出入库日期':self.day,'物料编码':'M1','物料名称':'试剂','规格':'规格','主单位':'盒','供应商名称':'乙',
               '公司名称':'公司B','数量':3.,'采购单价':20.,'采购金额(万)':2.,'板块':'检验','供应商类型':'外部单位'},
              {'出入库日期':self.day,'物料编码':'M1','物料名称':'试剂','规格':'规格','主单位':'盒','供应商名称':'内部',
               '公司名称':'公司C','数量':100.,'采购单价':1.,'采购金额(万)':100.,'板块':'检验','供应商类型':'内部单位'}]
        self.frame=pd.DataFrame(rows)
        storage.replace_partition('processed','procurement_inbound',self.day,self.frame)
        parts=storage.partitions('processed','procurement_inbound','2025-10-01','2026-09-30')
        meta={'inputs':[{'layer':'processed','dataset':'procurement_inbound','start':'2025-10-01','end':'2026-09-30','partitions':parts}]}
        tables={'detail':pd.DataFrame({'物料编码':['M1']}),'monthly':pd.DataFrame({'物料编码':['M1'],'采购月':['2026-09'],'月采购量':[5.],'月含税金额万':[3.]})}
        storage.replace_outputs('procurement_increment','2026-09-30',tables,meta)

    def test_filtered_details_reconcile_with_monthly_and_paginate(self):
        monthly=service.read_monthly('2026-09','M1')
        page,total=service.read_details('2026-09','M1',purchase_month='2026-09',size=1)
        page2,total2=service.read_details('2026-09','M1',purchase_month='2026-09',size=1,page=2)
        self.assertEqual((total,total2),(2,2))
        self.assertEqual(pd.concat([page,page2])['数量'].sum(),monthly['月采购量'].iloc[0])
        self.assertEqual(pd.concat([page,page2])['采购金额(万)'].sum(),monthly['月含税金额万'].iloc[0])
        subset,n=service.read_details('2026-09','M1',supplier='乙')
        self.assertEqual((n,subset.iloc[0]['公司名称']),(1,'公司B'))
        with self.assertRaises(ValueError): service.read_details('2026-09','M2')
        with self.assertRaises(ValueError): service.read_details('2026-09','M1',purchase_month='2024-01')

    def test_changed_input_invalidates_existing_report(self):
        storage.replace_partition('processed','procurement_inbound',self.day,self.frame.assign(数量=4.))
        with self.assertRaisesRegex(ValueError,'已变化'):
            service.read_report('2026-09')
