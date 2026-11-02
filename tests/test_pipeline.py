"""从 raw 到 processed、分析和 outputs 的贯通校验。"""
import unittest
import sys
from unittest.mock import patch
import pandas as pd
import storage
from clean.procurement_inbound import clean_inbound
from analysis.procurement_increment import analyze
from read_data import read_raw_procurement_inbound_meta, read_processed_procurement_inbound_meta, read_outputs_meta
from write_data import write_raw_procurement_inbound_days, write_processed_procurement_inbound_days, write_outputs
from helpers import make_temp_dir
from test_clean_inbound import dirty_row, with_standard_columns
from sources.procurement_inbound import sync_export
from scripts import build_processed, archive_procurement_increment


class PipelineTests(unittest.TestCase):
    def setUp(self):
        root=make_temp_dir(self); p=patch.object(storage,'DATABASE',root/'test.duckdb');p.start();self.addCleanup(p.stop)

    def test_clean_analyze_archive_and_detect_stale_input(self):
        months=pd.period_range('2025-10','2026-09',freq='M')
        raw=with_standard_columns(pd.DataFrame([
            dirty_row(出入库日期=str(month.start_time.date()),物料编码='M1',数量=qty,
                      板块='检验',采购类型名称='定向采购合同')
            for month,qty in zip(months,[10]*9+[20,30,40])]))
        raw['采购金额(万)']=1.
        days={day:part.reset_index(drop=True) for day,part in raw.groupby('出入库日期')}
        write_raw_procurement_inbound_days(days)
        cleaned={}; fingerprints={}
        for day in days:
            source,meta=read_raw_procurement_inbound_meta(day)
            cleaned[day],_=clean_inbound(source)
            fingerprints[day]=meta['partitions'][0]['fingerprint']
        write_processed_procurement_inbound_days(cleaned,raw_fingerprints=fingerprints)
        processed,meta=read_processed_procurement_inbound_meta('2025-10','2026-09')
        self.assertEqual(meta['coverage']['input_versions'],'已核实')
        self.assertTrue(processed['供应商类型'].eq('外部单位').all())
        self.assertTrue(processed['采购单价'].eq(88.5).all())
        tables,_=analyze(processed,'2026-09')
        self.assertEqual(tables['detail']['谈判优先级'].tolist(),['优先谈判'])
        source={'layer':'processed','dataset':'procurement_inbound','start':'2025-10-01','end':'2026-09-30','partitions':meta['partitions']}
        write_outputs('procurement_increment','2026-09-30',tables,{'inputs':[source]})
        result,metadata=read_outputs_meta('procurement_increment','2026-09-30')
        self.assertEqual(len(result),1)
        self.assertEqual(metadata['input_status'],'已核对')
        day=next(iter(days))
        write_raw_procurement_inbound_days({day:days[day].assign(数量=11)})
        with self.assertRaisesRegex(ValueError,'已过期'):
            read_processed_procurement_inbound_meta('2025-10','2026-09')
        with self.assertRaisesRegex(ValueError,'已变化'):
            read_outputs_meta('procurement_increment','2026-09-30')

    def test_source_to_monthly_archive(self):
        root = make_temp_dir(self)
        months = pd.period_range('2025-10', '2026-09', freq='M')
        raw = with_standard_columns(pd.DataFrame([
            dirty_row(出入库日期=str(month.start_time.date()), 物料编码='M1', 数量=qty,
                      来块='检验', 采购类型名称='定向采购合同')
            for month, qty in zip(months, [10] * 9 + [20, 30, 40])]))
        raw['采购金额(万)'] = 1.
        raw.to_excel(root / '入库明细测试.xlsx', index=False, sheet_name='入库明细（含单价）')
        with patch('sources.procurement_inbound.local_data_dir', return_value=root):
            self.assertEqual(sync_export()['rows'], 12)
        with patch.object(sys, 'argv', ['build_processed']):
            build_processed.main()
        with patch.object(sys, 'argv', ['archive_procurement_increment', '2026-09']):
            archive_procurement_increment.main()
        report, metadata = read_outputs_meta('procurement_increment', '2026-09-30')
        self.assertEqual(report['物料编码'].tolist(), ['M1'])
        self.assertEqual(metadata['input_status'], '已核对')
