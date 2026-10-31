#!/usr/bin/env python3
"""分析：跑采购增量分析，导出 Excel 报告到 outputs/reports/<归档日>/。

取数与计算见 src/read_data.py、src/analysis/procurement_increment.py，
编排见 src/pipeline.py；口径见 notebooks/采购增量分析/采购增量分析.md。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

import pipeline
from analysis.procurement_increment import (
    ANALYSIS_WINDOW_MONTHS,
    MIN_RECENT_AMOUNT_WAN,
    PRIORITY_ORDER,
    RECENT_FILTER_MONTHS,
    Config,
)
from paths import PROJECT_ROOT


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="采购增量分析")
    parser.add_argument("--start-date", default=None, help="取数窗口起（含），YYYY / YYYY-MM / YYYY-MM-DD，默认全部")
    parser.add_argument("--end-date", default=None, help="取数窗口止（含），写法同上，默认全部")
    parser.add_argument("--end-month", default=None, help="业务结束月份 YYYY-MM，默认取数据最大月")
    parser.add_argument("--window-months", type=int, default=ANALYSIS_WINDOW_MONTHS, help="分析窗口月数")
    parser.add_argument("--recent-filter-months", type=int, default=RECENT_FILTER_MONTHS,
                        help="金额准入考察的最近月数")
    parser.add_argument("--min-amount-wan", type=float, default=MIN_RECENT_AMOUNT_WAN,
                        help="最近N个月累计采购金额门槛（万元）")
    parser.add_argument("--out-dir", default=None, help="输出目录，默认 <项目根>/outputs")
    parser.add_argument("--dry-run", action="store_true", help="只跑分析不写文件")
    args = parser.parse_args(argv)

    cfg = Config(
        start_date=args.start_date or "",
        end_date=args.end_date or "",
        end_month=args.end_month,
        window_months=args.window_months,
        recent_filter_months=args.recent_filter_months,
        min_recent_amount_wan=args.min_amount_wan,
    )

    print("[1/3] 取数并计算…")
    try:
        result = pipeline.analyze(
            cfg,
            out_dir=Path(args.out_dir) if args.out_dir else None,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1

    meta = result["meta"]
    months = meta.get("data_month_range") or {}
    cover = f" | 数据覆盖 {months.get('start')} ~ {months.get('end')}" if months else ""
    fetched = f" | 数据转换于 {meta['fetched_at']}" if meta.get("fetched_at") else ""
    print(f"      分析窗口 {meta['window_start']} ~ {meta['window_end']}{cover}{fetched}")
    print(f"      过滤后明细 {meta['filtered_rows']} 行 / {meta['filtered_materials']} 个物料")
    print("[2/3] 增长指标与分类完成")

    counts = result["priority_summary"].set_index("谈判优先级")["物料数"].to_dict()
    for key in PRIORITY_ORDER:
        print(f"      {key}: {int(counts.get(key, 0))}")

    if args.dry_run:
        print("[3/3] --dry-run，跳过文件输出")
        return 0

    xlsx_path = result["report_path"]
    try:
        shown = xlsx_path.relative_to(PROJECT_ROOT)
    except ValueError:  # --out-dir 指到项目外
        shown = xlsx_path
    print(f"[3/3] 导出 Excel：{shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
