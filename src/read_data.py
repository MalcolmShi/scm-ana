"""取数门面：拿到一张 DataFrame 就完事。

```python
from read_data import read_inbound

df = read_inbound("2026-09")             # 2026 年 9 月
df = read_inbound("2026-09-30")          # 当天
df = read_inbound("2026")                # 2026 年全年
df = read_inbound("2026-01", "2026-09")  # 范围，两边都含
```

实现全部在 ``src/sources/procurement_inbound.py``（窗口解析、日档拼接、逐行裁剪），
这里只是把 ``(DataFrame, meta)`` 里的 meta 拿掉，并转出几个核对链路用的函数。
需要数据版本留痕时用 ``read_inbound_meta()``。
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import pandas as pd

from sources.procurement_inbound import (
    ALIASES,
    COLUMN_GROUPS,
    DATE_COLUMN,
    SOURCE_ID,
    archive_overview,
    field_profile,
    read_range,
    select_days,
)

__all__ = [
    "DEFAULT_SOURCE", "read_inbound", "read_inbound_meta",
    "archive_overview", "select_days", "field_profile",
    "SOURCE_ID", "ALIASES", "DATE_COLUMN", "COLUMN_GROUPS",
]

#: 本项目当前唯一的数据来源
DEFAULT_SOURCE = SOURCE_ID


def read_inbound(start: str = "", end: str = "",
                 columns: Optional[List[str]] = None) -> pd.DataFrame:
    """读采购入库明细，返回一张 DataFrame —— 只有数据，没有其他返回值。

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
