#!/usr/bin/env python3
"""构建发布用精简库：去掉 raw 层，只保留 outputs、processed 和 meta。

用法：
  uv run python scripts/build_publish_db.py
  uv run python scripts/build_publish_db.py --out outputs/publish/scm.duckdb

发布时用环境变量指向精简库，正式库不受影响：
  SCM_DB_PATH=outputs/publish/scm.duckdb \
  SCM_SAVINGS_DB_PATH=outputs/publish/procurement_savings_demo.duckdb \
  PORT=8080 uv run python scripts/run_dashboard.py

raw 层只服务清洗与回溯，看板和 outputs 查询都用不到，去掉可把 93.9 MB 压到 32.8 MB。
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import duckdb

from paths import DATA_ROOT, OUTPUTS_ROOT

SCHEMAS = ('processed', 'outputs', 'meta')
SOURCE = DATA_ROOT / 'scm.duckdb'
SAVINGS = DATA_ROOT / 'procurement_savings_demo.duckdb'


def tables(con: duckdb.DuckDBPyConnection, schema: str) -> list[str]:
    rows = con.execute(
        "SELECT table_name FROM duckdb_tables() "
        "WHERE database_name='src' AND schema_name=? ORDER BY table_name",
        [schema]).fetchall()
    return [name for (name,) in rows]


def build(target: Path) -> dict[str, int]:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    out = duckdb.connect(str(target))
    out.execute(f"ATTACH '{SOURCE}' AS src (READ_ONLY)")
    counts: dict[str, int] = {}
    try:
        for schema in SCHEMAS:
            out.execute(f'CREATE SCHEMA IF NOT EXISTS {schema}')
            for name in tables(out, schema):
                out.execute(f'CREATE TABLE {schema}.{name} AS SELECT * FROM src.{schema}.{name}')
                counts[f'{schema}.{name}'] = out.execute(
                    f'SELECT count(*) FROM {schema}.{name}').fetchone()[0]
        out.execute('CHECKPOINT')
    finally:
        out.close()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=OUTPUTS_ROOT / 'publish' / 'scm.duckdb')
    parser.add_argument('--with-savings', action='store_true', default=True,
                        help='一并复制采购降本演示库（默认复制）')
    args = parser.parse_args()

    counts = build(args.out)
    print(f'发布库：{args.out}（{args.out.stat().st_size / 1e6:.1f} MB）')
    for name, rows in counts.items():
        print(f'  {name}: {rows:,} 行')

    if args.with_savings and SAVINGS.exists():
        target = args.out.parent / SAVINGS.name
        shutil.copy2(SAVINGS, target)
        print(f'演示库：{target}（{target.stat().st_size / 1e6:.1f} MB）')

    print('\n本地验证：')
    print(f'  SCM_DB_PATH={args.out} PORT=8080 uv run python scripts/run_dashboard.py')


if __name__ == '__main__':
    main()
