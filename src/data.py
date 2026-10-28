"""按来源和抓取日读写 parquet 归档，不包含接口或采购业务逻辑。"""
from __future__ import annotations

import re
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from paths import DATA_ROOT, TIMEZONE

LAYERS = ("raw", "processed")
META_SOURCE = "_source"
META_FETCHED_AT = "_fetched_at"


class DataStoreError(RuntimeError):
    """数据归档缺失或不符合约定。"""


def validate_date(value: str) -> str:
    """只接受完整的 YYYY-MM-DD，不接受月份或缩写日期。"""
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
    except (TypeError, ValueError):
        raise DataStoreError(f"归档日期必须是有效的 YYYY-MM-DD：{value}") from None
    return value


def archive_date(fetched_at: str) -> str:
    """抓取时间统一按上海时区确定归档日。"""
    stamp = datetime.fromisoformat(fetched_at)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=TIMEZONE)
    return stamp.astimezone(TIMEZONE).date().isoformat()


def snapshot_path(layer: str, source: str, snapshot_date: str) -> Path:
    """例如 raw/procurement-inbound/2026/2026-10/2026-10-25/data.parquet。"""
    if layer not in LAYERS:
        raise DataStoreError(f"未知数据层：{layer}，可选 {', '.join(LAYERS)}")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", source):
        raise DataStoreError(f"数据来源名称必须由小写字母、数字和连字符组成：{source}")
    day = validate_date(snapshot_date)
    return DATA_ROOT / layer / source / day[:4] / day[:7] / day / "data.parquet"


def list_snapshots(source: str, layer: str = "raw") -> list[str]:
    # 扫描文件前先校验来源名称和数据层。
    root = snapshot_path(layer, source, "2000-01-01").parents[3]
    days = []
    for path in root.glob("????/????-??/????-??-??/data.parquet"):
        day = path.parent.name
        try:
            expected = snapshot_path(layer, source, day)
        except DataStoreError:
            continue
        if path == expected:
            days.append(day)
    return sorted(days)


def latest_snapshot_date(source: str) -> str:
    """在 raw 和 processed 中选择最新日期，不回退到较早的 processed。"""
    days = set(list_snapshots(source, "raw")) | set(list_snapshots(source, "processed"))
    if not days:
        raise DataStoreError(f"来源「{source}」尚无数据归档；先运行 python scripts/sync_data.py --source {source}")
    return max(days)


def write_snapshot(frame: pd.DataFrame, layer: str, source: str, snapshot_date: str) -> Path:
    """原子替换当天文件，其他日期不受影响；不对数据字段做转换。"""
    path = snapshot_path(layer, source, snapshot_date)
    if META_SOURCE in frame and set(frame[META_SOURCE].dropna()) != {source}:
        raise DataStoreError("表内数据来源与归档来源不一致")
    if META_FETCHED_AT in frame:
        stamps = frame[META_FETCHED_AT].dropna().unique()
        if len(stamps) != 1 or archive_date(str(stamps[0])) != snapshot_date:
            raise DataStoreError("表内抓取时间必须唯一，且与归档日期一致")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".data-", suffix=".part", delete=False) as file:
        temporary = Path(file.name)
    try:
        frame.to_parquet(temporary, index=False)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def read_snapshot(layer: str, source: str, snapshot_date: Optional[str] = None,
                  columns: Optional[list[str]] = None) -> tuple[pd.DataFrame, dict]:
    """读取一版归档。日期省略时选择该层最新一版，元数据不受选列影响。"""
    if snapshot_date is None:
        days = list_snapshots(source, layer)
        if not days:
            raise DataStoreError(f"来源「{source}」在 {layer} 中没有归档")
        snapshot_date = days[-1]
    path = snapshot_path(layer, source, snapshot_date)
    if not path.is_file():
        raise DataStoreError(f"归档不存在：{path}")
    frame = pd.read_parquet(path)
    fetched_at = str(frame[META_FETCHED_AT].iloc[0]) if META_FETCHED_AT in frame and len(frame) else None
    meta = {
        "source": source,
        "snapshot_date": snapshot_date,
        "data_file": str(path),
        "rows": len(frame),
        "fetched_at": fetched_at,
    }
    return (frame.loc[:, columns] if columns is not None else frame), meta
