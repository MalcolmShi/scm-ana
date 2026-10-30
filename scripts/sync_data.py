#!/usr/bin/env python3
"""把来源文件原样归档到 raw，或只查看已有的归档。"""
from __future__ import annotations

import argparse
import sys

import archive
from pipeline import ARCHIVE_MODES, all_sources, resolve_source_id, sync_source


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="把来源文件原样归档到 raw")
    parser.add_argument("--source", default=None, help="来源 id，默认处理所有已接入来源")
    parser.add_argument("--mode", choices=ARCHIVE_MODES, default="fetch-day",
                        help="fetch-day=转换当天一版全量（默认）；business-date=按业务日期逐日成档")
    parser.add_argument("--dates", default=None,
                        help="business-date 模式下只归档这些日期，逗号分隔，如 2026-09-30,2026-09-29")
    parser.add_argument("--dry-run", action="store_true", help="读取并校验，不写文件")
    parser.add_argument("--list", action="store_true", help="列出所有归档日期，不读文件、不写文件")
    args = parser.parse_args(argv)

    try:
        sources = [resolve_source_id(args.source)] if args.source else all_sources()
    except archive.DataStoreError as exc:
        parser.error(str(exc))

    dates = [day.strip() for day in args.dates.split(",") if day.strip()] if args.dates else None

    failed = False
    for source in sources:
        if args.list:
            print(f"■ {source}")
            for layer in ("raw", "processed"):
                days = archive.list_snapshots(source, layer)
                print(f"  {layer}: {', '.join(days) if days else '暂无归档'}")
            continue
        print(f"[{source}] 读取来源文件…")
        try:
            meta = sync_source(source, dry_run=args.dry_run, mode=args.mode, dates=dates)
        except Exception as exc:
            print(f"[{source}] 失败：{exc}", file=sys.stderr)
            failed = True
            continue
        if meta.get("files"):
            print(f"  来源文件：{', '.join(meta['files'])}")
        if meta["mode"] == "fetch-day":
            print(f"  归档日 {meta['snapshot_date']} | {meta['rows']} 行 | 转换于 {meta['fetched_at']}")
            print(f"  raw: {meta['target']}")
            if meta.get("previous_fetched_at"):
                print(f"  当天上一版转换于 {meta['previous_fetched_at']}")
        else:
            total = sum(item["rows"] for item in meta["written"].values())
            print(f"  按业务日期成档 {len(meta['days'])} 天 | 合计 {total} 行 | 转换于 {meta['fetched_at']}")
            for day in meta["days"][:5]:
                print(f"    {day}: {meta['written'][day]['rows']} 行")
            if len(meta["days"]) > 5:
                print(f"    …… 另有 {len(meta['days']) - 5} 天")
        if args.dry_run:
            print("  试运行通过，未写文件")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
