#!/usr/bin/env python3
"""将上一版 DuckDB 迁入通用表结构；保留原数据库备份。"""
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import duckdb
import storage
from paths import LOCAL_DATA_ROOT, TIMEZONE


def main():
    old = Path(storage.DATABASE)
    if not old.exists():
        raise FileNotFoundError(f'数据库不存在：{old}')
    with duckdb.connect(str(old), read_only=True) as existing:
        columns = {row[0] for row in existing.execute('DESCRIBE SELECT * FROM meta.partitions').fetchall()}
        if 'dataset' in columns:
            print('数据库已是新结构，无需迁移。')
            return
    tmp = old.parent / f'.重新规划-{uuid4().hex}.duckdb'
    backup = LOCAL_DATA_ROOT / f'scm-duckdb-before-redesign-{datetime.now(TIMEZONE).strftime("%Y%m%dT%H%M%S")}.duckdb'
    try:
        with duckdb.connect(str(tmp)) as con:
            storage.initialize(con)
            con.execute('ATTACH ? AS previous (READ_ONLY)', [str(old)])
            con.begin()
            for layer in ('raw', 'processed'):
                source = f'SELECT * EXCLUDE (_archive_day), _archive_day AS _business_day FROM previous.{layer}.procurement_inbound'
                dest = f'{layer}.procurement_inbound'
                con.execute(f'CREATE TABLE {dest} AS {source}')
                for first, second in ((source, f'SELECT * FROM {dest}'), (f'SELECT * FROM {dest}', source)):
                    count = con.execute(f'SELECT count(*) FROM (({first}) EXCEPT ALL ({second}))').fetchone()[0]
                    if count:
                        raise RuntimeError(f'{layer} 差异：{count} 行')
                print(f'{layer}: {con.execute(f"SELECT count(*) FROM {dest}").fetchone()[0]:,} 行逐行一致', flush=True)
            con.execute('''INSERT INTO meta.partitions
                SELECT layer, 'procurement_inbound', business_day, row_count, fingerprint,
                       CASE WHEN layer='processed' AND source_fingerprint IS NOT NULL THEN 'procurement_inbound' ELSE NULL END,
                       source_fingerprint
                FROM previous.meta.partitions''')
            for oldname, newname in [('procurement_increment', 'procurement_increment__detail'),
                                     ('procurement_increment_monthly', 'procurement_increment__monthly')]:
                con.execute(f'CREATE TABLE outputs.{newname} AS SELECT * FROM previous.outputs.{oldname}')
                diff=con.execute(f'SELECT count(*) FROM ((SELECT * FROM outputs.{newname}) EXCEPT ALL (SELECT * FROM previous.outputs.{oldname}))').fetchone()[0]
                if diff:
                    raise RuntimeError(f'{newname} 差异：{diff} 行')
            records=con.execute('SELECT business_end,archive_date,generated_at,details_json FROM previous.meta.output_periods WHERE scene=?', ['采购增量分析']).fetchall()
            for end, archived, generated, text in records:
                meta=json.loads(text)
                meta['tables']=['detail','monthly']
                for item in meta.get('input_files', []):
                    if 'day' in item and 'fingerprint' in item:
                        item['source_dataset'] = 'procurement_inbound' if item.get('source_fingerprint') is not None else None
                con.execute('INSERT INTO meta.output_periods VALUES (?,?,?,?,?)', ['procurement_increment',end,archived,generated,json.dumps(meta,ensure_ascii=False)])
            con.commit()
            con.execute('DETACH previous')
        LOCAL_DATA_ROOT.mkdir(parents=True,exist_ok=True)
        shutil.copy2(old, backup)
        os.replace(tmp, old)
        print(f'数据库结构已切换；原数据库备份：{backup}', flush=True)
    finally:
        tmp.unlink(missing_ok=True)
        Path(str(tmp)+'.wal').unlink(missing_ok=True)

if __name__=='__main__':
    main()
