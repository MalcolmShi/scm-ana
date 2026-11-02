"""指标结果归档：parquet + json 两份同源。"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from pandas.testing import assert_frame_equal

import archive
from helpers import make_temp_dir
from reporting import metrics

SCENE = "采购增量分析"


def frame():
    return pd.DataFrame({
        "物料编码": ["001", "002", "003"],
        "采购金额(万)": [12.5, None, 0.0],
        "谈判优先级": ["优先谈判", "观察", "不建议"],
    })


class MetricsArchiveTests(unittest.TestCase):
    def setUp(self):
        self.root: Path = make_temp_dir(self)
        p = patch.object(archive, "DATA_ROOT", self.root)
        p.start()
        self.addCleanup(p.stop)

    def test_parquet_and_json_land_in_same_day_dir(self):
        paths = metrics.archive_metrics(frame(), SCENE, "2026-09-30")
        expected = self.root / "outputs" / SCENE / "2026" / "2026-09" / "2026-09-30"
        self.assertEqual(paths["parquet"], expected / "data.parquet")
        self.assertEqual(paths["json"], expected / "data.json")

    def test_json_shape_and_nan_becomes_null(self):
        paths = metrics.archive_metrics(
            frame(), SCENE, "2026-09-30",
            tables={"月度数据": pd.DataFrame({"月份": ["2026-08"], "金额": [1.0]})},
            meta={"window_start": "2025-10-01"},
        )
        payload = json.loads(paths["json"].read_text(encoding="utf-8"))
        self.assertEqual(payload["meta"]["scene"], SCENE)
        self.assertEqual(payload["meta"]["rows"], 3)
        self.assertEqual(payload["meta"]["window_start"], "2025-10-01")
        self.assertEqual(sorted(payload["tables"]), ["detail", "月度数据"])
        detail = payload["tables"]["detail"]
        self.assertEqual(detail["columns"], list(frame().columns))
        self.assertIsNone(detail["rows"][1]["采购金额(万)"])
        self.assertEqual(len(detail["rows"]), len(frame()))

    def test_parquet_roundtrip_matches_notebook(self):
        paths = metrics.archive_metrics(frame(), SCENE, "2026-09-30")
        loaded, meta = metrics.read_outputs(SCENE)
        assert_frame_equal(loaded, frame())
        self.assertEqual(meta["snapshot_date"], "2026-09-30")
        self.assertEqual(meta["rows"], 3)
        self.assertEqual(paths["parquet"], Path(meta["data_file"]))

    def test_latest_version_is_used_when_date_omitted(self):
        metrics.archive_metrics(frame(), SCENE, "2026-08-31")
        metrics.archive_metrics(frame(), SCENE, "2026-09-30")
        _, meta = metrics.read_outputs(SCENE)
        self.assertEqual(meta["snapshot_date"], "2026-09-30")

    def test_scene_name_allows_chinese_but_rejects_paths(self):
        metrics.archive_metrics(frame(), SCENE, "2026-09-30")   # 中文场景名可用
        for bad in ("", "../outputs", ".hidden", "a/b"):
            with self.subTest(bad=bad):
                with self.assertRaises(archive.DataStoreError):
                    archive.snapshot_path("outputs", bad, "2026-09-30")

    def test_source_id_rule_still_applies_to_raw(self):
        with self.assertRaises(archive.DataStoreError):
            archive.snapshot_path("raw", "采购入库", "2026-09-30")


if __name__ == "__main__":
    unittest.main()
