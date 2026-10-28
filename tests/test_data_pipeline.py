"""离线验证原始取数、底表清洗和现有读取入口。"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from pandas.testing import assert_frame_equal

import data
import pipeline
from cleaning.procurement_inbound import clean_procurement
from connectors.procurement_inbound import ProcurementInboundClient, DataPlatformError


def inbound_row(**changes):
    return {
        "company_type": "检验",
        "inv_inout_date": "2026-09-30",
        "item_num": "00123",
        "inv_in_qty": "12.000000",
        "notax_purchase_order_amt": "1234.560000",
        "purchase_order_amt": "1395.052800",
        "tax_rate": "0.130000",
        "extra_field": "保留未知字段",
        **changes,
    }


class DataPipelineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        p = patch.object(data, "DATA_ROOT", root)
        p.start()
        self.addCleanup(p.stop)
        self.day = "2026-10-28"
        self.fetched_at = "2026-10-28T07:30:00+08:00"
        self.raw = pd.DataFrame([inbound_row()]).assign(
            _source=pipeline.DEFAULT_SOURCE, _fetched_at=self.fetched_at,
        )

    def path(self, layer, day=None):
        return data.snapshot_path(layer, pipeline.DEFAULT_SOURCE, day or self.day)

    def sync(self, raw=None, dry_run=False, stamp=None):
        if raw is None:
            raw = self.raw
        if stamp:
            raw = raw.assign(_fetched_at=stamp)
        else:
            stamp = self.fetched_at
        meta = {"source": pipeline.DEFAULT_SOURCE, "snapshot_date": data.archive_date(stamp), "fetched_at": stamp}
        with patch.object(pipeline, "fetch_raw", return_value=(raw, meta)):
            return pipeline.sync_source("采购入库", dry_run=dry_run)

    def test_sync_preserves_raw_and_cleans_processed(self):
        self.sync()
        raw = pd.read_parquet(self.path("raw"))
        assert_frame_equal(raw, self.raw)
        frame, meta = pipeline.load_source()
        self.assertEqual(frame.loc[0, "物料编码"], "00123")
        self.assertEqual(frame.loc[0, "数量合计"], 12.0)
        self.assertAlmostEqual(frame.loc[0, "采购不含税金额(万)"], 0.123456)
        self.assertAlmostEqual(frame.loc[0, "采购入库含税金额(万)"], 0.13950528)
        self.assertEqual(frame.loc[0, "采购入库年份"], 2026)
        self.assertEqual(frame.loc[0, "采购入库月份"], 9)
        self.assertEqual(frame.loc[0, "出入库月份"], "2026-09-01")
        self.assertEqual(frame.loc[0, "extra_field"], "保留未知字段")
        self.assertEqual(meta["fetched_at"], self.fetched_at)
        self.assertEqual(meta["data_month_range"]["months"], ["2026-09"])
        self.assertIn("processed", meta["data_file"])
        with self.assertRaises(ValueError):
            clean_procurement(frame)  # 不允许重复清洗、重复除以一万

    def test_rebuild_and_column_selection_preserve_metadata(self):
        data.write_snapshot(self.raw, "raw", pipeline.DEFAULT_SOURCE, self.day)
        frame, meta = pipeline.load_source(columns=["物料编码"])
        self.assertEqual(list(frame.columns), ["物料编码"])
        self.assertEqual(meta["fetched_at"], self.fetched_at)
        self.assertEqual(meta["data_month_range"]["end"], "2026-09")
        # raw 更新后，下一次读取必须重新生成 processed。
        path = self.path("raw")
        modified = self.raw.copy()
        modified["inv_in_qty"] = "24.000000"
        modified.to_parquet(path, index=False)
        processed = self.path("processed")
        os.utime(path, ns=(path.stat().st_atime_ns, processed.stat().st_mtime_ns + 1_000_000))
        frame, _ = pipeline.load_source()
        self.assertEqual(frame.loc[0, "数量合计"], 24.0)

    def test_existing_processed_can_be_read_without_raw(self):
        expected = clean_procurement(self.raw)
        path = self.path("processed")
        path.parent.mkdir(parents=True)
        expected.to_parquet(path, index=False)
        frame, _ = pipeline.load_source("入库明细")
        assert_frame_equal(frame, expected)
        self.assertFalse(self.path("raw").exists())

    def test_dry_run_and_invalid_input_do_not_replace_files(self):
        self.sync(dry_run=True)
        self.assertFalse(self.path("raw").exists())
        self.sync()
        paths = [self.path("raw"), self.path("processed")]
        before = [path.read_bytes() for path in paths]
        with self.assertRaises(data.DataStoreError):
            self.sync(pd.DataFrame())
        with self.assertRaises(ValueError):
            self.sync(self.raw.drop(columns=["notax_purchase_order_amt"]))
        self.assertEqual([path.read_bytes() for path in paths], before)

    def test_real_time_read_uses_same_cleaning_without_writing(self):
        with patch.object(pipeline, "fetch_raw", return_value=(self.raw, {"rows": 1})):
            frame, _ = pipeline.load_from_connector()
        assert_frame_equal(frame, clean_procurement(self.raw))
        self.assertFalse(self.path("raw").exists())
        self.assertFalse(self.path("processed").exists())

    def test_paging_returns_original_values_and_rejects_bad_responses(self):
        bodies = []

        def transport(url, headers, body, timeout):
            bodies.append(json.loads(body))
            rows = [inbound_row(), inbound_row()] if len(bodies) == 1 else []
            return {"code": "0", "data": {"data": rows}}

        client = ProcurementInboundClient(page_size=2, app_key="test", app_secret="test", transport=transport)
        with patch.dict(pipeline.SOURCE_HANDLERS, {pipeline.DEFAULT_SOURCE: (lambda: client, clean_procurement, pipeline.month_range)}), patch.object(pipeline, "load_env"):
            raw, _ = pipeline.fetch_raw()
        self.assertEqual(bodies, [{"page_num": "1", "page_size": "2"}, {"page_num": "2", "page_size": "2"}])
        self.assertEqual(raw.loc[0, "notax_purchase_order_amt"], "1234.560000")
        for payload in ({"code": 3004}, {"code": 0}, {"code": 0, "data": {"data": None}}):
            with self.subTest(payload=payload), self.assertRaises(DataPlatformError):
                client._rows_of(payload)


    def test_daily_snapshots_latest_and_exact_history(self):
        self.sync(stamp="2026-10-25T07:30:00+08:00")
        historical = self.path("raw", "2026-10-25").read_bytes()
        newer = self.raw.assign(inv_in_qty="24.000000")
        self.sync(newer, stamp="2026-10-26T07:30:00+08:00")
        self.assertEqual(self.path("raw", "2026-10-25").read_bytes(), historical)
        self.assertEqual(data.list_snapshots(pipeline.DEFAULT_SOURCE), ["2026-10-25", "2026-10-26"])
        latest, meta = pipeline.load_source()
        self.assertEqual(meta["snapshot_date"], "2026-10-26")
        self.assertEqual(len(latest), 1)  # 每日全量快照不能拼接
        self.assertEqual(latest.loc[0, "数量合计"], 24.0)
        previous, meta = pipeline.load_source(snapshot_date="2026-10-25")
        self.assertEqual(previous.loc[0, "数量合计"], 12.0)
        self.assertEqual(meta["snapshot_date"], "2026-10-25")
        with self.assertRaises(data.DataStoreError):
            pipeline.load_source(snapshot_date="2026-10-24")

    def test_newest_raw_is_prepared_instead_of_reading_older_processed(self):
        self.sync(stamp="2026-10-25T07:30:00+08:00")
        newer = self.raw.assign(inv_in_qty="36.000000", _fetched_at="2026-10-26T07:30:00+08:00")
        data.write_snapshot(newer, "raw", pipeline.DEFAULT_SOURCE, "2026-10-26")
        frame, meta = pipeline.load_source()
        self.assertEqual(meta["snapshot_date"], "2026-10-26")
        self.assertEqual(frame.loc[0, "数量合计"], 36.0)
        self.assertTrue(self.path("processed", "2026-10-26").exists())

    def test_generic_archive_supports_other_sources_and_validates_paths(self):
        frame = pd.DataFrame({"adjustment_id": ["P001"]})
        path = data.write_snapshot(frame, "raw", "price-adjustment", "2026-10-25")
        self.assertEqual(path.relative_to(data.DATA_ROOT).as_posix(), "raw/price-adjustment/2026/2026-10/2026-10-25/data.parquet")
        loaded, _ = data.read_snapshot("raw", "price-adjustment")
        assert_frame_equal(loaded, frame)
        for source, day in [("../other", self.day), (pipeline.DEFAULT_SOURCE, "2026-02-30"), (pipeline.DEFAULT_SOURCE, "20261025")]:
            with self.subTest(source=source, day=day), self.assertRaises(data.DataStoreError):
                data.snapshot_path("raw", source, day)
        self.assertEqual(data.archive_date("2026-10-25T18:00:00+00:00"), "2026-10-26")
        with self.assertRaises(data.DataStoreError):
            data.write_snapshot(self.raw, "raw", pipeline.DEFAULT_SOURCE, "2026-10-25")

    def test_atomic_write_failure_preserves_previous_day_file(self):
        self.sync()
        original = self.path("raw").read_bytes()
        with patch.object(pd.DataFrame, "to_parquet", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                data.write_snapshot(self.raw, "raw", pipeline.DEFAULT_SOURCE, self.day)
        self.assertEqual(self.path("raw").read_bytes(), original)
        self.assertEqual(list(self.path("raw").parent.glob("*.part")), [])

    def test_same_day_rerun_replaces_only_that_day(self):
        self.sync(stamp="2026-10-27T07:30:00+08:00")
        previous_day = self.path("raw", "2026-10-27").read_bytes()
        self.sync()
        self.sync(self.raw.assign(inv_in_qty="48.000000"), stamp="2026-10-28T15:30:00+08:00")
        latest, meta = pipeline.load_source()
        self.assertEqual(latest.loc[0, "数量合计"], 48.0)
        self.assertEqual(meta["fetched_at"], "2026-10-28T15:30:00+08:00")
        self.assertEqual(self.path("raw", "2026-10-27").read_bytes(), previous_day)

    def test_another_source_uses_the_same_sync_and_archive(self):
        class ExampleClient:
            url = "http://example.invalid/offline-test"

            def fetch_all(self):
                return [{"record_id": "X001"}]

        handlers = {"test-source": (ExampleClient, lambda frame: frame.copy(), lambda frame: {})}
        with patch.dict(pipeline.SOURCE_HANDLERS, handlers), patch.object(pipeline, "load_env"):
            meta = pipeline.sync_source("test-source")
            frame, loaded_meta = pipeline.load_source("test-source")
        self.assertEqual(frame.loc[0, "record_id"], "X001")
        self.assertEqual(loaded_meta["snapshot_date"], meta["snapshot_date"])
        self.assertTrue(data.snapshot_path("raw", "test-source", meta["snapshot_date"]).exists())


if __name__ == "__main__":
    unittest.main()
