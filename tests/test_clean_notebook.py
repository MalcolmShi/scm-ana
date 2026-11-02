"""清洗确认 notebook 必须读取 raw，即使同日已有 processed。"""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import archive
from clean.procurement_inbound import STANDARD_COLUMNS
from helpers import inbound_row, make_temp_dir
from paths import PROJECT_ROOT


class CleanNotebookTests(unittest.TestCase):
    def test_duplicate_check_uses_raw_when_processed_exists(self):
        root = make_temp_dir(self)
        raw = pd.DataFrame([inbound_row(批次号="lot-1", 单据编码="doc-1", 财务内部外供应商类型="外部单位")])
        for column in STANDARD_COLUMNS:
            source = {"供应商类型": "财务内部外供应商类型", "采购模式": "采购类型名称"}.get(column, column)
            if source not in raw:
                raw[source] = "样例"
        raw["购退单价"] = None
        processed = raw.drop(columns=["批次号", "单据编码"])
        with patch.object(archive, "DATA_ROOT", root):
            for layer, frame in (("raw", raw), ("processed", processed)):
                archive.write_snapshot(frame, layer, "procurement-inbound", "2026-09-30", check_fetched_at=False)
            path = PROJECT_ROOT / "notebooks/采购增量分析/数据清洗确认.ipynb"
            notebook = json.loads(path.read_text())
            scope = {"display": lambda value: None}
            duplicate_section = next(i for i, cell in enumerate(notebook["cells"])
                                     if "### 已确认字段相同的记录" in ''.join(cell["source"]))
            for index in (2, 4, duplicate_section + 1):
                source = ''.join(notebook["cells"][index]["source"])
                exec(compile(source, str(path), "exec"), scope)
            self.assertEqual(scope["df"]["批次号"].tolist(), ["lot-1"])
            self.assertIn("财务内部外供应商类型", scope["df"].columns)
            self.assertNotIn("供应商类型", scope["df"].columns)


if __name__ == "__main__":
    unittest.main()
