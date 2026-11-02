"""按业务周期事务保存多张结果表。"""
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal
import storage
from helpers import make_temp_dir


class OutputTests(unittest.TestCase):
    def setUp(self):
        root=make_temp_dir(self)
        p=patch.object(storage,'DATABASE',root/'test.duckdb');p.start();self.addCleanup(p.stop)

    def test_scene_and_period_are_independent(self):
        a={'detail':pd.DataFrame({'code':['A'], 'values':[[1,2]]}), 'monthly':pd.DataFrame({'month':['2026-09'],'total':[2.]})}
        original = {'scope':'test', 'parameters': {'threshold': 5.0, 'flags': [True, None], 'empty': []}}
        storage.replace_outputs('demand','2026-09-30',a,original)
        b={'detail':pd.DataFrame({'code':['B']})}
        storage.replace_outputs('savings','2026-09-30',b,{})
        actual,meta=storage.read_outputs('demand','2026-09-30','monthly')
        assert_frame_equal(actual,a['monthly'],check_dtype=False)
        self.assertEqual(meta['business_end'],'2026-09-30')
        self.assertEqual(meta['parameters'],original['parameters'])
        self.assertEqual(meta['archive_date'],meta['generated_at'][:10])
        self.assertEqual(storage.read_outputs('savings','2026-09-30')[0]['code'].tolist(),['B'])
        changed={**a,'monthly':pd.DataFrame({'month':['2026-09'],'total':[3.]})}
        storage.replace_outputs('demand','2026-09-30',changed,{})
        self.assertEqual(storage.read_outputs('demand','2026-09-30','monthly')[0]['total'].tolist(),[3.])
        storage.replace_outputs('demand','2026-09-30',{'detail': a['detail']},{})
        with self.assertRaisesRegex(ValueError,'结果表不存在'):
            storage.read_outputs('demand','2026-09-30','monthly')
        with storage.connect(read_only=True) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM outputs.demand__monthly').fetchone()[0], 0)

    def test_failure_rolls_back_all_tables(self):
        a={'detail':pd.DataFrame({'code':['A']}),'monthly':pd.DataFrame({'total':[1.]})}
        storage.replace_outputs('demand','2026-09-30',a,{})
        bad={'detail':pd.DataFrame({'code':['B']}),'monthly':pd.DataFrame({'wrong':[2.]})}
        with self.assertRaisesRegex(ValueError,'字段不一致'):
            storage.replace_outputs('demand','2026-09-30',bad,{})
        self.assertEqual(storage.read_outputs('demand','2026-09-30')[0]['code'].tolist(),['A'])

    def test_numpy_scalar_list_round_trip(self):
        first = {'detail': pd.DataFrame({'codes': [np.array([1, 2], dtype=np.int64)]})}
        second = {'detail': pd.DataFrame({'codes': [[np.int64(3), np.int64(4)]]})}
        storage.replace_outputs('demand', '2026-09-30', first, {})
        storage.replace_outputs('demand', '2026-09-30', second, {})
        actual, _ = storage.read_outputs('demand', '2026-09-30')
        self.assertEqual(list(actual['codes'][0]), [3, 4])
