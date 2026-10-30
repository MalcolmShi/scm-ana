"""本地 Excel 数据源：读取项目 ``.local-data/`` 下的上游导出文件。

本模块只负责把 Excel 读成 DataFrame：列名、取值和单位保持导出文件的原样，
不做任何字段改名、单位换算或格式转换。清洗由业务侧单独处理。

用法
----
::

    from sources.local_excel import list_local_files, load_local_inbound

    list_local_files()                    # 本地有哪些导出文件
    frame, info = load_local_inbound()    # 读成 raw 表
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from paths import LOCAL_DATA_ROOT

#: 导出文件默认匹配规则。文件名形如「入库明细（2026）.xlsx」，个别带空格也匹配。
DEFAULT_PATTERN = "入库明细*.xlsx"

#: 导出文件里的明细工作表名；文件没有这个表时退回第一个表。
DEFAULT_SHEET = "入库明细（含单价）"


class LocalDataError(RuntimeError):
    """本地数据目录缺失、文件不可读或内容为空。"""


def local_data_dir(dir_path: Optional[Path] = None) -> Path:
    """本地数据目录，可用环境变量 ``SCM_ANA_LOCAL_DATA_DIR`` 覆盖。"""
    if dir_path is not None:
        return Path(dir_path)
    override = os.environ.get("SCM_ANA_LOCAL_DATA_DIR")
    return Path(override).expanduser() if override else LOCAL_DATA_ROOT


def list_local_files(dir_path: Optional[Path] = None,
                     pattern: str = DEFAULT_PATTERN) -> List[Path]:
    """列出本地导出文件，按文件名排序。"""
    root = local_data_dir(dir_path)
    if not root.is_dir():
        raise LocalDataError(
            f"本地数据目录不存在：{root}\n"
            "把上游导出的 Excel 放进该目录后重试。"
        )
    files = sorted(path for path in root.glob(pattern) if path.is_file())
    if not files:
        raise LocalDataError(
            f"{root} 下没有匹配「{pattern}」的 Excel 文件。\n"
            f"目录现有内容：{', '.join(sorted(p.name for p in root.iterdir())) or '空'}"
        )
    return files


def read_excel(path: Path, sheet: str = DEFAULT_SHEET) -> pd.DataFrame:
    """读取单个 Excel 的明细表，保留原始列名和值。"""
    book = pd.ExcelFile(path)
    name = sheet if sheet in book.sheet_names else book.sheet_names[0]
    return pd.read_excel(path, sheet_name=name)


def load_local_inbound(dir_path: Optional[Path] = None,
                       files: Optional[Iterable[Path]] = None,
                       pattern: str = DEFAULT_PATTERN) -> Tuple[pd.DataFrame, dict]:
    """读取一个或多个导出文件，原样合并成一张表。

    Returns:
        ``(frame, info)``：``frame`` 是合并后的原始记录，列名和取值保持导出文件原样；
        ``info`` 记录文件清单和行数，便于在归档前先打印出来核对。
    """
    paths = [Path(path) for path in files] if files is not None else list_local_files(dir_path, pattern)
    if not paths:
        raise LocalDataError("没有可读取的本地导出文件。")

    frames = []
    for path in paths:
        if not path.is_file():
            raise LocalDataError(f"导出文件不存在：{path}")
        part = read_excel(path)
        if part.empty:
            raise LocalDataError(f"导出文件没有数据行：{path}")
        frames.append(part)

    frame = pd.concat(frames, ignore_index=True)
    info = {
        "files": [path.name for path in paths],
        "file_rows": {path.name: len(part) for path, part in zip(paths, frames)},
        "rows": len(frame),
        "columns": list(frame.columns),
    }
    return frame, info


def month_range(df: pd.DataFrame) -> Dict[str, Any]:
    """从数据本身推断业务月份覆盖范围，不靠目录名或转换时间。"""
    empty: Dict[str, Any] = {"start": None, "end": None, "months": [], "count": 0}

    date_column = next((column for column in ("出入库日期", "采购入库日期") if column in df.columns), None)
    if date_column:
        labels = pd.to_datetime(df[date_column], errors="coerce").dt.strftime("%Y-%m").dropna()
    elif {"年份", "月份"} <= set(df.columns):
        years = pd.to_numeric(df["年份"], errors="coerce")
        months = pd.to_numeric(df["月份"], errors="coerce")
        valid = years.notna() & months.notna()
        labels = (
            years[valid].astype(int).astype(str)
            + "-"
            + months[valid].astype(int).astype(str).str.zfill(2)
        )
    else:
        return empty

    unique = sorted(set(labels.tolist()))
    if not unique:
        return empty
    return {"start": unique[0], "end": unique[-1], "months": unique, "count": len(unique)}


class LocalExcelSource:
    """本地 Excel 来源的通用实现，子类声明来源口径。"""

    source_id = ""
    aliases: Tuple[str, ...] = ()
    #: 业务日期列，按业务日期分档时用它切分
    date_column: Optional[str] = None
    #: 导出文件匹配规则
    pattern = DEFAULT_PATTERN
    #: 明细工作表名
    sheet = DEFAULT_SHEET

    def __init__(self, dir_path: Optional[Path] = None,
                 files: Optional[Iterable[Path]] = None) -> None:
        self.dir_path = dir_path
        self.files = files

    def load(self) -> Tuple[pd.DataFrame, dict]:
        """读取本地导出文件，返回原始表和来源信息。"""
        return load_local_inbound(self.dir_path, self.files, self.pattern)

    @staticmethod
    def coverage(frame: pd.DataFrame) -> Dict[str, Any]:
        """业务月份覆盖范围，由记录里的日期算出。"""
        return month_range(frame)


def _main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="查看本地采购入库导出文件")
    parser.add_argument("--dir", default=None, help="本地数据目录，默认 .local-data/")
    parser.add_argument("--pattern", default=DEFAULT_PATTERN, help="文件匹配规则")
    parser.add_argument("--read", action="store_true", help="读一遍并打印行数和列名")
    args = parser.parse_args(argv)

    try:
        files = list_local_files(args.dir, args.pattern)
    except LocalDataError as exc:
        print(f"错误：{exc}", flush=True)
        return 2

    for path in files:
        print(f"  {path.name}  ({path.stat().st_size / 1024 / 1024:.1f} MB)")
    if not args.read:
        return 0

    frame, info = load_local_inbound(args.dir, files)
    print(f"\n合计 {info['rows']} 行，{len(info['columns'])} 列")
    for name, rows in info["file_rows"].items():
        print(f"  {name}: {rows} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
