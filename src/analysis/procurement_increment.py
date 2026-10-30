#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采购增量分析计算。口径见 docs/降本识别/采购增量分析.md，探索见 notebooks/采购增量分析.ipynb。"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from read_data import DEFAULT_SOURCE, read_inbound_meta

#: 分析口径常量 —— 与方案文档一一对应，改动请同步方案文档
TARGET_BOARDS: List[str] = ["检验", "检验产品", "诊断"]
ANALYSIS_WINDOW_MONTHS = 12
RECENT_FILTER_MONTHS = 6
MIN_RECENT_AMOUNT_WAN = 5.0
RECENT_ZERO_MONTHS = 3
TOP_N = 15

#: 供应商范围：只保留市场交易，关联单位不是议价对象
SUPPLIER_TYPE_COLUMN = "财务内部外供应商类型"
EXTERNAL_SUPPLIER_TYPES: Tuple[str, ...] = ("外部单位",)

#: 优先级阈值
TREND_INDEX_PRIORITY = 0.20   # 优先谈判：采购量趋势指数门槛
MOM_GROWTH_PRIORITY = 0.10    # 优先谈判：最近1个月环比增长率门槛
STREAK_PRIORITY = 2           # 优先谈判：连续增长月数门槛
TREND_INDEX_WATCH = 0.10     # 观察：采购量趋势指数门槛
RECENT_SHARE_WATCH = 0.30    # 观察：最近3个月采购量占比门槛

#: 无基期物料单列一档，不占谈判名额（趋势指数无法计算，不参与增长排名）
NEW_MATERIAL_LABEL = "新引入"
PRIORITY_ORDER = {"优先谈判": 0, "观察": 1, "不建议": 2, NEW_MATERIAL_LABEL: 3}
PRIORITY_LABELS: List[str] = list(PRIORITY_ORDER)

ANALYSIS_COLUMNS: List[str] = [
    "板块", "物料编码", "物料名称", "规格", "主单位", "品牌", "主品牌",
    "一级分类", "二级分类", "三级分类", "四级分类", "五级分类",
    "设备试剂分类", "进口&国产", "供应商名称", SUPPLIER_TYPE_COLUMN,
    "采购入库年份", "采购入库月份",
    "数量合计", "采购不含税金额(万)", "采购不含税单价",
]

MATERIAL_DIM_COLUMNS: List[str] = [
    "物料名称", "规格", "主单位", "品牌", "主品牌",
    "一级分类", "二级分类", "三级分类", "四级分类", "五级分类",
    "设备试剂分类", "进口&国产",
]

REPORT_COLUMNS: List[str] = [
    "物料编码", "物料名称", "规格", "品牌", "五级分类", "核心供应商名称",
    "最近3个月平均采购量", "前3个月平均采购量", "采购量趋势指数",
    "最近1个月环比增长率", "连续增长月数", "最近3个月采购量占比",
    "最近12个月累计采购不含税金额(万)", "单一供应商供货占比", "谈判优先级",
]


@dataclass
class Config:
    """一次运行的分析配置，便于复现与留痕。"""
    start_date: str = ""                      # 归档区间起（含），空表示全部归档日
    end_date: str = ""                        # 归档区间止（含），空表示全部归档日
    end_month: Optional[str] = None           # 业务结束月 YYYY-MM；None 表示取数据最大月
    window_months: int = ANALYSIS_WINDOW_MONTHS
    recent_filter_months: int = RECENT_FILTER_MONTHS
    min_recent_amount_wan: float = MIN_RECENT_AMOUNT_WAN
    external_only: bool = True                # 只保留外部单位，排除关联交易
    boards: List[str] = None

    def __post_init__(self) -> None:
        if self.boards is None:
            self.boards = list(TARGET_BOARDS)


# --------------------------------------------------------------------------
# 取数
# --------------------------------------------------------------------------
def load_source_data(cfg: Config) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """载入采购入库明细。

    读 ``data/raw/procurement-inbound/`` 下按出入库日期成档的 parquet，
    按解析后的窗口拼接；某个归档日有业务侧清洗底表时优先读底表。

    Returns:
        ``(DataFrame, meta)``，meta 记录实际使用的窗口、日档、数据文件与覆盖月份。
    """
    return read_inbound_meta(cfg.start_date, cfg.end_date)


# --------------------------------------------------------------------------
# 阶段一：范围过滤与清洗
# --------------------------------------------------------------------------
def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """把导出文件的原始列名对齐到分析口径。

    只做字段名映射，不改数值、不做单位换算：导出文件用「数量」「年份」「月份」，
    清洗后的底表用「数量合计」「采购入库年份」「采购入库月份」。
    """
    frame = df.copy()
    if "数量合计" not in frame.columns and "数量" in frame.columns:
        frame = frame.rename(columns={"数量": "数量合计"})
    if "采购入库年份" not in frame.columns and "年份" in frame.columns:
        frame = frame.rename(columns={"年份": "采购入库年份"})
    if "采购入库月份" not in frame.columns and "月份" in frame.columns:
        frame = frame.rename(columns={"月份": "采购入库月份"})
    return frame


def clean_and_filter(df: pd.DataFrame, cfg: Config) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """板块过滤、供应商范围过滤、字段清洗、12 个月窗口裁剪、最近 N 个月金额准入。"""
    df = normalize_columns(df)
    available_cols = [c for c in ANALYSIS_COLUMNS if c in df.columns]
    missing = [c for c in ("板块", "物料编码", "数量合计", "采购不含税金额(万)") if c not in df.columns]
    if missing:
        raise KeyError(f"源数据缺少必需字段: {missing}")

    source_df = df.loc[df["板块"].isin(cfg.boards), available_cols].copy()
    source_df = source_df.rename(columns={
        "采购入库年份": "年份", "采购入库月份": "月份", "数量合计": "数量",
    })

    for col in ["年份", "月份", "物料编码"]:
        if col in source_df.columns:
            source_df[col] = pd.to_numeric(source_df[col], errors="coerce")
    for col in ["数量", "采购不含税金额(万)", "采购不含税单价"]:
        if col in source_df.columns:
            source_df[col] = pd.to_numeric(source_df[col], errors="coerce")

    source_df = source_df.dropna(subset=["年份", "月份", "物料编码", "数量"]).copy()
    source_df["年份"] = source_df["年份"].astype(int)
    source_df["月份"] = source_df["月份"].astype(int)
    source_df["年月"] = pd.to_datetime(
        source_df["年份"].astype(str) + "-" + source_df["月份"].astype(str).str.zfill(2) + "-01"
    )

    # 供应商范围：关联单位之间不是市场议价关系，默认只保留外部单位。
    # 排除量单独记录，报告里能看到「因为这条口径少了多少」，避免数据无声消失。
    supplier_scope = "全部供应商"
    excluded_rows, excluded_amount = 0, 0.0
    if cfg.external_only and SUPPLIER_TYPE_COLUMN in source_df.columns:
        keep = source_df[SUPPLIER_TYPE_COLUMN].isin(EXTERNAL_SUPPLIER_TYPES)
        dropped = source_df.loc[~keep]
        excluded_rows = int(len(dropped))
        excluded_amount = float(dropped["采购不含税金额(万)"].sum())
        source_df = source_df.loc[keep].copy()
        supplier_scope = "仅外部单位"
        if source_df.empty:
            raise ValueError(
                f"按「{SUPPLIER_TYPE_COLUMN}」只保留 {'/'.join(EXTERNAL_SUPPLIER_TYPES)} 后没有数据，"
                "检查该列取值或关闭 external_only。"
            )

    # 数据实际覆盖的业务月份（用于回溯时的覆盖度校验）
    available_months = sorted(
        source_df["年月"].dt.strftime("%Y-%m").unique().tolist()
    )
    data_month_start = available_months[0] if available_months else None
    data_month_end = available_months[-1] if available_months else None

    # 结束月：优先用配置指定，否则取数据最大月
    if cfg.end_month:
        report_end_month = pd.Timestamp(cfg.end_month + "-01")
        available = source_df["年月"].max()
        if report_end_month > available:
            raise ValueError(
                f"指定的结束月份 {cfg.end_month} 晚于数据最新月份 {available:%Y-%m}，请检查数据或参数。"
            )
    else:
        report_end_month = source_df["年月"].max()

    window_start_month = report_end_month - pd.DateOffset(months=cfg.window_months - 1)

    # 覆盖度校验：窗口内必须有完整 12 个月的数据，否则补零会扭曲增长指标
    window_months_needed = [
        f"{d:%Y-%m}" for d in pd.date_range(window_start_month, report_end_month, freq="MS")
    ]
    missing_months = [m for m in window_months_needed if m not in set(available_months)]
    if missing_months:
        raise ValueError(
            f"分析窗口 {window_start_month:%Y-%m} ~ {report_end_month:%Y-%m} 缺少数据月份 "
            f"{', '.join(missing_months)}。\n"
            f"当前数据覆盖 {data_month_start} ~ {data_month_end}，不足以支撑 {cfg.window_months} 个月窗口。\n"
            f"缺失月份会被补零，导致增长指标失真。请换用数据完整的结束月份，或选择覆盖完整的历史归档。"
        )

    analysis_df = source_df.loc[
        source_df["年月"].between(window_start_month, report_end_month)
    ].copy()

    # 最近 N 个月累计金额准入（严格大于，等于门槛不通过）
    recent_start = report_end_month - pd.DateOffset(months=cfg.recent_filter_months - 1)
    recent_amount = (
        analysis_df.loc[analysis_df["年月"] >= recent_start, ["物料编码", "采购不含税金额(万)"]]
        .groupby("物料编码", as_index=False, observed=True)["采购不含税金额(万)"]
        .sum()
        .rename(columns={"采购不含税金额(万)": f"最近{cfg.recent_filter_months}个月累计采购不含税金额(万)"})
    )
    eligible = recent_amount.loc[
        recent_amount[f"最近{cfg.recent_filter_months}个月累计采购不含税金额(万)"] > cfg.min_recent_amount_wan,
        "物料编码",
    ]
    analysis_df = analysis_df.loc[analysis_df["物料编码"].isin(eligible)].copy()

    meta = {
        "window_start": f"{window_start_month:%Y-%m}",
        "window_end": f"{report_end_month:%Y-%m}",
        "source_rows": int(len(source_df)),
        "filtered_rows": int(len(analysis_df)),
        "filtered_materials": int(analysis_df["物料编码"].nunique()),
        "data_month_start": data_month_start,
        "data_month_end": data_month_end,
        "supplier_scope": supplier_scope,
        "supplier_excluded_rows": excluded_rows,
        "supplier_excluded_amount": round(excluded_amount, 1),
    }
    return analysis_df, meta


# --------------------------------------------------------------------------
# 阶段二：月度汇总 + 指标计算
# --------------------------------------------------------------------------
def build_monthly(analysis_df: pd.DataFrame, window_start_month: pd.Timestamp,
                  report_end_month: pd.Timestamp) -> pd.DataFrame:
    """补齐 12 个月时间轴并汇总到「物料编码 + 年月」粒度。"""
    dim_cols = [c for c in MATERIAL_DIM_COLUMNS if c in analysis_df.columns]
    material_dim = (
        analysis_df.sort_values(["物料编码", "年月"])
        .groupby("物料编码", observed=True)[dim_cols]
        .first()
        .reset_index()
    )

    month_index = pd.DataFrame({"年月": pd.date_range(window_start_month, report_end_month, freq="MS")})
    monthly = material_dim.assign(_k=1).merge(month_index.assign(_k=1), on="_k").drop(columns="_k")

    monthly_amount = (
        analysis_df.groupby(["物料编码", "年月"], as_index=False, observed=True)
        .agg({"数量": "sum", "采购不含税金额(万)": "sum"})
    )
    monthly = monthly.merge(monthly_amount, on=["物料编码", "年月"], how="left")
    monthly["数量"] = monthly["数量"].fillna(0.0)
    monthly["采购不含税金额(万)"] = monthly["采购不含税金额(万)"].fillna(0.0)
    # 均价统一在 add_monthly_metrics 里按「元」计算，这里不再生成万元/件 的同名列
    return monthly


def calc_positive_streak(growth_series: pd.Series) -> pd.Series:
    """连续环比为正的月数；一旦非正即归零。"""
    streak = 0
    values: List[int] = []
    for growth_value in growth_series:
        streak = streak + 1 if (pd.notna(growth_value) and growth_value > 0) else 0
        values.append(streak)
    return pd.Series(values, index=growth_series.index)


def add_monthly_metrics(group: pd.DataFrame) -> pd.DataFrame:
    """单物料的滚动指标计算。"""
    group = group.sort_values("年月").copy()
    quantity = group["数量"]
    amount = group["采购不含税金额(万)"]

    previous_quantity = quantity.shift(1)
    growth = quantity.pct_change()
    growth = growth.where(previous_quantity > 0, np.nan)

    group["最近1个月环比增长率"] = growth
    group["最近3个月平均采购量"] = quantity.rolling(3, min_periods=3).mean()
    group["前3个月平均采购量"] = quantity.shift(3).rolling(3, min_periods=3).mean()
    # 基期 <= 0（新引入，或前 3 个月净退货）时趋势指数无意义：留空而不是 inf/负值，
    # inf 会同时满足「>=20%」和「降序排第一」，把新物料顶到优先谈判榜首。
    baseline = group["前3个月平均采购量"]
    group["采购量趋势指数"] = (
        group["最近3个月平均采购量"] / baseline.where(baseline > 0) - 1
    )
    group["连续增长月数"] = calc_positive_streak(growth)
    group["最近3个月采购量占比"] = (
        quantity.rolling(3, min_periods=3).sum() / quantity.rolling(12, min_periods=12).sum()
    )
    group["最近12个月累计采购不含税金额(万)"] = amount.rolling(12, min_periods=12).sum()
    group["最近3个月零采购月份"] = (quantity == 0).rolling(3, min_periods=3).sum()
    group["月加权平均价(元)"] = amount * 10000 / quantity.replace(0, np.nan)
    return group


def compute_supplier_metrics(analysis_df: pd.DataFrame) -> pd.DataFrame:
    """核心供应商与单一供应商供货占比。"""
    supplier_amount = (
        analysis_df.groupby(["物料编码", "供应商名称"], as_index=False, observed=True)["采购不含税金额(万)"]
        .sum()
        .sort_values(["物料编码", "采购不含税金额(万)"], ascending=[True, False])
    )
    core_supplier = supplier_amount.drop_duplicates("物料编码").rename(columns={
        "供应商名称": "核心供应商名称",
        "采购不含税金额(万)": "核心供应商金额(万)",
    })
    supplier_total = (
        analysis_df.groupby("物料编码", as_index=False, observed=True)["采购不含税金额(万)"]
        .sum()
        .rename(columns={"采购不含税金额(万)": "最近12个月累计采购不含税金额(万)"})
    )
    core_supplier = core_supplier.merge(supplier_total, on="物料编码", how="left")
    core_supplier["单一供应商供货占比"] = (
        core_supplier["核心供应商金额(万)"] / core_supplier["最近12个月累计采购不含税金额(万)"]
    )
    return core_supplier


def compute_monthly(monthly: pd.DataFrame) -> pd.DataFrame:
    """逐物料套用指标。"""
    frames = [
        add_monthly_metrics(group)
        for _, group in monthly.groupby("物料编码", sort=False, observed=True)
    ]
    return pd.concat(frames, ignore_index=True)


# --------------------------------------------------------------------------
# 阶段三：优先级分类
# --------------------------------------------------------------------------
def classify_priority(row: pd.Series) -> str:
    """四档分类：新引入单列 → 优先谈判 → 观察 → 不建议，命中前一档即止。"""
    # 前 3 个月没有正采购 = 没有增长基线，趋势指数为空。这类物料是「新上量」或
    # 前期净退货，与「采购量增长」不是一回事，单独成档、不占谈判名额。
    if not (row["前3个月平均采购量"] > 0):
        return NEW_MATERIAL_LABEL
    if row["采购量趋势指数"] >= TREND_INDEX_PRIORITY and (
        row["最近1个月环比增长率"] >= MOM_GROWTH_PRIORITY
        or row["连续增长月数"] >= STREAK_PRIORITY
    ):
        return "优先谈判"
    if row["采购量趋势指数"] >= TREND_INDEX_WATCH or row["最近3个月采购量占比"] >= RECENT_SHARE_WATCH:
        return "观察"
    return "不建议"


def build_report(monthly: pd.DataFrame, core_supplier: pd.DataFrame) -> pd.DataFrame:
    """取每物料最后一个月指标，过滤零采购，分类并排序。"""
    latest = (
        monthly.sort_values(["物料编码", "年月"])
        .groupby("物料编码", as_index=False)
        .tail(1)
        .merge(
            core_supplier[["物料编码", "核心供应商名称", "单一供应商供货占比"]],
            on="物料编码", how="left",
        )
    )
    latest = latest.loc[latest["最近3个月零采购月份"] == 0].copy()

    latest["谈判优先级"] = latest.apply(classify_priority, axis=1)
    latest["_排序"] = latest["谈判优先级"].map(PRIORITY_ORDER)
    # 趋势指数为空的新引入物料排在档内最后，用累计金额兜底排序
    latest = latest.sort_values(
        ["_排序", "采购量趋势指数", "最近12个月累计采购不含税金额(万)", "最近3个月采购量占比"],
        ascending=[True, False, False, False],
        na_position="last",
    ).drop(columns=["_排序"]).reset_index(drop=True)

    cols = [c for c in REPORT_COLUMNS if c in latest.columns]
    return latest[cols].copy()


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def run_analysis(cfg: Config) -> Dict[str, Any]:
    """执行完整分析，返回各阶段结果。"""
    df, data_meta = load_source_data(cfg)
    analysis_df, meta = clean_and_filter(df, cfg)

    window_start = pd.Timestamp(meta["window_start"] + "-01")
    window_end = pd.Timestamp(meta["window_end"] + "-01")

    monthly = build_monthly(analysis_df, window_start, window_end)
    monthly = compute_monthly(monthly)
    core_supplier = compute_supplier_metrics(analysis_df)
    report_df = build_report(monthly, core_supplier)

    priority_summary = (
        report_df.groupby("谈判优先级", observed=True)
        .agg(物料数=("物料编码", "size"),
             采购金额=("最近12个月累计采购不含税金额(万)", "sum"))
        .reindex(PRIORITY_LABELS)
        .fillna(0)
        .reset_index()
    )

    # 数据溯源信息跟着结果走，便于报告标注「用的是哪一版数据」
    meta["data_source"] = data_meta.get("source", DEFAULT_SOURCE)
    meta["data_file"] = data_meta.get("data_file")
    meta["snapshot_date"] = data_meta.get("snapshot_date")
    meta["data_month_range"] = data_meta.get("data_month_range", {})
    meta["read_from"] = "采购入库归档"
    meta["new_material_count"] = int((report_df["谈判优先级"] == NEW_MATERIAL_LABEL).sum())

    return {
        "config": asdict(cfg),
        "meta": meta,
        "monthly": monthly,
        "core_supplier": core_supplier,
        "report": report_df,
        "priority_summary": priority_summary,
        "report_end_month": meta["window_end"],
    }


def export(result: Dict[str, Any], out_dir: Path) -> Path:
    """导出 Excel 报告，返回文件路径。"""
    from reporting.excel import export_excel

    meta = result["meta"]
    reports_dir = Path(out_dir) / "reports" / str(meta["snapshot_date"])
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{result['report_end_month']}采购增量分析报告.xlsx"
    export_excel(result, path)
    return path
