"""采购入库来源：读取业务导出的 Excel，并按业务日写入 raw。"""
from __future__ import annotations
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
import pandas as pd
from paths import LOCAL_DATA_ROOT, TIMEZONE

SOURCE_ID = "procurement-inbound"
DATE_COLUMN = "出入库日期"
FILE_PATTERN = "入库明细*.xlsx"
SHEET_NAME = "入库明细（含单价）"

class LocalDataError(RuntimeError):
    pass

def local_data_dir() -> Path:
    """导出文件所在目录，可用环境变量 ``SCM_ANA_LOCAL_DATA_DIR`` 指向别处。"""
    override = os.environ.get("SCM_ANA_LOCAL_DATA_DIR")
    return Path(override).expanduser() if override else LOCAL_DATA_ROOT


def list_excel_files(dir_path: Optional[Path] = None) -> List[Path]:
    """列出入库明细导出文件，按文件名排序。"""
    root = Path(dir_path) if dir_path else local_data_dir()
    if not root.is_dir():
        raise LocalDataError(
            f"本地数据目录不存在：{root}\n"
            "把上游导出的入库明细 Excel 放进该目录后重试。"
        )
    files = sorted(path for path in root.glob(FILE_PATTERN) if path.is_file())
    if not files:
        raise LocalDataError(
            f"{root} 下没有匹配「{FILE_PATTERN}」的入库明细导出文件。\n"
            f"目录现有内容：{', '.join(sorted(p.name for p in root.iterdir())) or '空'}"
        )
    return files


def read_excel(path: Path) -> pd.DataFrame:
    """读取一个导出文件的明细表，保留原始列名和值。"""
    with pd.ExcelFile(path) as book:      # 用 with 关掉文件句柄，避免批量导入时句柄泄漏
        sheet = SHEET_NAME if SHEET_NAME in book.sheet_names else book.sheet_names[0]
        return pd.read_excel(book, sheet_name=sheet)


def read_export(dir_path: Optional[Path] = None) -> Tuple[pd.DataFrame, dict]:
    """读取全部导出文件，原样合并成一张表。

    Returns:
        ``(frame, info)``：``frame`` 是合并后的原始记录；``info`` 记录文件清单和行数。
    """
    files = list_excel_files(dir_path)
    frames = []
    for path in files:
        part = read_excel(path)
        if part.empty:
            raise LocalDataError(f"导出文件没有数据行：{path}")
        frames.append(part)
    frame = pd.concat(frames, ignore_index=True)
    info = {
        "files": [path.name for path in files],
        "file_rows": {path.name: len(part) for path, part in zip(files, frames)},
        "rows": len(frame),
        "columns": list(frame.columns),
    }
    return frame, info


def split_by_business_date(frame: pd.DataFrame,
                           dates: Optional[Iterable[str]] = None) -> Tuple[Dict[str, pd.DataFrame], int]:
    """按「出入库日期」切分，每天一份；没有出入库日期的行跳过。

    Returns:
        ``(每天的表, 跳过行数)``。
    """
    if DATE_COLUMN not in frame.columns:
        raise ValueError(f"入库明细缺少业务日期列「{DATE_COLUMN}」，无法按业务日期归档")
    wanted = set(dates) if dates else None
    keys = pd.to_datetime(frame[DATE_COLUMN], errors="coerce").dt.strftime("%Y-%m-%d")
    parts: Dict[str, pd.DataFrame] = {}
    for day, part in frame.groupby(keys, sort=True):
        if not isinstance(day, str) or not day:
            continue  # 没有出入库日期的行跳过
        if wanted and day not in wanted:
            continue
        parts[day] = part.reset_index(drop=True)
    return parts, int(keys.isna().sum())


def sync_export(dates: Optional[Iterable[str]] = None, dry_run: bool = False) -> dict:
    """把入库明细导出文件按出入库日期逐日归档到 raw。

    Args:
        dates: 只归档这些业务日期（YYYY-MM-DD），为空表示全部有记录的日期。
        dry_run: 只读取和校验，不写文件。
    """
    frame, info = read_export()
    if frame.empty:
        raise ValueError("入库明细导出文件没有数据行，已中止")
    if {"_source", "_fetched_at", "_business_day"} & set(frame.columns):
        raise ValueError("源数据包含保留字段，不能覆盖")
    stamp = datetime.now(TIMEZONE).isoformat(timespec="seconds")
    frame = frame.assign(_source=SOURCE_ID, _fetched_at=stamp)

    parts, skipped = split_by_business_date(frame, dates)
    if skipped:
        raise ValueError(f'有 {skipped} 行缺少有效出入库日期，未导入')
    if not parts:
        raise ValueError("没有匹配的业务日期，未写归档")

    if not dry_run:
        from write_data import write_raw_procurement_inbound_days
        write_raw_procurement_inbound_days(parts)
    written = {day: {"rows": len(part), "path": "DuckDB raw.procurement_inbound"} for day, part in parts.items()}
    return {
        "source": SOURCE_ID, "fetched_at": stamp, "rows": len(frame),
        "files": info["files"], "file_rows": info["file_rows"],
        "status": "dry-run" if dry_run else "synced",
        "days": sorted(written), "written": written, "skipped_rows": skipped,
        "data_file": "raw.procurement_inbound",
    }
