#!/usr/bin/env python3
"""采购入库 Excel 导入 raw。"""
import argparse
import storage
from sources.procurement_inbound import sync_export


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dates', help='仅导入这些业务日，逗号分隔')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--list', action='store_true')
    args = parser.parse_args()
    if args.list:
        for layer in ('raw', 'processed'):
            parts = storage.partitions(layer, 'procurement_inbound', '0001-01-01', '9999-12-31')
            print(f"{layer}: {len(parts)} 天，最新 {parts[-1]['day'] if parts else '无'}")
        return
    info = sync_export([day.strip() for day in args.dates.split(',') if day.strip()] if args.dates else None, args.dry_run)
    print(f"{'试运行' if args.dry_run else '已导入'}：{len(info['days'])} 天，{sum(p['rows'] for p in info['written'].values()):,} 行")


if __name__ == '__main__':
    main()
