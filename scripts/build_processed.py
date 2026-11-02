#!/usr/bin/env python3
"""清洗：把 data/raw 的采购入库日档清洗成 processed 明细底表，逐日归档。

第三步收口脚本（方案 §3.4）：规则来自 notebooks/采购增量分析/分析方案.md §三，
与 数据清洗确认.ipynb 的总账对拍一致。清洗是行级的，逐日清洗与整窗清洗结果一致。

    uv run python scripts/build_processed.py                 # 全部归档日
    uv run python scripts/build_processed.py --start 2025-10 --end 2026-09
    uv run python scripts/build_processed.py --dry-run       # 只校验不写
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional

from archive import DataStoreError
from clean import build_processed


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="采购入库 raw → processed 清洗归档")
    parser.add_argument("--start", default="", help="窗口起（含），YYYY / YYYY-MM / YYYY-MM-DD，默认最早")
    parser.add_argument("--end", default="", help="窗口止（含），写法同上，默认最新")
    parser.add_argument("--drop-duplicates", action="store_true",
                        help="剔除整行重复（项二待业务定夺，默认保留）")
    parser.add_argument("--dry-run", action="store_true", help="只清洗校验，不写文件")
    args = parser.parse_args(argv)

    print("[procurement-inbound] 读 raw 日档并清洗…")
    try:
        meta = build_processed(start=args.start, end=args.end,
                               drop_duplicates=args.drop_duplicates, dry_run=args.dry_run)
    except (DataStoreError, KeyError, ValueError) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1

    totals = meta["totals"]
    print(f"  归档日 {len(meta['days'])} 天（{meta['days'][0]} ~ {meta['days'][-1]}）")
    print(f"  行数：{totals['rows_before']:,} → {totals['rows_after']:,}"
          + (f"（剔重复 {totals['duplicates_dropped']:,} 行）" if totals["duplicates_dropped"] else ""))
    print(f"  含税金额(万)：{totals['amount_before_wan']:,.1f} → {totals['amount_after_wan']:,.1f}")
    print(f"  处置台账：脏字符修复 {totals['text_fixed']:,} | 供应商类型填外部 "
          f"{totals['supplier_type_filled']:,} | 单价补购退 {totals['price_filled']:,} | "
          f"采购模式归一 {totals['mode_normalized']:,}")
    if totals["duplicate_rows"] and not args.drop_duplicates:
        print(f"  提醒：检出整行重复 {totals['duplicate_rows']:,} 行，项二待业务定夺，本次按默认保留")
    print("  试运行通过，未写文件" if args.dry_run else "  归档目录：data/processed/procurement-inbound/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
