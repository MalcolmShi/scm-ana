"""通用 DuckDB 存取。只处理数据层、表、业务日期和事务，不包含业务规则。"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa

from paths import DATA_ROOT, TIMEZONE

DATABASE = DATA_ROOT / 'scm.duckdb'
LAYERS = ('raw', 'processed')


def _name(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[a-z][a-z0-9_]*', value) or '__' in value:
        raise ValueError(f'名称只允许小写英文字母、数字和下划线：{value!r}')
    return value


def _table(layer: str, name: str) -> str:
    if layer not in (*LAYERS, 'outputs'):
        raise ValueError(f'未知数据层：{layer}')
    if not re.fullmatch(r'[a-z][a-z0-9_]*', name):
        raise ValueError(f'无效的表名：{name!r}')
    return f'{layer}.{name}'


def connect(read_only: bool = False):
    path = Path(DATABASE)
    if read_only and not path.exists():
        raise FileNotFoundError(f'数据库不存在：{path}')
    if not read_only:
        path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path), read_only=read_only)


def initialize(conn):
    for schema in (*LAYERS, 'outputs', 'meta'):
        conn.execute(f'CREATE SCHEMA IF NOT EXISTS {schema}')
    conn.execute('''CREATE TABLE IF NOT EXISTS meta.partitions (
        layer VARCHAR, dataset VARCHAR, business_day DATE, row_count BIGINT,
        fingerprint VARCHAR, source_dataset VARCHAR, source_fingerprint VARCHAR,
        PRIMARY KEY (layer, dataset, business_day))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS meta.output_periods (
        scene VARCHAR, business_end DATE, archive_date DATE, generated_at VARCHAR,
        details_json VARCHAR, PRIMARY KEY (scene, business_end))''')


def fingerprint(frame: pd.DataFrame) -> str:
    digest = hashlib.sha256()
    digest.update(json.dumps(list(frame.columns), ensure_ascii=False).encode())
    digest.update(pd.util.hash_pandas_object(frame, index=False).values.tobytes())
    return digest.hexdigest()


def _check_schema(con, table: str, incoming: pa.Table, reserved: str):
    expected = {row[0]: row[1] for row in con.execute(f'DESCRIBE SELECT * EXCLUDE ({reserved}) FROM {table}').fetchall()}
    actual = {row[0]: row[1] for row in con.execute('DESCRIBE SELECT * FROM incoming').fetchall()}
    if expected.keys() != actual.keys():
        raise ValueError(f'{table} 字段不一致：缺少 {sorted(expected.keys()-actual.keys())}，新增 {sorted(actual.keys()-expected.keys())}')
    mismatch = {name: (expected[name], actual[name]) for name in expected
                if expected[name] != actual[name] and not pa.types.is_null(incoming.schema.field(name).type)}
    if mismatch:
        raise ValueError(f'{table} 字段类型不一致：{mismatch}')


def _partitions(con, layer, dataset, start, end):
    rows = con.execute('''SELECT business_day, row_count, fingerprint, source_dataset, source_fingerprint
        FROM meta.partitions WHERE layer=? AND dataset=? AND business_day BETWEEN ? AND ?
        ORDER BY business_day''', [layer, dataset, start, end]).fetchall()
    return [{'day': str(day), 'rows': count, 'fingerprint': digest, 'source_dataset': source_dataset, 'source_fingerprint': source}
            for day, count, digest, source_dataset, source in rows]


def partitions(layer: str, dataset: str, start: str, end: str) -> list[dict]:
    _table(layer, _name(dataset))
    with connect(read_only=True) as con:
        return _partitions(con, layer, dataset, start, end)


def read_window(layer: str, dataset: str, start: str, end: str, columns=None, *, source_dataset=None):
    """在同一快照读取数据、分区清单和可选的 raw 来源清单。"""
    table = _table(layer, _name(dataset))
    if source_dataset is not None:
        _name(source_dataset)
    with connect(read_only=True) as con:
        con.begin()
        parts = _partitions(con, layer, dataset, start, end)
        if not parts:
            raise ValueError(f'{layer}.{dataset} 在 {start} 至 {end} 没有数据')
        raw_parts = _partitions(con, 'raw', source_dataset, start, end) if source_dataset else []
        frame = con.execute(f'SELECT * EXCLUDE (_business_day) FROM {table} WHERE _business_day BETWEEN ? AND ? ORDER BY _business_day, rowid', [start, end]).fetchdf()
        con.commit()
    if columns is not None:
        frame = frame.loc[:, list(columns)]
    return frame.reset_index(drop=True), parts, raw_parts


def read_partitions(layer: str, dataset: str, start: str, end: str, columns=None) -> pd.DataFrame:
    return read_window(layer, dataset, start, end, columns)[0]


def replace_partitions(layer: str, dataset: str, frames: dict[str, pd.DataFrame], *,
                       source_dataset=None, source_fingerprints=None):
    """同一轮多日期数据在一个事务中写入；任一日失败则全部回滚。"""
    table = _table(layer, _name(dataset))
    if layer not in LAYERS or not frames:
        raise ValueError('数据层或日期数据不能为空')
    source_fingerprints = source_fingerprints or {}
    if source_fingerprints and (layer != 'processed' or set(source_fingerprints) != set(frames)):
        raise ValueError('清洗输入指纹必须覆盖全部日期')
    if source_fingerprints:
        _name(source_dataset)
    with connect() as con:
        initialize(con)
        con.begin()
        try:
            for day, frame in frames.items():
                if '_business_day' in frame:
                    raise ValueError('输入包含保留字段 _business_day')
                source_fingerprint = source_fingerprints.get(day)
                if source_fingerprint is not None:
                    matches = con.execute('SELECT fingerprint FROM meta.partitions WHERE layer=? AND dataset=? AND business_day=?', ['raw', source_dataset, day]).fetchall()
                    if not matches or matches[0][0] != source_fingerprint:
                        raise ValueError(f'raw 已变化，不能写入过期清洗结果：{day}')
                incoming = pa.Table.from_pandas(frame, preserve_index=False)
                con.register('incoming', incoming)
                con.execute(f'CREATE TABLE IF NOT EXISTS {table} AS SELECT *, CAST(? AS DATE) AS _business_day FROM incoming WHERE false', [day])
                _check_schema(con, table, incoming, '_business_day')
                con.execute(f'DELETE FROM {table} WHERE _business_day=?', [day])
                con.execute(f'INSERT INTO {table} BY NAME SELECT *, CAST(? AS DATE) AS _business_day FROM incoming', [day])
                con.execute('INSERT OR REPLACE INTO meta.partitions VALUES (?, ?, ?, ?, ?, ?, ?)', [layer, dataset, day, len(frame), fingerprint(frame), source_dataset, source_fingerprint])
                if layer == 'raw':
                    con.execute("UPDATE meta.partitions SET source_dataset=?, source_fingerprint='raw已更新' WHERE layer='processed' AND (source_dataset=? OR (source_dataset IS NULL AND dataset=?)) AND business_day=?", [dataset, dataset, dataset, day])
                con.unregister('incoming')
            con.commit()
        except BaseException:
            con.rollback()
            raise


def replace_partition(layer: str, dataset: str, day: str, frame: pd.DataFrame, *, source_dataset=None, source_fingerprint=None):
    fingerprints = {day: source_fingerprint} if source_fingerprint is not None else None
    replace_partitions(layer, dataset, {day: frame}, source_dataset=source_dataset, source_fingerprints=fingerprints)


def replace_outputs(scene: str, end: str, tables: dict[str, pd.DataFrame], metadata: dict):
    _name(scene)
    if not tables or 'detail' not in tables:
        raise ValueError('必须提供 detail 主表')
    with connect() as con:
        initialize(con)
        con.begin()
        try:
            for source in metadata.get('inputs', []):
                rows = con.execute('''SELECT business_day, row_count, fingerprint, source_dataset, source_fingerprint
                    FROM meta.partitions WHERE layer=? AND dataset=? AND business_day BETWEEN ? AND ? ORDER BY business_day''',
                    [source['layer'], source['dataset'], source['start'], source['end']]).fetchall()
                current = [{'day': str(day), 'rows': count, 'fingerprint': digest,
                            'source_dataset': source_dataset, 'source_fingerprint': source_fingerprint}
                           for day, count, digest, source_dataset, source_fingerprint in rows]
                if current != source['partitions']:
                    raise ValueError(f"输入 {source['layer']}.{source['dataset']} 已变化，未写入结果")
            previous = con.execute('SELECT details_json FROM meta.output_periods WHERE scene=? AND business_end=?', [scene, end]).fetchall()
            if previous:
                old_tables = set(json.loads(previous[0][0]).get('tables', []))
                for removed in old_tables - set(tables):
                    table = _table('outputs', f'{scene}__{_name(removed)}')
                    con.execute(f'DELETE FROM {table} WHERE _business_end=?', [end])
            for name, frame in tables.items():
                table = _table('outputs', f'{scene}__{_name(name)}')
                if '_business_end' in frame:
                    raise ValueError('结果包含保留字段 _business_end')
                incoming = pa.Table.from_pandas(frame, preserve_index=False)
                con.register('incoming', incoming)
                con.execute(f'CREATE TABLE IF NOT EXISTS {table} AS SELECT *, CAST(? AS DATE) AS _business_end FROM incoming WHERE false', [end])
                _check_schema(con, table, incoming, '_business_end')
                con.execute(f'DELETE FROM {table} WHERE _business_end=?', [end])
                con.execute(f'INSERT INTO {table} BY NAME SELECT *, CAST(? AS DATE) AS _business_end FROM incoming', [end])
                con.unregister('incoming')
            now = datetime.now(TIMEZONE)
            details = {**metadata, 'tables': list(tables)}
            con.execute('INSERT OR REPLACE INTO meta.output_periods VALUES (?, ?, ?, ?, ?)', [scene, end, now.date(), now.isoformat(timespec='seconds'), json.dumps(details, ensure_ascii=False, default=str)])
            con.commit()
        except BaseException:
            con.rollback()
            raise


def output_metadata(con, scene: str, end: str) -> dict:
    """在调用方事务中检查输入版本并返回结果元数据。"""
    _name(scene)
    records = con.execute('SELECT archive_date, generated_at, details_json FROM meta.output_periods WHERE scene=? AND business_end=?', [scene, end]).fetchall()
    if not records:
        raise ValueError(f'未归档：{scene} / {end}')
    archived, generated, text = records[0]
    meta = json.loads(text)
    inputs = meta.get('inputs', [])
    for source in inputs:
        rows = con.execute("""SELECT business_day, row_count, fingerprint, source_dataset, source_fingerprint
            FROM meta.partitions WHERE layer=? AND dataset=? AND business_day BETWEEN ? AND ? ORDER BY business_day""",
            [source['layer'], source['dataset'], source['start'], source['end']]).fetchall()
        current = [{'day': str(day), 'rows': count, 'fingerprint': digest,
                    'source_dataset': source_dataset, 'source_fingerprint': source_fingerprint}
                   for day, count, digest, source_dataset, source_fingerprint in rows]
        if source['partitions'] != current:
            raise ValueError(f"输入 {source['layer']}.{source['dataset']} 已变化，请重新计算结果")
    meta.update(scene=scene, business_end=end, archive_date=str(archived), generated_at=generated,
                input_status='已核对' if inputs else '未核实')
    return meta


def read_outputs(scene: str, end: str, name: str = 'detail') -> tuple[pd.DataFrame, dict]:
    table = _table('outputs', f'{_name(scene)}__{_name(name)}')
    with connect(read_only=True) as con:
        con.begin()
        meta = output_metadata(con, scene, end)
        if name not in meta['tables']:
            raise ValueError(f'结果表不存在：{name}')
        frame = con.execute(f'SELECT * EXCLUDE (_business_end) FROM {table} WHERE _business_end=?', [end]).fetchdf()
        con.commit()
    return frame, meta
