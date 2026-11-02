#!/usr/bin/env python3
"""导出钉钉多维表派发任务；默认最新归档周期的优先谈判物料，负责人按五级分类规则识别。"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

import storage
from dispatch_rules import DEFAULT_OWNER, OWNER_USER_IDS, owner_for
from paths import PROJECT_ROOT, TIMEZONE
from read_data import read_outputs

SCENE = 'procurement_increment'
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / 'outputs'

# 钉钉多维表字段：顺序即建表顺序（业务结束月为首列/主字段），导出记录只包含这些字段
PERCENT = {'最近1个月环比增长率': '最近1个月环比增长率%',
           '单一供应商供货占比': '单一供应商供货占比%',
           '最近3个月采购量占比': '最近3个月采购量占比%'}
FIELDS = ['业务结束月', '物料编码', '物料名称', '规格', '主单位', '五级分类',
          '核心供应商名称', '谈判优先级', '采购负责人', '任务状态',
          '最近12个月采购净量', '最近12个月含税金额万', '核心供应商含税金额万',
          '采购量趋势指数', '连续增长月数',
          *PERCENT.values()]


def resolve_end_month(value: str | None, now: datetime | None = None) -> str:
    """空值时取已归档的最新业务结束月；否则校验 YYYY-MM。"""
    if value is None:
        with storage.connect(read_only=True) as con:
            rows = con.execute(
                'SELECT max(business_end) FROM meta.output_periods WHERE scene=?', [SCENE]).fetchall()
        latest = rows[0][0] if rows else None
        if latest is None:
            raise ValueError('outputs 尚无归档周期，请先运行 archive_procurement_increment.py')
        return pd.Timestamp(latest).strftime('%Y-%m')
    if not re.fullmatch(r'\d{4}-\d{2}', value):
        raise ValueError('结束月必须为 YYYY-MM')
    return value


def category_map() -> pd.DataFrame:
    """物料 → 五级分类；一个物料出现多个分类时取记录最多的一档。"""
    with storage.connect(read_only=True) as con:
        return con.execute('''
            SELECT 物料编码, arg_max(五级分类, n) AS 五级分类 FROM (
                SELECT 物料编码, 五级分类, count(*) AS n
                FROM processed.procurement_inbound GROUP BY 物料编码, 五级分类)
            GROUP BY 物料编码''').fetchdf()


def build_records(frame: pd.DataFrame, categories: pd.DataFrame) -> tuple[list[dict], dict]:
    merged = frame.merge(categories, on='物料编码', how='left')
    merged['采购负责人'] = merged['五级分类'].map(owner_for)
    merged['任务状态'] = '待处理'
    for source, target in PERCENT.items():
        merged[target] = (pd.to_numeric(merged[source], errors='coerce') * 100).round(1)
    records = []
    for row in merged.to_dict('records'):
        record = {}
        for field in FIELDS:
            value = row.get(field)
            if pd.isna(value):
                record[field] = None
            elif isinstance(value, float):
                record[field] = round(value, 4)
            elif isinstance(value, int) and not isinstance(value, bool):
                record[field] = int(value)
            elif field == '物料编码':
                record[field] = str(int(value))
            else:
                record[field] = str(value)
        records.append(record)
    unmapped = merged[merged['采购负责人'] == DEFAULT_OWNER]
    unmapped_summary = (unmapped.groupby(merged['五级分类'].fillna('<空>'))
                        .size().sort_values(ascending=False).to_dict())
    return records, unmapped_summary


def main() -> None:
    parser = argparse.ArgumentParser(description='导出钉钉多维表派发任务 JSON')
    parser.add_argument('end_month', nargs='?', help='业务结束月 YYYY-MM；默认最新归档周期')
    parser.add_argument('--priority', default='优先谈判',
                        help='谈判优先级过滤；传 all 表示不过滤（默认 优先谈判）')
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT_DIR, help='JSON 输出目录')
    args = parser.parse_args()
    try:
        month = resolve_end_month(args.end_month)
    except ValueError as exc:
        parser.error(str(exc))
    end = pd.Period(month, freq='M').end_time.date().isoformat()
    frame = read_outputs(SCENE, end)
    if args.priority != 'all':
        frame = frame[frame['谈判优先级'] == args.priority]
    if frame.empty:
        parser.exit(1, f'没有可派发的记录：{month} / {args.priority}\n')
    records, unmapped = build_records(frame, category_map())
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / f'dingtalk_dispatch_{month}.json'
    payload = {
        'scene': SCENE, 'end_month': month, 'priority': args.priority,
        'generated_at': datetime.now(TIMEZONE).isoformat(timespec='seconds'),
        'rows': len(records), 'fields': FIELDS,
        'unmapped_categories': unmapped,
        'owner_summary': pd.Series([r['采购负责人'] for r in records]).value_counts().to_dict(),
        # 钉钉人员字段写入用：姓名 → userId；待分配不在映射中，写空
        'owner_user_ids': {name: uid for name, uid in OWNER_USER_IDS.items()
                           if name in {r['采购负责人'] for r in records}},
        'records': records,
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
    owners = '；'.join(f'{name} {count} 条' for name, count in payload['owner_summary'].items())
    print(f'业务月份：{month}；优先级：{args.priority}；派发任务：{len(records)} 条')
    print(f'负责人分布：{owners}')
    if unmapped:
        detail = '；'.join(f'{cat} {count} 条' for cat, count in unmapped.items())
        print(f'未匹配规则的分类（{DEFAULT_OWNER}）：{detail}')
    print(f'输出文件：{output}')


if __name__ == '__main__':
    main()
