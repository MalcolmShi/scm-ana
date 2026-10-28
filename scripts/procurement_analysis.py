#!/usr/bin/env python3
"""按指定归档执行采购增量分析并导出报告。"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Optional

from paths import OUTPUTS_ROOT, PROJECT_ROOT
from procurement import (
    ANALYSIS_WINDOW_MONTHS,
    DEFAULT_SOURCE,
    MIN_RECENT_AMOUNT_WAN,
    PRIORITY_ORDER,
    RECENT_FILTER_MONTHS,
    Config,
    run_analysis,
)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="采购增量分析")
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="数据来源 id（procurement-inbound）")
    parser.add_argument("--read-from", choices=["local", "connector"], default="local",
                        help="local=读 data/processed/（默认），connector=直接走数据源接口拉取")
    parser.add_argument("--snapshot-date", default=None, help="归档抓取日 YYYY-MM-DD，默认最新一版")
    parser.add_argument("--end-month", default=None, help="业务结束月份 YYYY-MM，默认取数据最大月")
    parser.add_argument("--window-months", type=int, default=ANALYSIS_WINDOW_MONTHS, help="分析窗口月数")
    parser.add_argument("--recent-filter-months", type=int, default=RECENT_FILTER_MONTHS,
                        help="金额准入考察的最近月数")
    parser.add_argument("--min-amount-wan", type=float, default=MIN_RECENT_AMOUNT_WAN,
                        help="最近N个月累计采购金额门槛（万元）")
    parser.add_argument("--out-dir", default=None, help="输出目录，默认 <项目根>/outputs")
    parser.add_argument("--dry-run", action="store_true", help="只跑分析不写文件")
    args = parser.parse_args(argv)

    if args.snapshot_date and args.read_from == "connector":
        parser.error("--snapshot-date 只能与 --read-from local 一起使用")

    cfg = Config(
        source=args.source,
        snapshot_date=args.snapshot_date,
        end_month=args.end_month,
        window_months=args.window_months,
        recent_filter_months=args.recent_filter_months,
        min_recent_amount_wan=args.min_amount_wan,
    )
    print(f"[1/4] 取数中（来源={cfg.source}，读取方式={args.read_from}）…")
    result = run_analysis(cfg, source=args.read_from)

    meta = result["meta"]
    report_df = result["report"]
    range_text = ""
    if meta.get("data_month_range"):
        r = meta["data_month_range"]
        range_text = f" | 数据覆盖 {r.get('start')} ~ {r.get('end')}"
    fetched = f" | 数据抓取于 {meta['fetched_at']}" if meta.get("fetched_at") else ""
    print(f"      分析窗口 {meta['window_start']} ~ {meta['window_end']}{range_text}{fetched}")
    print(f"      过滤后明细 {meta['filtered_rows']} 行 / {meta['filtered_materials']} 个物料")

    print("[2/4] 增长指标与分类完成")
    counts = result["priority_summary"].set_index("谈判优先级")["物料数"].to_dict()
    for key in PRIORITY_ORDER:
        print(f"      {key}: {int(counts.get(key, 0))}")

    if args.dry_run:
        print("[3/4] --dry-run，跳过文件输出")
        print("[4/4] 完成")
        return 0

    out_dir = Path(args.out_dir) if args.out_dir else OUTPUTS_ROOT
    reports_dir = out_dir / "reports" / meta["snapshot_date"]
    reports_dir.mkdir(parents=True, exist_ok=True)

    from reporting.excel import export_excel

    end_month = result["report_end_month"]
    xlsx_path = reports_dir / f"{end_month}采购增量分析报告.xlsx"
    try:
        shown = xlsx_path.relative_to(PROJECT_ROOT)
    except ValueError:  # --out-dir 指到项目外
        shown = xlsx_path
    print(f"[3/4] 导出 Excel：{shown}")
    export_excel(result, xlsx_path)
    print("[4/4] 完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
