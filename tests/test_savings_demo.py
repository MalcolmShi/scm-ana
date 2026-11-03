"""模拟看板快照校验，避免过期或非模拟数据被误展示。"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb

from services import procurement_savings_demo as demo


class SavingsDemoSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.snapshot = self.root / 'snapshot.json'
        self.patches = [patch.object(demo, 'DATA_DIR', self.root),
                        patch.object(demo, 'SNAPSHOT', self.snapshot)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        checksums = {}
        for name in demo.INPUT_FILES:
            contents = name.encode('utf-8')
            (self.root / name).write_bytes(contents)
            checksums[name] = hashlib.sha256(contents).hexdigest()
        self.data = {'meta': {'schema_version': 1, 'data_kind': 'mock', '输入校验': checksums}}
        self.data.update({name: [] for name in demo.TABLES})
        self.write_snapshot()

    def write_snapshot(self):
        self.snapshot.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')

    def test_valid_snapshot_is_returned(self):
        self.assertEqual(json.loads(demo.read_snapshot()), self.data)

    def test_changed_input_is_rejected(self):
        (self.root / demo.INPUT_FILES[0]).write_text('changed', encoding='utf-8')
        with self.assertRaisesRegex(demo.DemoDataUnavailable, '模拟输入已变化'):
            demo.read_snapshot()

    def test_non_mock_or_incomplete_snapshot_is_rejected(self):
        self.data['meta']['data_kind'] = 'production'
        self.write_snapshot()
        with self.assertRaises(demo.DemoDataUnavailable):
            demo.read_snapshot()
        self.data['meta']['data_kind'] = 'mock'
        del self.data['exceptions']
        self.write_snapshot()
        with self.assertRaises(demo.DemoDataUnavailable):
            demo.read_snapshot()

    def test_missing_or_invalid_snapshot_is_rejected(self):
        self.snapshot.unlink()
        with self.assertRaises(demo.DemoDataUnavailable):
            demo.read_snapshot()
        self.snapshot.write_text('{', encoding='utf-8')
        with self.assertRaises(demo.DemoDataUnavailable):
            demo.read_snapshot()


class SavingsDemoDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.database = self.root / 'demo.duckdb'
        for name, path in [('DATA_DIR', self.root), ('SNAPSHOT', self.root / 'snapshot.json'),
                           ('DATABASE', self.database)]:
            item = patch.object(demo, name, path)
            item.start()
            self.addCleanup(item.stop)
        checksums = {}
        for name in demo.INPUT_FILES:
            contents = '测试字段\n模拟记录\n'.encode('utf-8')
            (self.root / name).write_bytes(contents)
            checksums[name] = hashlib.sha256(contents).hexdigest()
        self.payload = {'meta': {'schema_version': 1, 'data_kind': 'mock', '输入校验': checksums}}
        self.payload.update({name: [{'数值': 1}] for name in demo.TABLES})
        demo.SNAPSHOT.write_text(json.dumps(self.payload, ensure_ascii=False), encoding='utf-8')

    def test_database_reflects_changes_and_reimport_replaces_them(self):
        counts = demo.import_demo_database()
        self.assertEqual(set(counts), set(demo.TABLES) | set(demo.SOURCE_FILES))
        self.assertTrue(all(count == 1 for count in counts.values()))
        self.assertEqual(json.loads(demo.read_database())['detail'][0]['数值'], 1)
        with duckdb.connect(str(self.database)) as con:
            con.execute('UPDATE demo_savings.detail SET 数值 = 2')
        self.assertEqual(json.loads(demo.read_database())['detail'][0]['数值'], 2)
        demo.import_demo_database()
        self.assertEqual(json.loads(demo.read_database())['detail'][0]['数值'], 1)

    def test_missing_or_non_mock_database_is_rejected(self):
        with self.assertRaises(demo.DemoDataUnavailable):
            demo.read_database()
        demo.import_demo_database()
        with duckdb.connect(str(self.database)) as con:
            con.execute("UPDATE demo_savings.metadata SET meta_json = ?::JSON",
                        [json.dumps({'schema_version': 1, 'data_kind': 'production'})])
        with self.assertRaises(demo.DemoDataUnavailable):
            demo.read_database()


if __name__ == '__main__':
    unittest.main()
