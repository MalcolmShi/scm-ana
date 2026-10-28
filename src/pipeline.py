"""数据来源的取数、清洗与归档流程。固定接口规则仍属于各自的客户端。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

import data
from cleaning.procurement_inbound import clean_procurement, month_range
from connectors.procurement_inbound import ProcurementInboundClient
from envfile import load_env
from paths import PROJECT_ROOT, TIMEZONE

DEFAULT_SOURCE = "procurement-inbound"

# 新来源只在这里选择实际的取数与清洗函数，不用 JSON 描述接口协议。
SOURCE_HANDLERS = {
    DEFAULT_SOURCE: (ProcurementInboundClient, clean_procurement, month_range),
}
SOURCE_ALIASES = {"采购入库": DEFAULT_SOURCE, "入库明细": DEFAULT_SOURCE}


def resolve_source_id(source: str) -> str:
    source = SOURCE_ALIASES.get(source, source)
    if source not in SOURCE_HANDLERS:
        raise data.DataStoreError(f"未知数据来源「{source}」，可选 {', '.join(SOURCE_HANDLERS)}")
    return source


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
    """实时读取原始记录，不写入文件。"""
    source = resolve_source_id(source)
    client_factory, _, coverage = SOURCE_HANDLERS[source]
    load_env()
    client = client_factory()
    rows = client.fetch_all()
    if not rows:
        raise data.DataStoreError(f"来源「{source}」接口返回 0 行，已中止")
    stamp = datetime.now(TIMEZONE).isoformat(timespec="seconds")
    frame = pd.DataFrame(rows).assign(_source=source, _fetched_at=stamp)
    return frame, {
        "source": source, "snapshot_date": data.archive_date(stamp),
        "fetched_at": stamp, "data_file": None, "rows": len(frame),
        "endpoint": client.url, "data_month_range": coverage(frame),
    }


def sync_source(source: str = DEFAULT_SOURCE, dry_run: bool = False) -> dict:
    """验证完整输入后，按抓取日保存 raw 和 processed。"""
    source = resolve_source_id(source)
    _, clean, _ = SOURCE_HANDLERS[source]
    raw, meta = fetch_raw(source)
    if raw.empty:
        raise data.DataStoreError(f"来源「{source}」为空，已中止")
    processed = clean(raw)
    day = meta["snapshot_date"]
    raw_path = data.snapshot_path("raw", source, day)
    processed_path = data.snapshot_path("processed", source, day)
    previous_stamp = None
    if raw_path.is_file():
        _, previous_meta = data.read_snapshot("raw", source, day)
        previous_stamp = previous_meta["fetched_at"]
    if not dry_run:
        data.write_snapshot(raw, "raw", source, day)
        data.write_snapshot(processed, "processed", source, day)
    return {
        **meta, "status": "dry-run" if dry_run else "synced",
        "target": str(raw_path), "processed": str(processed_path),
        "previous_fetched_at": previous_stamp,
    }


def prepare_source(source: str = DEFAULT_SOURCE, snapshot_date: Optional[str] = None) -> dict:
    """用指定日 raw 重建同一天的 processed，不重新取数。"""
    source = resolve_source_id(source)
    _, clean, coverage = SOURCE_HANDLERS[source]
    raw, meta = data.read_snapshot("raw", source, snapshot_date)
    processed = clean(raw)
    path = data.write_snapshot(processed, "processed", source, meta["snapshot_date"])
    return _metadata(processed, {**meta, "data_file": str(path)}, coverage)


def load_source(source: str = DEFAULT_SOURCE, columns: Optional[list[str]] = None,
                snapshot_date: Optional[str] = None) -> tuple[pd.DataFrame, dict]:
    """读最新一版或指定日的底表，必要时只从同一天 raw 重建。"""
    source = resolve_source_id(source)
    _, _, coverage = SOURCE_HANDLERS[source]
    day = snapshot_date if snapshot_date is not None else data.latest_snapshot_date(source)
    raw_path = data.snapshot_path("raw", source, day)
    processed_path = data.snapshot_path("processed", source, day)
    if raw_path.is_file() and (not processed_path.is_file() or raw_path.stat().st_mtime_ns > processed_path.stat().st_mtime_ns):
        prepare_source(source, day)
    frame, meta = data.read_snapshot("processed", source, day)
    meta = _metadata(frame, meta, coverage)
    return (frame.loc[:, columns] if columns is not None else frame), meta


def load_from_connector(source: str = DEFAULT_SOURCE,
                        columns: Optional[list[str]] = None) -> tuple[pd.DataFrame, dict]:
    """实时读取并执行同样的清洗，不保存归档。"""
    source = resolve_source_id(source)
    _, clean, _ = SOURCE_HANDLERS[source]
    raw, meta = fetch_raw(source)
    frame = clean(raw)
    return (frame.loc[:, columns] if columns is not None else frame), meta
