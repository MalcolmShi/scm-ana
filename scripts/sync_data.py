#!/usr/bin/env python3
"""导入：把上游导出的入库明细 Excel 按「出入库日期」逐日成档到 data/raw。

只在换导出文件、重做 raw 时用；日常取数直接读已有归档（见 read_data.py）。
读取规则：``.local-data/`` 下匹配「入库明细*.xlsx」的文件，工作表「入库明细（含单价）」。
"""
from __future__ import annotations

import argparse
import sys

from archive import list_snapshots
from sources.procurement_inbound import SOURCE_ID, sync_export


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="采购入库明细按日归档")
    parser.add_argument("--dates", default=None,
                        help="只归档这些业务日期，逗号分隔，如 2026-09-30,2026-09-29；不填则全部有记录的日期")
    parser.add_argument("--dry-run", action="store_true", help="读取并校验，不写文件")
    parser.add_argument("--list", action="store_true", help="列出已有归档日期，不读文件、不写文件")
    args = parser.parse_args(argv)

    if args.list:
        for layer in ("raw", "processed"):
            days = list_snapshots(SOURCE_ID, layer)
            tail = f"（{', '.join(days[-3:])} …）" if days else "（暂无归档）"
            print(f"  {layer}: {len(days)} 个归档日{tail}")
        return 0

    dates = [day.strip() for day in args.dates.split(",") if day.strip()] if args.dates else None
    print("[procurement-inbound] 读取入库明细导出文件…")
    try:
        meta = sync_export(dates=dates, dry_run=args.dry_run)
    except Exception as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1

    print(f"  来源文件：{', '.join(meta['files'])}")
    for name, rows in meta["file_rows"].items():
        print(f"    {name}: {rows} 行")
    total = sum(item["rows"] for item in meta["written"].values())
    print(f"  按出入库日期成档 {len(meta['days'])} 天 | 合计 {total} 行 | 转换于 {meta['fetched_at']}")
    print(f"  跳过没有出入库日期的行：{meta['skipped_rows']} 行")
    for day in meta["days"][:3]:
        print(f"    {day}: {meta['written'][day]['rows']} 行")
    if len(meta["days"]) > 3:
        print(f"    …… 另有 {len(meta['days']) - 3} 天")
    print("  试运行通过，未写文件" if args.dry_run else "  归档目录：data/raw/procurement-inbound/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
