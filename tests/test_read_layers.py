"""显式读取数据层和来源过期保护。"""
import unittest
from unittest.mock import patch
import pandas as pd
from pandas.testing import assert_frame_equal
import storage
from read_data import read_raw_procurement_inbound, read_processed_procurement_inbound, read_processed_procurement_inbound_meta
from write_data import write_raw_procurement_inbound, write_processed_procurement_inbound
from helpers import inbound_row, make_temp_dir


class LayerTests(unittest.TestCase):
    def setUp(self):
        root=make_temp_dir(self);p=patch.object(storage,'DATABASE',root/'test.duckdb');p.start();self.addCleanup(p.stop)
        self.raw=pd.DataFrame([inbound_row()]); self.cleaned=self.raw.assign(数量=99)
        write_raw_procurement_inbound('2026-09-30',self.raw)
        self.fingerprint=storage.partitions('raw','procurement_inbound','2026-09-30','2026-09-30')[0]['fingerprint']

    def test_layers_are_explicit(self):
        with self.assertRaisesRegex(ValueError,'数据集尚未建立'):
            read_processed_procurement_inbound('2026-09')
        write_processed_procurement_inbound('2026-09-30',self.cleaned,raw_fingerprint=self.fingerprint)
        assert_frame_equal(read_raw_procurement_inbound('2026-09'),self.raw,check_dtype=False)
        assert_frame_equal(read_processed_procurement_inbound('2026-09'),self.cleaned,check_dtype=False)
        _,meta=read_processed_procurement_inbound_meta('2026-09')
        self.assertEqual(meta['coverage']['input_versions'],'已核实')
        self.assertEqual(list(read_raw_procurement_inbound('2026-09',columns=['物料编码']).columns),['物料编码'])

    def test_raw_update_invalidates_processed(self):
        write_processed_procurement_inbound('2026-09-30',self.cleaned,raw_fingerprint=self.fingerprint)
        write_raw_procurement_inbound('2026-09-30',self.raw.assign(数量=42))
        with self.assertRaisesRegex(ValueError,'已过期'):
            read_processed_procurement_inbound('2026-09')
        with self.assertRaisesRegex(ValueError,'raw 已变化'):
            write_processed_procurement_inbound('2026-09-30',self.cleaned,raw_fingerprint=self.fingerprint)

    def test_known_missing_partition_fails(self):
        write_processed_procurement_inbound('2026-09-30',self.cleaned,raw_fingerprint=self.fingerprint)
        write_raw_procurement_inbound('2026-09-29',self.raw.assign(出入库日期='2026-09-29'))
        with self.assertRaisesRegex(ValueError,'2026-09-29'):
            read_processed_procurement_inbound('2026-09')

    def test_date_windows_without_rows_return_empty_with_columns(self):
        write_processed_procurement_inbound('2026-09-30',self.cleaned,raw_fingerprint=self.fingerprint)
        for reader in (read_raw_procurement_inbound, read_processed_procurement_inbound):
            for args in [('2024',), ('2026-08',), ('2026-09-29',), ('2026-08-01', '2026-08-31')]:
                result = reader(*args)
                self.assertTrue(result.empty, args)
                self.assertEqual(list(result.columns), list(self.raw.columns))
            self.assertEqual(list(reader('2026-08', columns=['物料编码']).columns), ['物料编码'])
            self.assertEqual(len(reader('2026-09')), 1)
            self.assertEqual(len(reader('2026-09-29', '2026-09-30')), 1)
        _, meta = read_processed_procurement_inbound_meta('2026-08')
        self.assertEqual(meta['rows'], 0)
        self.assertEqual(meta['coverage']['input_versions'], '未核实')
