"""通用 DuckDB 存取。只处理数据层、表、业务日期和事务，不包含业务规则。"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

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
        PRIMARY KEY (scene, business_end))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS meta.output_tables (scene VARCHAR, business_end DATE, name VARCHAR)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS meta.output_attributes (scene VARCHAR, business_end DATE, path VARCHAR[], kind VARCHAR, value VARCHAR)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS meta.output_inputs (scene VARCHAR, business_end DATE, input_index INTEGER, layer VARCHAR, dataset VARCHAR, start_day DATE, end_day DATE)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS meta.output_input_partitions (scene VARCHAR, business_end DATE, input_index INTEGER, business_day DATE, row_count BIGINT, fingerprint VARCHAR, source_dataset VARCHAR, source_fingerprint VARCHAR)''')


def fingerprint(frame: pd.DataFrame) -> str:
    digest = hashlib.sha256()
    for column in frame.columns:
        encoded = str(column).encode('utf-8')
        digest.update(len(encoded).to_bytes(8, 'big'))
        digest.update(encoded)
    digest.update(pd.util.hash_pandas_object(frame, index=False).values.tobytes())
    return digest.hexdigest()


def _prepare_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """让 DuckDB 正确识别包含 NumPy 标量的列表列。"""
    result = frame.copy(deep=False)
    for name in (name for name in frame.columns if frame[name].dtype == object):
        if frame[name].map(lambda value: isinstance(value, list) and
                           any(isinstance(item, np.generic) for item in value)).any():
            result[name] = frame[name].map(lambda value: np.asarray(value) if isinstance(value, list) else value)
    return result


def _check_schema(con, table: str, incoming: pd.DataFrame, reserved: str):
    expected = {row[0]: row[1] for row in con.execute(f'DESCRIBE SELECT * EXCLUDE ({reserved}) FROM {table}').fetchall()}
    actual = {row[0]: row[1] for row in con.execute('DESCRIBE SELECT * FROM incoming').fetchall()}
    if expected.keys() != actual.keys():
        raise ValueError(f'{table} 字段不一致：缺少 {sorted(expected.keys()-actual.keys())}，新增 {sorted(actual.keys()-expected.keys())}')
    mismatch = {name: (expected[name], actual[name]) for name in expected
                if expected[name] != actual[name] and not (incoming[name].isna().all() and
                    (pd.api.types.is_object_dtype(incoming[name].dtype) or pd.api.types.is_string_dtype(incoming[name].dtype)))}
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
        exists = con.execute('SELECT count(*) FROM information_schema.tables WHERE table_schema=? AND table_name=?', [layer, dataset]).fetchone()[0]
        if not exists:
            raise ValueError(f'数据集尚未建立：{layer}.{dataset}')
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
                incoming = _prepare_frame(frame)
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


def _flatten(value, path=()):
    """将元数据展开成有类型的路径和值，保留空容器。"""
    if isinstance(value, dict):
        yield list(path), 'dict', None
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError('元数据键必须为字符串')
            yield from _flatten(item, (*path, key))
    elif isinstance(value, (list, tuple)):
        yield list(path), 'list', None
        for index, item in enumerate(value):
            yield from _flatten(item, (*path, str(index)))
    elif value is None:
        yield list(path), 'null', None
    elif isinstance(value, bool):
        yield list(path), 'bool', str(value)
    elif isinstance(value, int):
        yield list(path), 'int', str(value)
    elif isinstance(value, float):
        yield list(path), 'float', repr(value)
    elif isinstance(value, str):
        yield list(path), 'str', value
    else:
        raise ValueError(f'不支持的元数据类型：{type(value).__name__}')


def _unflatten(rows):
    nodes = {tuple(path): (kind, value) for path, kind, value in rows}
    children = {}
    for path in nodes:
        if path:
            children.setdefault(path[:-1], []).append(path[-1])
    def build(path):
        kind, value = nodes[path]
        if kind == 'dict':
            return {key: build((*path, key)) for key in children.get(path, [])}
        if kind == 'list':
            return [build((*path, key)) for key in sorted(children.get(path, []), key=int)]
        if kind == 'null': return None
        if kind == 'bool': return value == 'True'
        if kind == 'int': return int(value)
        if kind == 'float': return float(value)
        if kind == 'str': return value
        raise ValueError(f'未知元数据类型：{kind}')
    return build(()) if () in nodes else {}


def _save_metadata(con, scene, end, archived, generated, metadata, table_names):
    for name in ('output_tables', 'output_attributes', 'output_inputs', 'output_input_partitions'):
        con.execute(f'DELETE FROM meta.{name} WHERE scene=? AND business_end=?', [scene, end])
    con.execute('INSERT OR REPLACE INTO meta.output_periods VALUES (?, ?, ?, ?)', [scene, end, archived, generated])
    con.executemany('INSERT INTO meta.output_tables VALUES (?, ?, ?)', [[scene,end,name] for name in table_names])
    attributes = {key: value for key, value in metadata.items() if key not in ('inputs','tables')}
    con.executemany('INSERT INTO meta.output_attributes VALUES (?, ?, ?, ?, ?)',
                    [[scene,end,path,kind,value] for path,kind,value in _flatten(attributes)])
    for index, source in enumerate(metadata.get('inputs', [])):
        con.execute('INSERT INTO meta.output_inputs VALUES (?, ?, ?, ?, ?, ?, ?)',
                    [scene,end,index,source['layer'],source['dataset'],source['start'],source['end']])
        rows = [[scene,end,index,p['day'],p['rows'],p['fingerprint'],p.get('source_dataset'),p.get('source_fingerprint')]
                for p in source['partitions']]
        if rows:
            con.executemany('INSERT INTO meta.output_input_partitions VALUES (?, ?, ?, ?, ?, ?, ?, ?)',rows)


def replace_outputs(scene: str, end: str, tables: dict[str, pd.DataFrame], metadata: dict):
    _name(scene)
    if not tables or 'detail' not in tables:
        raise ValueError('必须提供 detail 主表')
    with connect() as con:
        initialize(con)
        con.begin()
        try:
            for source in metadata.get('inputs', []):
                current = _partitions(con, source['layer'], source['dataset'], source['start'], source['end'])
                if current != source['partitions']:
                    raise ValueError(f"输入 {source['layer']}.{source['dataset']} 已变化，未写入结果")
            previous = con.execute('SELECT name FROM meta.output_tables WHERE scene=? AND business_end=?', [scene, end]).fetchall()
            for removed in {row[0] for row in previous} - set(tables):
                table = _table('outputs', f'{scene}__{_name(removed)}')
                con.execute(f'DELETE FROM {table} WHERE _business_end=?', [end])
            for name, frame in tables.items():
                table = _table('outputs', f'{scene}__{_name(name)}')
                if '_business_end' in frame:
                    raise ValueError('结果包含保留字段 _business_end')
                incoming = _prepare_frame(frame)
                con.register('incoming', incoming)
                con.execute(f'CREATE TABLE IF NOT EXISTS {table} AS SELECT *, CAST(? AS DATE) AS _business_end FROM incoming WHERE false', [end])
                _check_schema(con, table, incoming, '_business_end')
                con.execute(f'DELETE FROM {table} WHERE _business_end=?', [end])
                con.execute(f'INSERT INTO {table} BY NAME SELECT *, CAST(? AS DATE) AS _business_end FROM incoming', [end])
                con.unregister('incoming')
            now = datetime.now(TIMEZONE)
            _save_metadata(con, scene, end, now.date(), now.isoformat(timespec='seconds'), metadata, list(tables))
            con.commit()
        except BaseException:
            con.rollback()
            raise


def output_metadata(con, scene: str, end: str) -> dict:
    """在调用方事务中检查输入版本并返回结果元数据。"""
    _name(scene)
    records = con.execute('SELECT archive_date, generated_at FROM meta.output_periods WHERE scene=? AND business_end=?', [scene, end]).fetchall()
    if not records:
        raise ValueError(f'未归档：{scene} / {end}')
    archived, generated = records[0]
    attributes = con.execute('SELECT path, kind, value FROM meta.output_attributes WHERE scene=? AND business_end=?', [scene,end]).fetchall()
    meta = _unflatten(attributes)
    tables = [row[0] for row in con.execute('SELECT name FROM meta.output_tables WHERE scene=? AND business_end=?', [scene,end]).fetchall()]
    sources = con.execute('SELECT input_index, layer, dataset, start_day, end_day FROM meta.output_inputs WHERE scene=? AND business_end=? ORDER BY input_index', [scene,end]).fetchall()
    inputs = []
    for index, layer, dataset, start, stop in sources:
        rows = con.execute('''SELECT business_day, row_count, fingerprint, source_dataset, source_fingerprint
            FROM meta.output_input_partitions WHERE scene=? AND business_end=? AND input_index=? ORDER BY business_day''', [scene,end,index]).fetchall()
        expected = [{'day': str(day), 'rows': count, 'fingerprint': digest,
                     'source_dataset': source_dataset, 'source_fingerprint': source_fingerprint}
                    for day, count, digest, source_dataset, source_fingerprint in rows]
        if expected != _partitions(con, layer, dataset, str(start), str(stop)):
            raise ValueError(f'输入 {layer}.{dataset} 已变化，请重新计算结果')
        inputs.append({'layer': layer, 'dataset': dataset, 'start': str(start), 'end': str(stop), 'partitions': expected})
    meta.update(scene=scene, business_end=end, archive_date=str(archived), generated_at=generated,
                input_status='已核对' if sources else '未核实', tables=tables, inputs=inputs)
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
