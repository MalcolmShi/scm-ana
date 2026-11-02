#!/usr/bin/env python3
"""采购入库清洗：raw → processed，默认保留重复行。"""
import argparse
from clean.procurement_inbound import clean_inbound
from read_data import read_raw_procurement_inbound_meta, resolve_window
from write_data import write_processed_procurement_inbound_days
import storage


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', default='')
    parser.add_argument('--end', default='')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    low, high = resolve_window(args.start, args.end)
    parts = storage.partitions('raw', 'procurement_inbound', low, high)
    if not parts:
        parser.error('指定窗口没有 raw 数据')
    rows_before = rows_after = 0
    cleaned_parts = {}
    for part in parts:
        day = part['day']
        raw, _ = read_raw_procurement_inbound_meta(day)
        cleaned, audit = clean_inbound(raw)
        if not args.dry_run:
            cleaned_parts[day] = cleaned
        rows_before += audit['rows_before']; rows_after += audit['rows_after']
    if not args.dry_run:
        write_processed_procurement_inbound_days(cleaned_parts, raw_fingerprints={part["day"]: part["fingerprint"] for part in parts})
    print(f"{'试运行' if args.dry_run else '已清洗'}：{len(parts)} 天，{rows_before:,} → {rows_after:,} 行")


if __name__ == '__main__':
    main()
