#!/usr/bin/env python3
"""同步来源数据，或只查看已有的按日归档。"""
from __future__ import annotations

import argparse
import sys

import data
from pipeline import SOURCE_HANDLERS, resolve_source_id, sync_source


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="按抓取日归档原始数据和分析底表")
    parser.add_argument("--source", default=None, help="来源名称，默认同步所有已接入来源")
    parser.add_argument("--dry-run", action="store_true", help="拉取并验证清洗，不写文件")
    parser.add_argument("--list", action="store_true", help="列出本地所有归档日期，不联网、不写文件")
    args = parser.parse_args(argv)
    try:
        sources = [resolve_source_id(args.source)] if args.source else list(SOURCE_HANDLERS)
    except data.DataStoreError as exc:
        parser.error(str(exc))
    failed = False
    for source in sources:
        if args.list:
            print(f"■ {source}")
            for layer in ("raw", "processed"):
                days = data.list_snapshots(source, layer)
                print(f"  {layer}: {', '.join(days) if days else '暂无归档'}")
            continue
        print(f"[{source}] 拉取并清洗…")
        try:
            meta = sync_source(source, dry_run=args.dry_run)
        except Exception as exc:
            print(f"[{source}] 失败：{exc}", file=sys.stderr)
            failed = True
            continue
        print(f"  归档日 {meta['snapshot_date']} | {meta['rows']} 行 | 抓取于 {meta['fetched_at']}")
        print(f"  raw: {meta['target']}\n  processed: {meta['processed']}")
        if meta.get("previous_fetched_at"):
            print(f"  当天上一版抓取于 {meta['previous_fetched_at']}")
        if args.dry_run:
            print("  试运行通过，未写文件")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
