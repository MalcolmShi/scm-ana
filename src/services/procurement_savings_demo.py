"""采购降本模拟看板快照；不读取正式业务数据或计算正式结果。"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

from paths import DATA_ROOT, PROJECT_ROOT, TIMEZONE


DATA_DIR = PROJECT_ROOT / 'notebooks' / '采购降本统计' / '演示数据'
SNAPSHOT = DATA_DIR / '模拟降本看板快照.json'
DATABASE = Path(os.environ.get('SCM_SAVINGS_DB_PATH') or DATA_ROOT / 'procurement_savings_demo.duckdb')
SCHEMA = 'demo_savings'
INPUT_FILES = (
    '模拟协议版本-扩展.csv',
    '模拟采购入库-扩展.csv',
    '模拟替换填报-扩展.csv',
    '模拟返利反馈-扩展.csv',
)
TABLES = ('detail', 'receipts', 'exceptions', 'tax_changes', 'rebates',
          'targets', 'protocols', 'relations', 'candidates')
SOURCE_FILES = {
    'source_agreements': INPUT_FILES[0],
    'source_inbound': INPUT_FILES[1],
    'source_replacements': INPUT_FILES[2],
    'source_rebates': INPUT_FILES[3],
}


class DemoDataUnavailable(Exception):
    """模拟数据不存在、失效或损坏。"""


def read_snapshot() -> bytes:
    """读取模拟快照，并确认与其四份输入文件一致。"""
    try:
        payload = SNAPSHOT.read_bytes()
        data = json.loads(payload)
        meta = data['meta']
        checksums = meta['输入校验']
        if (meta['schema_version'] != 1 or meta['data_kind'] != 'mock'
                or set(checksums) != set(INPUT_FILES)
                or any(not isinstance(data[name], list) for name in TABLES)):
            raise ValueError('快照结构不符合要求')
        for name in INPUT_FILES:
            digest = hashlib.sha256((DATA_DIR / name).read_bytes()).hexdigest()
            if digest != checksums[name]:
                raise DemoDataUnavailable(f'模拟输入已变化：{name}；请重新运行模拟演示 notebook 生成快照')
    except DemoDataUnavailable:
        raise
    except (FileNotFoundError, OSError, KeyError, TypeError, ValueError) as exc:
        raise DemoDataUnavailable('模拟看板数据缺失或不可用，请先完整运行采购降本模拟演示 notebook') from exc
    return payload


def import_demo_database() -> dict[str, int]:
    """将 notebook 的模拟结果及对应输入一次性导入独立演示库。

    重跑会替换整个模拟数据集，数据库中的临时演示修改也会被覆盖。
    """
    data = json.loads(read_snapshot())
    frames = {name: pd.DataFrame(data[name]) for name in TABLES}
    for table, filename in SOURCE_FILES.items():
        # 编码应保留原样；其余字段按模拟 notebook 的常用数值类型读取。
        types = {'协议编号': 'string', '供应商编码': 'string', '演示替换编号': 'string',
                 'updated_at': 'string'}
        frames[table] = pd.read_csv(DATA_DIR / filename, dtype=types)
    for name, frame in frames.items():
        if not len(frame.columns):
            raise DemoDataUnavailable(f'模拟表 {name} 没有字段，无法导入')

    metadata = dict(data['meta'])
    metadata['导入时间'] = datetime.now(TIMEZONE).isoformat(timespec='seconds')
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with duckdb.connect(str(DATABASE)) as con:
            con.begin()
            con.execute(f'CREATE SCHEMA IF NOT EXISTS {SCHEMA}')
            for name, frame in frames.items():
                con.register('demo_incoming', frame)
                con.execute(f'CREATE OR REPLACE TABLE {SCHEMA}.{name} AS SELECT * FROM demo_incoming')
                con.unregister('demo_incoming')
            con.execute(f'CREATE OR REPLACE TABLE {SCHEMA}.metadata AS SELECT ?::JSON AS meta_json',
                        [json.dumps(metadata, ensure_ascii=False)])
            con.commit()
    except duckdb.Error as exc:
        raise DemoDataUnavailable(f'模拟数据库导入失败：{exc}') from exc
    return {name: len(frame) for name, frame in frames.items()}


def read_database() -> bytes:
    """在同一读取事务中读取独立演示库，供看板每次刷新查询。"""
    if not DATABASE.exists():
        raise DemoDataUnavailable('模拟数据库不存在，请先运行 scripts/import_savings_demo.py')
    try:
        with duckdb.connect(str(DATABASE), read_only=True) as con:
            con.begin()
            rows = con.execute(f'SELECT meta_json FROM {SCHEMA}.metadata').fetchall()
            if len(rows) != 1:
                raise ValueError('模拟数据元信息不完整')
            meta = json.loads(rows[0][0])
            if meta.get('data_kind') != 'mock' or meta.get('schema_version') != 1:
                raise ValueError('数据库不是兼容的模拟数据')
            data = {'meta': meta}
            for name in TABLES:
                result = con.execute(
                    f"SELECT coalesce(to_json(list(t ORDER BY rowid)), '[]') FROM {SCHEMA}.{name} t"
                ).fetchone()[0]
                data[name] = json.loads(result)
            con.commit()
        return json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (duckdb.Error, OSError, ValueError, TypeError, KeyError) as exc:
        raise DemoDataUnavailable(f'模拟数据库读取失败，请检查演示数据：{exc}') from exc
