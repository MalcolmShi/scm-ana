"""清洗确认 notebook 必须读取 raw，即使同日已有 processed。"""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import archive
from helpers import inbound_row, make_temp_dir
from paths import PROJECT_ROOT


class CleanNotebookTests(unittest.TestCase):
    def test_duplicate_check_uses_raw_when_processed_exists(self):
        root = make_temp_dir(self)
        raw = pd.DataFrame([inbound_row(批次号="lot-1", 单据编码="doc-1", 财务内部外供应商类型="外部单位")])
        processed = raw.drop(columns=["批次号", "单据编码"])
        with patch.object(archive, "DATA_ROOT", root):
            for layer, frame in (("raw", raw), ("processed", processed)):
                archive.write_snapshot(frame, layer, "procurement-inbound", "2026-09-30", check_fetched_at=False)
            path = PROJECT_ROOT / "notebooks/采购增量分析/数据清洗确认.ipynb"
            notebook = json.loads(path.read_text())
            scope = {"display": lambda value: None}
            for index in (1, 3):
                source = ''.join(notebook["cells"][index]["source"])
                exec(compile(source, str(path), "exec"), scope)
            self.assertEqual(scope["df"]["批次号"].tolist(), ["lot-1"])
            self.assertIn("供应商类型", scope["df"].columns)
            self.assertNotIn("财务内部外供应商类型", scope["df"].columns)
            self.assertEqual(scope["raw_meta"]["layer"], "raw")


if __name__ == "__main__":
    unittest.main()
