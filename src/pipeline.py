"""数据接入编排：来源读取 → raw 归档 → 归档读取。

来源实现在 ``src/sources/``（对应 ``docs/数据接入/``），归档读写在 ``src/archive.py``。
本模块只做编排，不含采购业务口径。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

import archive
from paths import PROJECT_ROOT, TIMEZONE
from sources import UnknownSourceError, get as get_source, resolve, source_ids

DEFAULT_SOURCE = "procurement-inbound"

#: 归档分档方式
#: - fetch-day：转换当天一版全量快照
#: - business-date：按记录里的业务日期逐日成档
ARCHIVE_MODES = ("fetch-day", "business-date")


def resolve_source_id(source: str) -> str:
    """把别名或来源 id 归一成来源 id。"""
    try:
        return resolve(source)
    except UnknownSourceError as exc:
        raise archive.DataStoreError(str(exc)) from None


def _metadata(frame: pd.DataFrame, meta: dict, coverage) -> dict:
    meta = {**meta, "data_month_range": coverage(frame)}
    if meta.get("data_file"):
        path = Path(meta["data_file"])
        try:
            meta["data_file"] = str(path.relative_to(PROJECT_ROOT))
        except ValueError:
            pass
    return meta


def fetch_raw(source: str = DEFAULT_SOURCE) -> tuple[pd.DataFrame, dict]:
    """读取来源的原始记录，不写入文件。"""
    source = resolve_source_id(source)
    source_class = get_source(source)
    frame, info = source_class().load()
    if frame.empty:
        raise archive.DataStoreError(f"来源「{source}」没有数据行，已中止")
    stamp = datetime.now(TIMEZONE).isoformat(timespec="seconds")
    frame = frame.assign(_source=source, _fetched_at=stamp)
    return frame, {
        "source": source, "snapshot_date": archive.archive_date(stamp),
        "fetched_at": stamp, "data_file": None, "rows": len(frame),
        "files": info.get("files", []),
        "data_month_range": source_class.coverage(frame),
    }


def split_by_business_date(frame: pd.DataFrame, date_column: str,
                           dates: Optional[Iterable[str]] = None) -> Dict[str, pd.DataFrame]:
    """按业务日期切分记录。``dates`` 为空表示全部有记录的日期。"""
    if date_column not in frame.columns:
        raise archive.DataStoreError(f"来源数据缺少业务日期列「{date_column}」，无法按业务日期分档")
    wanted = set(dates) if dates else None
    keys = pd.to_datetime(frame[date_column], errors="coerce").dt.strftime("%Y-%m-%d")
    parts: Dict[str, pd.DataFrame] = {}
    for day, part in frame.groupby(keys, sort=True):
        if not isinstance(day, str) or not day:
            continue
        if wanted and day not in wanted:
            continue
        parts[day] = part.reset_index(drop=True)
    return parts


def sync_source(source: str = DEFAULT_SOURCE, dry_run: bool = False,
                mode: str = "fetch-day", dates: Optional[Iterable[str]] = None) -> dict:
    """读取来源并写入 raw 归档。

    Args:
        mode: ``fetch-day`` 转换当天一版全量；``business-date`` 按业务日期逐日成档。
        dates: ``business-date`` 模式下只归档这些日期（YYYY-MM-DD），为空表示全部。
    """
    if mode not in ARCHIVE_MODES:
        raise archive.DataStoreError(f"未知归档方式「{mode}」，可选 {', '.join(ARCHIVE_MODES)}")
    source = resolve_source_id(source)
    source_class = get_source(source)
    raw, meta = fetch_raw(source)
    if raw.empty:
        raise archive.DataStoreError(f"来源「{source}」为空，已中止")

    if mode == "fetch-day":
        day = meta["snapshot_date"]
        target = archive.snapshot_path("raw", source, day)
        previous_stamp = None
        if target.is_file():
            _, previous_meta = archive.read_snapshot("raw", source, day)
            previous_stamp = previous_meta["fetched_at"]
        if not dry_run:
            archive.write_snapshot(raw, "raw", source, day)
        return {
            **meta, "mode": mode, "status": "dry-run" if dry_run else "synced",
            "target": str(target), "previous_fetched_at": previous_stamp,
        }

    date_column = getattr(source_class, "date_column", None)
    parts = split_by_business_date(raw, date_column, dates)
    if not parts:
        raise archive.DataStoreError("没有匹配的业务日期，未写归档")
    written = {}
    for day, part in parts.items():
        target = archive.snapshot_path("raw", source, day)
        if not dry_run:
            archive.write_snapshot(part, "raw", source, day, check_fetched_at=False)
        written[day] = {"rows": len(part), "path": str(target)}
    return {
        **meta, "mode": mode, "status": "dry-run" if dry_run else "synced",
        "days": sorted(written), "written": written,
        "target": str(archive.snapshot_path("raw", source, sorted(written)[0])),
    }


def load_source(source: str = DEFAULT_SOURCE, columns: Optional[list[str]] = None,
                snapshot_date: Optional[str] = None) -> tuple[pd.DataFrame, dict]:
    """读最新一版或指定日的归档；没有底表时读 raw。"""
    source = resolve_source_id(source)
    source_class = get_source(source)
    day = snapshot_date if snapshot_date is not None else archive.latest_snapshot_date(source)
    processed_path = archive.snapshot_path("processed", source, day)
    layer = "processed" if processed_path.is_file() else "raw"
    frame, meta = archive.read_snapshot(layer, source, day)
    meta = _metadata(frame, meta, source_class.coverage)
    meta["layer"] = layer
    return (frame.loc[:, columns] if columns is not None else frame), meta


def load_from_source(source: str = DEFAULT_SOURCE,
                      columns: Optional[list[str]] = None) -> tuple[pd.DataFrame, dict]:
    """实时读取来源，不落盘。"""
    source = resolve_source_id(source)
    raw, meta = fetch_raw(source)
    return (raw.loc[:, columns] if columns is not None else raw), meta


def all_sources() -> List[str]:
    """已接入的来源 id。"""
    return source_ids()
