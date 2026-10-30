#!/usr/bin/env python3
"""按指定归档执行识别分析并导出报告。

分析在 ``src/analysis/`` 注册，方案文档在 ``docs/降本识别/``。
"""
from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path
from typing import Optional

from analysis import analysis_ids, get
from paths import OUTPUTS_ROOT, PROJECT_ROOT


def build_config(analysis, args):
    """按分析自己的 Config 声明组装参数，未声明的忽略。"""
    fields = {field.name for field in dataclasses.fields(analysis.Config)}
    candidates = {
        "source": args.source,
        "snapshot_date": args.snapshot_date,
        "end_month": args.end_month,
        "window_months": args.window_months,
        "recent_filter_months": args.recent_filter_months,
        "min_recent_amount_wan": args.min_amount_wan,
    }
    kwargs = {key: value for key, value in candidates.items() if key in fields and value is not None}
    return analysis.Config(**kwargs)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="执行识别分析")
    parser.add_argument("--analysis", default="procurement-increment",
                        help=f"分析 id，可选 {', '.join(analysis_ids())}")
    parser.add_argument("--source", default=None, help="数据来源 id，默认用分析自带的默认来源")
    parser.add_argument("--read-from", choices=["local", "live"], default="local",
                        help="local=读归档（默认），live=直接读取来源文件不落盘")
    parser.add_argument("--snapshot-date", default=None, help="归档日 YYYY-MM-DD，默认最新一版")
    parser.add_argument("--end-month", default=None, help="业务结束月份 YYYY-MM，默认取数据最大月")
    parser.add_argument("--window-months", type=int, default=None, help="分析窗口月数")
    parser.add_argument("--recent-filter-months", type=int, default=None,
                        help="金额准入考察的最近月数")
    parser.add_argument("--min-amount-wan", type=float, default=None,
                        help="最近N个月累计采购金额门槛（万元）")
    parser.add_argument("--out-dir", default=None, help="输出目录，默认 <项目根>/outputs")
    parser.add_argument("--dry-run", action="store_true", help="只跑分析不写文件")
    args = parser.parse_args(argv)

    if args.snapshot_date and args.read_from == "live":
        parser.error("--snapshot-date 只能与 --read-from local 一起使用")

    try:
        analysis = get(args.analysis)
    except KeyError as exc:
        parser.error(str(exc))

    cfg = build_config(analysis, args)
    print(f"[1/3] 取数中（分析={args.analysis}，来源={cfg.source}，读取方式={args.read_from}）…")
    result = analysis.run_analysis(cfg, source=args.read_from)

    meta = result["meta"]
    range_text = ""
    if meta.get("data_month_range"):
        r = meta["data_month_range"]
        range_text = f" | 数据覆盖 {r.get('start')} ~ {r.get('end')}"
    fetched = f" | 数据转换于 {meta['fetched_at']}" if meta.get("fetched_at") else ""
    print(f"      分析窗口 {meta['window_start']} ~ {meta['window_end']}{range_text}{fetched}")
    print(f"      过滤后明细 {meta['filtered_rows']} 行 / {meta['filtered_materials']} 个物料")
    print("[2/3] 指标计算与分类完成")

    if args.dry_run:
        print("[3/3] --dry-run，跳过文件输出")
        return 0

    out_dir = Path(args.out_dir) if args.out_dir else OUTPUTS_ROOT
    exporter = getattr(analysis, "export", None)
    if exporter is None:
        print("[3/3] 该分析没有配置导出，跳过文件输出")
        return 0
    path = exporter(result, out_dir)
    try:
        shown = path.relative_to(PROJECT_ROOT)
    except ValueError:  # --out-dir 指到项目外
        shown = path
    print(f"[3/3] 导出报告：{shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
