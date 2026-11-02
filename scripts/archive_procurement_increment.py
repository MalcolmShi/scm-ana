#!/usr/bin/env python3
"""按月归档采购增量；默认上月，也可指定历史月份。"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from analysis.procurement_increment import analyze
import storage
from write_data import write_outputs
from paths import PROJECT_ROOT, TIMEZONE
from read_data import read_processed_procurement_inbound_meta


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_end_month(value: str | None = None, now: datetime | None = None) -> pd.Period:
    """未指定时按上海时间取上一个完整自然月。"""
    if value is None:
        now = now or datetime.now(TIMEZONE)
        if now.tzinfo is None:
            raise ValueError("当前时间必须带时区")
        return pd.Period(now.astimezone(TIMEZONE).strftime("%Y-%m"), freq="M") - 1
    if not re.fullmatch(r"\d{4}-\d{2}", value):
        raise ValueError("结束月必须为 YYYY-MM")
    try:
        return pd.Period(value, freq="M")
    except (ValueError, TypeError) as exc:
        raise ValueError("结束月必须为有效的 YYYY-MM") from exc


def validate_months(days: list[str], end: pd.Period) -> None:
    """缺少整月归档不能当成零采购。"""
    expected = pd.period_range(end - 11, end, freq="M")
    available = {day[:7] for day in days}
    missing = [str(month) for month in expected if str(month) not in available]
    if missing:
        raise ValueError(f"processed 缺少月份：{', '.join(missing)}；请先核实并补齐数据")


def main() -> None:
    parser = argparse.ArgumentParser(description="采购增量分析及 DuckDB 归档")
    parser.add_argument("end_month", nargs="?", help="业务结束月 YYYY-MM；默认上海时间的上月")
    args = parser.parse_args()
    try:
        month = resolve_end_month(args.end_month)
    except ValueError as exc:
        parser.error(str(exc))
    start = (month - 11).start_time.date().isoformat()
    end = month.end_time.date().isoformat()
    frame, source_meta = read_processed_procurement_inbound_meta(start, end)
    try:
        validate_months(source_meta["coverage"]["processed_days"], month)
    except ValueError as exc:
        parser.exit(1, f"无法归档：{exc}\n")
    tables, analysis_meta = analyze(frame, str(month))
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        revision = None
    code_paths = [PROJECT_ROOT / "src/analysis/procurement_increment.py", Path(__file__),
                  PROJECT_ROOT / "src/storage.py", PROJECT_ROOT / "src/read_data.py", PROJECT_ROOT / "src/write_data.py"]
    meta = {"window_start": start, "window_end": end,
            "coverage": source_meta["coverage"], "analysis": analysis_meta,
            "rule_version": "procurement-increment-2026-11-02", "code_revision": revision,
            "code_sha256": {str(p.relative_to(PROJECT_ROOT)): digest(p) for p in code_paths},
            "parameters": {"end_month": str(month), "months": 12, "six_month_amount_wan_gt": 5,
                           "recent_month_quantity_gt": 0},
            "scope_note": "monthly 包含全部通过金额门槛的物料；detail 另要求近三个月每月净采购量大于零。",
            "coverage_note": "业务覆盖及 processed 对应 raw 的历史版本未经独立核实。"}
    meta["inputs"] = [{"layer": "processed", "dataset": "procurement_inbound", "start": start, "end": end, "partitions": source_meta["partitions"]}]
    write_outputs("procurement_increment", end, tables, meta)
    _, saved = storage.read_outputs("procurement_increment", end)
    print(f"业务月份：{month}；归档日期：{saved['archive_date']}\n数据库：{storage.DATABASE}\n采购增量报表：{len(tables['detail'])} 行；物料月度表：{len(tables['monthly'])} 行")
    print("业务覆盖和上游版本：待核实")


if __name__ == "__main__":
    main()
