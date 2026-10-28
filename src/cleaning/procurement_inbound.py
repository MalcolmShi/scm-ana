"""采购入库清洗：接口字段改名、数值转换、元转万元、日期派生。

输入为接口原始字段，输出为分析底表；接口字段说明见 docs/入库记录接口.md。
"""
from __future__ import annotations

from typing import Any, Dict

import pandas as pd

COLUMN_MAP = {
    "company_code": "公司编码",
    "company_name": "公司名称",
    "short_name": "公司简称",
    "company_type": "板块",
    "company_group_name": "多组织名称",
    "region_name": "大区",
    "store_code": "仓库编码",
    "store_name": "仓库名称",
    "store_type": "仓库类型",
    "bill_type_name": "单据类型名称",
    "inv_type_name": "出入库类型名称",
    "inv_inout_date": "出入库日期",
    "item_num": "物料编码",
    "item_trade_name": "物料名称",
    "item_spec": "规格",
    "item_sku": "货号",
    "inv_primary_uom": "主单位",
    "item_brand": "品牌",
    "main_brand": "主品牌",
    "stock_classification2": "一级存货分类",
    "stock_classification3": "二级存货分类",
    "level1_class_name": "一级分类",
    "level2_class_name": "二级分类",
    "level3_class_name": "三级分类",
    "level4_class_name": "四级分类",
    "level5_class_name": "五级分类",
    "item_reagent_type_name": "试剂类别名称",
    "supplier_code": "供应商编码",
    "supplier_name": "供应商名称",
    "supplier_type": "供应商类型",
    "supplier_nature_merged": "供应商类型（财务）",
    "supplier_custom_internal_external": "自定义内外部供应商",
    "is_group_bill": "是否多组织内交易",
    "pur_order_type_name": "采购类型名称",
    "equip_reagent_class": "设备试剂分类",
    "inv_in_qty": "数量合计",
    "purchase_order_amt": "采购入库含税金额(万)",
    "notax_purchase_order_amt": "采购不含税金额(万)",
    "tax_rate": "税率"
}


def clean_procurement(raw: pd.DataFrame) -> pd.DataFrame:
    """清洗接口原始数据，不修改输入，也不接受已经换算过的分析底表。"""
    required = {"company_type", "inv_inout_date", "item_num", "inv_in_qty", "notax_purchase_order_amt"}
    missing = sorted(required - set(raw.columns))
    if missing:
        raise ValueError(f"采购入库原始数据缺少接口字段：{', '.join(missing)}")

    frame = raw.rename(columns=COLUMN_MAP).copy()
    dates = pd.to_datetime(frame["出入库日期"], errors="coerce")
    frame["采购入库年份"] = dates.dt.year.astype("Int64")
    frame["采购入库月份"] = dates.dt.month.astype("Int64")
    frame["出入库月份"] = dates.dt.strftime("%Y-%m-01")

    for column in ("数量合计", "税率", "采购入库含税金额(万)", "采购不含税金额(万)"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    for column in ("采购入库含税金额(万)", "采购不含税金额(万)"):
        if column in frame.columns:
            frame[column] = frame[column] * 0.0001
    return frame


def month_range(df: pd.DataFrame) -> Dict[str, Any]:
    """从数据本身推断业务月份覆盖范围，不靠目录名或抓取时间。"""
    empty: Dict[str, Any] = {"start": None, "end": None, "months": [], "count": 0}

    if {"采购入库年份", "采购入库月份"} <= set(df.columns):
        years = pd.to_numeric(df["采购入库年份"], errors="coerce")
        months = pd.to_numeric(df["采购入库月份"], errors="coerce")
        valid = years.notna() & months.notna()
        labels = (
            years[valid].astype(int).astype(str)
            + "-"
            + months[valid].astype(int).astype(str).str.zfill(2)
        )
    elif "出入库日期" in df.columns or "inv_inout_date" in df.columns:
        date_column = "出入库日期" if "出入库日期" in df.columns else "inv_inout_date"
        labels = pd.to_datetime(df[date_column], errors="coerce").dt.strftime("%Y-%m").dropna()
    else:
        return empty

    unique = sorted(set(labels.tolist()))
    if not unique:
        return empty
    return {"start": unique[0], "end": unique[-1], "months": unique, "count": len(unique)}
