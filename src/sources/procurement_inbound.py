"""采购入库：来源口径、取数、导入。

这个模块是「采购入库」这一个数据源的全部实现，分两块：

取数（分析走这条）
    ``read_range(start, end, columns)`` 按「出入库日期」把日档拼成一张表，
    返回 ``(DataFrame, meta)``；日常取数用 ``read_data.read_inbound()`` 即可，它只返回 DataFrame。
    日档路径 ``data/<层>/procurement-inbound/<YYYY>/<YYYY-MM>/<YYYY-MM-DD>/data.parquet``，
    有业务侧清洗底表（``processed``）就优先读底表。

导入（只在换上游导出文件、重做 raw 时跑）
    ``sync_export()`` 读 ``.local-data/`` 下的入库明细 Excel，原样转成日档写入 ``raw``：
    列名、取值、单位都不动，只追加 ``_source``、``_fetched_at`` 两列。

口径与方案见 ``notebooks/数据清洗/采购入库接入及清洗.md``。
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd

import archive
from paths import LOCAL_DATA_ROOT, PROJECT_ROOT, TIMEZONE

#: 来源 id，与归档目录名一致
SOURCE_ID = "procurement-inbound"

#: 来源别名，对话和文档里都认
ALIASES = ("采购入库", "入库明细")

#: 业务日期列：raw 日档按它切分，取数时按它裁剪区间
DATE_COLUMN = "出入库日期"

#: 归档时追加的两列元数据
META_COLUMNS = ("_source", "_fetched_at")

#: 上游导出的金额单位已经是万元，代码不做单位换算
AMOUNT_UNIT = "万元"

#: 导入用：导出文件匹配规则与工作表名
FILE_PATTERN = "入库明细*.xlsx"
SHEET_NAME = "入库明细（含单价）"

#: 字段分组，只用于阅读和清洗规划，不参与计算
COLUMN_GROUPS: Dict[str, Tuple[str, ...]] = {
    "时间": ("年份", "月份", "出入库日期", "批次效期"),
    "组织与仓库": ("公司编码", "公司名称", "公司简称", "板块", "多组织名称", "大区",
                   "仓库编码", "仓库名称", "仓库类型"),
    "单据": ("分析类型", "数据来源系统", "单据编码", "出入库类型名称", "采购类型名称",
             "是否多组织内交易"),
    "物料": ("物料编码", "物料名称", "规格", "货号", "主单位", "品牌", "主品牌",
             "产品类型", "生产地址", "生产国", "批次号"),
    "物料分类": ("一级分类", "二级分类", "三级分类", "四级分类", "五级分类", "设备试剂分类"),
    "供应商": ("供应商名称", "自定义内外部供应商（凯莱谱、生物为外部）", "财务内部外供应商类型"),
    "数量与金额": ("数量", "采购单价", "购退单价", "采购金额(万)", "税率",
                   "采购不含税单价", "购退不含税单价", "采购不含税金额(万)",
                   "成本单价", "成本金额(万)"),
    "归档元数据": META_COLUMNS,
}


class LocalDataError(RuntimeError):
    """本地导出文件缺失、不可读或没有数据行。"""


# --------------------------------------------------------------------------
# 取数：读日档 parquet
# --------------------------------------------------------------------------
def available_days() -> List[str]:
    """所有可读的归档日，raw 与 processed 取并集后按日期排序。"""
    return sorted(set(archive.list_snapshots(SOURCE_ID, "raw"))
                  | set(archive.list_snapshots(SOURCE_ID, "processed")))


def select_days(start: str = "", end: str = "") -> List[str]:
    """窗口内的日档清单。语义同 ``read_range``：一个参数=一个完整周期，两个=范围。"""
    low, high = resolve_window(start, end)
    return [day for day in available_days() if low <= day <= high]


def preferred_layer(snapshot_date: str) -> str:
    """某个归档日优先读哪一层：有清洗底表读 processed，否则读 raw。"""
    processed = archive.snapshot_path("processed", SOURCE_ID, snapshot_date)
    return "processed" if processed.is_file() else "raw"


def read_range(start: str = "", end: str = "",
               columns: Optional[Iterable[str]] = None,
               threads: int = 8) -> Tuple[pd.DataFrame, dict]:
    """按「出入库日期」读一段区间并拼接成一张表。

    Args:
        start: 起点或单周期：``YYYY`` 整年、``YYYY-MM`` 整月、``YYYY-MM-DD`` 当天。
        end: 终点，写法同上；只在读范围时给。
        columns: 只读这些列，parquet 支持列裁剪。
        threads: 并行读取日档的线程数；日档之间互不依赖，多线程只是加快 IO。

    参数个数决定语义、参数精度决定粒度（见 ``resolve_window``）。
    区间判定用记录里的「出入库日期」：目录日期只用来挑候选文件，最终逐行裁剪，
    所以日档里混了别的日期也不会多读。
    """
    low, high = resolve_window(start, end)
    whole = not ((start or "").strip() or (end or "").strip())   # 不传参数 = 全部归档
    days = [day for day in available_days() if low <= day <= high]
    if not days:
        raise archive.DataStoreError(f"采购入库归档在 {low} ~ {high} 区间没有可读文件")

    wanted = list(columns) if columns else None
    # 按出入库日期裁剪必须读到这一列，调用方没要就临时加上，裁完再丢掉。
    read_cols = wanted
    if wanted is not None and DATE_COLUMN not in wanted:
        read_cols = wanted + [DATE_COLUMN]

    plan = [(day, preferred_layer(day)) for day in days]
    paths = [archive.snapshot_path(layer, SOURCE_ID, day) for day, layer in plan]

    def load(path: Path) -> pd.DataFrame:
        return pd.read_parquet(path, columns=read_cols)

    if threads > 1 and len(paths) > 1:
        with ThreadPoolExecutor(max_workers=threads) as pool:
            frames = list(pool.map(load, paths))
    else:
        frames = [load(path) for path in paths]

    combined = pd.concat(frames, ignore_index=True)
    if not whole:
        combined = _filter_by_business_date(combined, low, high)
    if wanted is not None and DATE_COLUMN not in wanted:
        combined = combined.loc[:, wanted]

    layers: Dict[str, int] = {}
    for _, layer in plan:
        layers[layer] = layers.get(layer, 0) + 1
    meta = {
        "source": SOURCE_ID, "start": days[0], "end": days[-1],
        "window_start": low, "window_end": high,
        "snapshot_date": days[-1], "days": days, "rows": len(combined),
        "layers": layers, "layer": "processed" if set(layers) == {"processed"} else "raw",
        "data_file": str(archive.snapshot_path(plan[0][1], SOURCE_ID, days[0]).relative_to(PROJECT_ROOT)),
        "data_month_range": month_range(combined),
    }
    return combined, meta


def read_day(snapshot_date: Optional[str] = None,
             columns: Optional[Iterable[str]] = None) -> Tuple[pd.DataFrame, dict]:
    """读一个归档日；不指定日期就读最新一个归档日。等价于 ``read_range(day, day)``。"""
    day = snapshot_date if snapshot_date is not None else archive.latest_snapshot_date(SOURCE_ID)
    return read_range(day, day, columns)


# --------------------------------------------------------------------------
# 窗口解析：一个参数=一个完整周期，两个参数=范围
# --------------------------------------------------------------------------
def resolve_window(start: str = "", end: str = "") -> Tuple[str, str]:
    """把调用参数解析成闭区间的 ``(起, 止)`` 两个完整日期。

    - 都不给：全部归档（``0000-01-01`` ~ ``9999-12-31``）
    - 只给 ``start``：``start`` 代表的那一个完整周期（当天 / 当月 / 当年）
    - 只给 ``end``：从最早到 ``end`` 所在周期的期末
    - 两个都给：``start`` 期初 ~ ``end`` 期末
    """
    has_start = bool((start or "").strip())
    has_end = bool((end or "").strip())
    if not has_start and not has_end:
        return "0000-01-01", "9999-12-31"
    if not has_end:
        return _bound(start, period_end=False), _bound(start, period_end=True)
    if not has_start:
        return "0000-01-01", _bound(end, period_end=True)
    low, high = _bound(start, period_end=False), _bound(end, period_end=True)
    if low > high:
        raise archive.DataStoreError(f"起始日期 {low} 晚于结束日期 {high}，检查一下参数顺序")
    return low, high


def _bound(value: str, period_end: bool) -> str:
    """把 YYYY / YYYY-MM / YYYY-MM-DD 补成完整日期；``period_end`` 取该期最后一天。"""
    text = (value or "").strip()
    if len(text) == 4 and text.isdigit():
        text = f"{text}-12" if period_end else f"{text}-01"
    if len(text) == 7:
        try:
            span = pd.Period(text, freq="M")
        except ValueError:
            raise _bad_date(value) from None
        return span.end_time.date().isoformat() if period_end else span.start_time.date().isoformat()
    try:
        return archive.validate_date(text)
    except archive.DataStoreError:
        raise _bad_date(value) from None


def _bad_date(value: str) -> archive.DataStoreError:
    return archive.DataStoreError(
        f"日期写法不对：{value}，用 2026（整年）/ 2026-09（整月）/ 2026-09-30（当天）")


def _filter_by_business_date(frame: pd.DataFrame, low: str, high: str) -> pd.DataFrame:
    """按「出入库日期」逐行裁剪，边界都含；日期解析不出来的行剔除。"""
    if DATE_COLUMN not in frame.columns:
        raise archive.DataStoreError(f"归档缺少业务日期列「{DATE_COLUMN}」，无法按出入库日期裁剪")
    dates = pd.to_datetime(frame[DATE_COLUMN], errors="coerce")
    mask = dates.notna() & dates.ge(pd.Timestamp(low)) & dates.le(pd.Timestamp(high))
    return frame.loc[mask].reset_index(drop=True)


# --------------------------------------------------------------------------
# 观察：归档清单与字段画像
# --------------------------------------------------------------------------
def archive_overview() -> pd.DataFrame:
    """归档清单：每个日档的层、行数、文件大小，不加载数据。

    只读 parquet 元数据，几百个日档也是秒级，用于核对数据覆盖是否完整。
    """
    rows = []
    for day in available_days():
        layer = preferred_layer(day)
        path = archive.snapshot_path(layer, SOURCE_ID, day)
        rows.append({
            "归档日": day,
            "层": layer,
            "行数": archive.row_count(layer, SOURCE_ID, day),
            "大小(MB)": round(path.stat().st_size / 1024 / 1024, 2),
            "文件": str(path.relative_to(PROJECT_ROOT)),
        })
    return pd.DataFrame(rows)


def field_profile(frame: pd.DataFrame, sample: int = 3) -> pd.DataFrame:
    """字段画像：顺序、类型、分组、非空率、唯一值数、样例值。

    只做观察，不改数据；用于核对 raw 字段和规划清洗规则。
    """
    rows = []
    total = len(frame)
    for position, column in enumerate(frame.columns, start=1):
        series = frame[column]
        non_null = int(series.notna().sum())
        values = series.dropna().unique()
        rows.append({
            "序号": position,
            "字段": column,
            "分组": _group_of(column),
            "类型": str(series.dtype),
            "非空数": non_null,
            "非空率": round(non_null / total, 4) if total else 0.0,
            "唯一值": int(len(values)),
            "样例值": " | ".join(str(value)[:28] for value in values[:sample]) if len(values) else "",
        })
    return pd.DataFrame(rows)


def _group_of(column: str) -> str:
    for group, columns in COLUMN_GROUPS.items():
        if column in columns:
            return group
    return "其他"


def month_range(df: pd.DataFrame) -> Dict[str, object]:
    """从记录本身推断业务月份覆盖范围，不靠目录名或转换时间。"""
    empty: Dict[str, object] = {"start": None, "end": None, "months": [], "count": 0}
    if DATE_COLUMN in df.columns:
        labels = pd.to_datetime(df[DATE_COLUMN], errors="coerce").dt.strftime("%Y-%m").dropna()
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


# --------------------------------------------------------------------------
# 导入：上游 Excel 导出 → raw 日档
# --------------------------------------------------------------------------
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
        return book.parse(sheet_name=sheet)


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
        raise archive.DataStoreError(f"入库明细缺少业务日期列「{DATE_COLUMN}」，无法按业务日期归档")
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
        raise archive.DataStoreError("入库明细导出文件没有数据行，已中止")
    stamp = datetime.now(TIMEZONE).isoformat(timespec="seconds")
    frame = frame.assign(_source=SOURCE_ID, _fetched_at=stamp)

    parts, skipped = split_by_business_date(frame, dates)
    if not parts:
        raise archive.DataStoreError("没有匹配的业务日期，未写归档")

    written = {}
    for day, part in parts.items():
        if not dry_run:
            archive.write_snapshot(part, "raw", SOURCE_ID, day, check_fetched_at=False)
        written[day] = {"rows": len(part), "path": str(archive.snapshot_path("raw", SOURCE_ID, day))}
    return {
        "source": SOURCE_ID, "fetched_at": stamp, "rows": len(frame),
        "files": info["files"], "file_rows": info["file_rows"],
        "data_month_range": month_range(frame),
        "status": "dry-run" if dry_run else "synced",
        "days": sorted(written), "written": written, "skipped_rows": skipped,
        "data_file": str(archive.snapshot_path("raw", SOURCE_ID, sorted(written)[0])),
    }
