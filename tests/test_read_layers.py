"""三层读取入口：指定层、窗口和缺失分区。"""
import unittest
from unittest.mock import patch

import pandas as pd
from pandas.testing import assert_frame_equal

import archive
import read_data
from helpers import inbound_row, make_temp_dir


class ReadLayersTests(unittest.TestCase):
    def setUp(self):
        root = make_temp_dir(self)
        patcher = patch.object(archive, "DATA_ROOT", root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.source = "procurement-inbound"
        self.raw = pd.DataFrame([inbound_row(出入库日期="2026-09-29")])
        self.processed = pd.DataFrame([{"出入库日期": "2026-09-29", "供应商类型": "外部单位"}])
        archive.write_snapshot(self.raw, "raw", self.source, "2026-09-29", check_fetched_at=False)
        archive.write_snapshot(self.processed, "processed", self.source, "2026-09-29", check_fetched_at=False)

    def test_raw_and_processed_are_explicit(self):
        assert_frame_equal(read_data.read_raw_procurement_inbound("2026-09"), self.raw)
        assert_frame_equal(read_data.read_processed_procurement_inbound("2026-09"), self.processed)
        _, meta = read_data.read_processed_procurement_inbound_meta("2026-09")
        self.assertEqual(meta["layer"], "processed")
        self.assertEqual(meta["days"], ["2026-09-29"])
        self.assertEqual(meta["coverage"]["input_versions"], "未核实")
        self.assertEqual(meta["coverage"]["missing_raw_days"], [])

    def test_missing_processed_day_is_reported_instead_of_skipped(self):
        missing = self.raw.assign(出入库日期="2026-09-30")
        archive.write_snapshot(missing, "raw", self.source, "2026-09-30", check_fetched_at=False)
        with self.assertRaisesRegex(archive.DataStoreError, "2026-09-30"):
            read_data.read_processed_procurement_inbound("2026-09")
        assert_frame_equal(read_data.read_processed_procurement_inbound("2026-09-29"), self.processed)

    def test_outputs_requires_explicit_business_date(self):
        expected = pd.DataFrame({"物料编码": ["001"], "金额": [3.]})
        archive.write_snapshot(expected, "outputs", "采购增量分析", "2026-09-30", check_fetched_at=False)
        assert_frame_equal(read_data.read_outputs("采购增量分析", "2026-09-30"), expected)
        _, meta = read_data.read_outputs_meta("采购增量分析", "2026-09-30")
        self.assertEqual(meta["snapshot_date"], "2026-09-30")
        with self.assertRaises(TypeError):
            read_data.read_outputs("采购增量分析")


if __name__ == "__main__":
    unittest.main()
