"""采购增量：分档和近期采购门槛。"""
import unittest
import pandas as pd
from analysis.procurement_increment import analyze


class IncrementTests(unittest.TestCase):
    def test_confirmed_thresholds_and_tax_inclusive_amount(self):
        months=pd.period_range('2025-10','2026-09',freq='M')
        rows=[]
        for code, values in [('growth',[10]*9+[20,30,40]),('zero',[10]*9+[20,30,0]),('negative',[10]*9+[20,30,-1]),('low',[10]*12)]:
            for month, qty in zip(months,values):
                rows.append({'物料编码':code,'物料名称':code,'规格':'规格','主单位':'件','供应商名称':'外部供应商','供应商类型':'外部单位','板块':'检验','出入库日期':month.start_time,'数量':qty,'采购金额(万)':.5 if code=='low' else 1.0})
        tables,meta=analyze(pd.DataFrame(rows),'2026-09')
        self.assertEqual(tables['detail']['物料编码'].tolist(),['growth'])
        self.assertEqual(tables['detail']['谈判优先级'].tolist(),['优先谈判'])
        self.assertEqual(set(tables['monthly']['物料编码']),{'growth','zero','negative'})
        self.assertEqual(meta['excluded_materials'],2)
