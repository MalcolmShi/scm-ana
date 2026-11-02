"""按数据层读取归档。

```python
from read_data import read_raw_procurement_inbound, read_processed_procurement_inbound, read_outputs

raw = read_raw_procurement_inbound("2026-09")
processed = read_processed_procurement_inbound("2025-10", "2026-09")
report = read_outputs("采购增量分析", "2026-09-30")
```

普通入口返回 DataFrame；``*_meta`` 同时返回读取说明。processed 会检查已知 raw
日期是否有对应归档，但现有数据没有内容指纹，无法验证归档版本或业务覆盖完整性。
``read_inbound`` / ``read_inbound_meta`` 保留供旧代码使用，仍会自动混读两层。
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import pandas as pd

import archive
from sources.procurement_inbound import (
    ALIASES,
    COLUMN_GROUPS,
    DATE_COLUMN,
    SOURCE_ID,
    archive_overview,
    field_profile,
    read_range,
    read_raw,
    read_processed,
    resolve_window,
    select_days,
)

__all__ = [
    "read_raw_procurement_inbound", "read_raw_procurement_inbound_meta",
    "read_processed_procurement_inbound", "read_processed_procurement_inbound_meta",
    "read_outputs", "read_outputs_meta",
    "DEFAULT_SOURCE", "read_inbound", "read_inbound_meta",
    "archive_overview", "select_days", "field_profile",
    "SOURCE_ID", "ALIASES", "DATE_COLUMN", "COLUMN_GROUPS",
]

#: 本项目当前唯一的数据来源
DEFAULT_SOURCE = SOURCE_ID


def read_raw_procurement_inbound_meta(start: str = "", end: str = "",
                                      columns: Optional[List[str]] = None) -> Tuple[pd.DataFrame, dict]:
    """读取采购入库 raw，返回原始字段和读取说明。"""
    return read_raw(start, end, columns)


def read_raw_procurement_inbound(start: str = "", end: str = "",
                                 columns: Optional[List[str]] = None) -> pd.DataFrame:
    """读取采购入库 raw，返回原始明细。"""
    return read_raw_procurement_inbound_meta(start, end, columns)[0]


def read_processed_procurement_inbound_meta(start: str = "", end: str = "",
                                            columns: Optional[List[str]] = None) -> Tuple[pd.DataFrame, dict]:
    """读取采购入库 processed；已知 raw 分区缺少清洗结果时失败。

    日档存在不证明上游业务覆盖完整，也不证明清洗结果与当前 raw 内容版本一致；
    现有归档没有内容指纹，调用方必须将版本一致性视为待核实。
    """
    low, high = resolve_window(start, end)
    raw_days = {day for day in archive.list_snapshots(SOURCE_ID, "raw") if low <= day <= high}
    processed_days = {day for day in archive.list_snapshots(SOURCE_ID, "processed") if low <= day <= high}
    missing = sorted(raw_days - processed_days)
    if missing:
        raise archive.DataStoreError(f"采购入库 processed 缺少 raw 对应归档日：{', '.join(missing)}")
    frame, meta = read_processed(start, end, columns)
    meta["coverage"] = {
        "raw_days": sorted(raw_days),
        "processed_days": sorted(processed_days),
        "missing_processed_days": missing,
        "missing_raw_days": sorted(processed_days - raw_days),
        "business_coverage": "未核实",
        "input_versions": "未核实",
    }
    return frame, meta


def read_processed_procurement_inbound(start: str = "", end: str = "",
                                       columns: Optional[List[str]] = None) -> pd.DataFrame:
    """读取采购入库 processed，缺少已知 raw 日档时不返回部分结果。"""
    return read_processed_procurement_inbound_meta(start, end, columns)[0]


def read_outputs_meta(scene: str, snapshot_date: str) -> Tuple[pd.DataFrame, dict]:
    """读取指定场景和业务结束日的主结果表及归档说明。

    当前归档仅支持主表，尚不支持按表名或批次读取历史结果。
    """
    return archive.read_snapshot("outputs", scene, snapshot_date)


def read_outputs(scene: str, snapshot_date: str) -> pd.DataFrame:
    """读取指定场景和业务结束日的主结果表。"""
    return read_outputs_meta(scene, snapshot_date)[0]


def read_inbound(start: str = "", end: str = "",
                 columns: Optional[List[str]] = None) -> pd.DataFrame:
    """读采购入库明细，返回一张 DataFrame —— 只有数据，没有其他返回值。

    语义只有一条：**有业务侧清洗底表读 processed，没有才读 raw**——业务分析永远
    拿到的应是当前最干净的一份。清洗确认、raw/processed 对拍这类要明确层级的读取，
    走来源层显式接口 ``sources.procurement_inbound.read_raw() / read_processed()``，
    不在本门面上加开关。

    - 一个参数：读它代表的**那一个完整周期**，``"2026-09-30"`` 当天、``"2026-09"`` 当月、``"2026"`` 当年
    - 两个参数：读两个边界之间的**范围**，两边都含，精度可以混用
    - 不传参数：全部归档

    Args:
        start: 起点或单周期，``YYYY`` / ``YYYY-MM`` / ``YYYY-MM-DD``。
        end: 终点，写法同上。
        columns: 只读这些列，不传读全列。
    """
    frame, _ = read_range(start, end, columns)
    return frame


def read_inbound_meta(start: str = "", end: str = "",
                      columns: Optional[List[str]] = None) -> Tuple[pd.DataFrame, dict]:
    """同上，但同时返回读取说明，供报告留痕。

    ``meta`` 含 ``window_start``/``window_end``（解析后的窗口）、``start``/``end``（命中的首末日档）、
    ``days``、``rows``、``layers``、``layer``、``snapshot_date``、``data_file``、``data_month_range``。
    """
    return read_range(start, end, columns)
