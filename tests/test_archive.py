"""通用存储：不同数据集隔离、分区写入和字段约束。"""
import unittest
from unittest.mock import patch
import pandas as pd
from pandas.testing import assert_frame_equal
import storage
from helpers import make_temp_dir


class StorageTests(unittest.TestCase):
    def setUp(self):
        root = make_temp_dir(self)
        p = patch.object(storage, 'DATABASE', root / 'test.duckdb')
        p.start(); self.addCleanup(p.stop)

    def test_independent_datasets_and_dates(self):
        first = pd.DataFrame({'code':['001'], 'amount':[1.0]})
        second = pd.DataFrame({'id':[42], 'count':[3]})
        storage.replace_partition('raw', 'orders', '2026-09-30', first)
        storage.replace_partition('raw', 'inventory', '2026-09-30', second)
        assert_frame_equal(storage.read_partitions('raw','orders','2026-09-01','2026-09-30'),first,check_dtype=False)
        assert_frame_equal(storage.read_partitions('raw','inventory','2026-09-01','2026-09-30'),second,check_dtype=False)
        self.assertEqual(storage.partitions('raw','orders','2026-09-01','2026-09-30')[0]['rows'],1)

    def test_rejects_schema_change_without_replacing_existing_data(self):
        frame = pd.DataFrame({'code':['001'], 'amount':[1.0]})
        storage.replace_partition('raw','orders','2026-09-30',frame)
        with self.assertRaisesRegex(ValueError,'字段不一致'):
            storage.replace_partition('raw','orders','2026-09-30',frame.drop(columns='amount'))
        assert_frame_equal(storage.read_partitions('raw','orders','2026-09-30','2026-09-30'),frame,check_dtype=False)
        with self.assertRaises(ValueError):
            storage.replace_partition('raw','orders; drop schema raw','2026-09-30',frame)

    def test_multi_day_write_is_atomic(self):
        first = pd.DataFrame({'code':['001'], 'amount':[1.0]})
        storage.replace_partition('raw','orders','2026-09-29',first)
        with self.assertRaisesRegex(ValueError,'字段不一致'):
            storage.replace_partitions('raw','orders',{
                '2026-09-29': first.assign(amount=2.0),
                '2026-09-30': first.drop(columns='amount'),
            })
        self.assertEqual(storage.read_partitions('raw','orders','2026-09-29','2026-09-30')['amount'].tolist(),[1.0])
        self.assertEqual(len(storage.partitions('raw','orders','2026-09-29','2026-09-30')),1)
