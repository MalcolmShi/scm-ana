"""采购入库清洗：raw 原样日档 → processed 明细底表。

口径来源：``notebooks/采购增量分析/分析方案.md`` §三（业务确认 2026-11-02），
预演与逐项确认见同目录 ``数据清洗确认.ipynb``，稳定脚本与其对拍一致。

- processed 是**明细底表**：27 个标准字段 + 溯源列 ``updated_at``，
  一行一条入库明细，**不做预聚合**；聚合是分析时按需执行的程序逻辑；
- 项二「整行重复」业务未拍板：默认不剔除，``drop_duplicates=True`` 显式打开才剔；
- 清洗只动格式与标注，不删行（除非显式去重），金额行数双守恒。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from paths import TIMEZONE
DATE_COLUMN = "出入库日期"

#: 业务金额列（含税口径，业务确认 2026-11-02：一律用含税对）
AMOUNT_COLUMN = "采购金额(万)"

#: 供应商类型：源列名 → 底表标准名，空值统一填「外部单位」
SUPPLIER_TYPE_SOURCE = "财务内部外供应商类型"
SUPPLIER_TYPE = "供应商类型"
SUPPLIER_TYPE_FILL = "外部单位"

#: 含税单价空值 ← 购退单价（仅清洗输入，不进底表）
PRICE_COLUMN = "采购单价"
PRICE_FILL_SOURCE = "购退单价"

#: 采购模式：源列「采购类型名称」去"合同"后缀归一，空值标「未标注」
MODE_SOURCE = "采购类型名称"
MODE_COLUMN = "采购模式"
MODE_MAP = {
    "定向采购合同": "定向采购",
    "集中采购合同": "集中采购",
    "指定采购合同": "指定采购",
    "自行采购合同": "自行采购",
    "普通采购合同": "其他",
}
MODE_UNLABELED = "未标注"

#: 文本脏字符：只去控制符与首尾空白，不改业务值
TEXT_DIRTY_PATTERN = r"[\r\n\t]"

#: 溯源列：单列 updated_at（取数时间），取代 raw 的 _source/_fetched_at（方案 2.3）
UPDATED_AT_COLUMN = "updated_at"

#: processed 底表标准字段（27 项，顺序即列序；方案 2.2 表 A）
STANDARD_COLUMNS = [
    DATE_COLUMN,
    "物料编码", "物料名称", "规格", "主单位",
    "一级分类", "二级分类", "三级分类", "四级分类", "五级分类",
    "品牌", "主品牌", "货号",
    "供应商名称", SUPPLIER_TYPE, "板块",
    "公司编码", "公司名称", "公司简称", "大区",
    "数据来源系统", "设备试剂分类", MODE_COLUMN,
    "数量", PRICE_COLUMN, AMOUNT_COLUMN, "税率",
]


def _text_columns(frame: pd.DataFrame) -> list[str]:
    """文本列：object / str / string dtype（与清洗确认 notebook 的 select_dtypes 口径一致）。

    不能用 ``is_string_dtype`` 判断 object 列——「货号」这类混有非字符串取值的列
    会被误判成非文本而漏洗（对拍抓出来的坑）。
    """
    return [c for c in frame.columns
            if frame[c].dtype == object or pd.api.types.is_string_dtype(frame[c])]


def _fill_text(series: pd.Series, value: str) -> pd.Series:
    """文本列填充：先转 object 再填。

    pyarrow 支撑的 str 列在「整列为空」时是 null 类型，直接 fillna 会报
    ``Invalid null value``；单日档就可能踩中，object 填充无此限制。
    """
    return series.astype("object").fillna(value)


def clean_inbound(frame: pd.DataFrame, drop_duplicates: bool = False,
                  fetched_at: Optional[str] = None) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """套用已确认的清洗规则，返回 ``(清洗后底表, 处置台账)``。

    规则与 ``数据清洗确认.ipynb`` 总账一致（对拍基准）：
    ① 文本列 strip + 去 ``\\r\\n\\t``；② 供应商类型空值填「外部单位」；
    ③ 含税单价为空或为负时，使用非空购退单价；④ 出入库日期 → 日期类型；
    ⑤ 整行重复默认保留（项二待业务定夺）；⑥ 采购模式去"合同"后缀归一。
    """
    clean = frame.copy()
    rows_before = len(clean)
    amount_before = float(clean[AMOUNT_COLUMN].sum()) if AMOUNT_COLUMN in clean else 0.0

    # ① 出入库日期 → 日期；解析失败的行说明 raw 有问题，直接报错不静默丢
    dates = pd.to_datetime(clean[DATE_COLUMN], errors="coerce")
    bad_dates = int(dates.isna().sum())
    if bad_dates:
        raise ValueError(f"raw 有 {bad_dates} 行出入库日期解析失败，先查 raw 再清洗")
    clean[DATE_COLUMN] = dates

    # ② 文本脏字符：strip + 去 \r\n\t（只动格式不改业务值）
    text_fixed = 0
    for col in _text_columns(clean):
        series = clean[col].astype("string")
        text_fixed += int(series.str.contains(TEXT_DIRTY_PATTERN, regex=True, na=False).sum()
                          + series.str.match(r"^\s|\s$", na=False).sum())
        clean[col] = series.str.replace(TEXT_DIRTY_PATTERN, "", regex=True).str.strip()

    # ③ 供应商类型：源列改名定标准名，空值填「外部单位」
    if SUPPLIER_TYPE_SOURCE not in clean.columns:
        raise KeyError(f"raw 缺少必需字段：{SUPPLIER_TYPE_SOURCE}")
    supplier_filled = int(clean[SUPPLIER_TYPE_SOURCE].isna().sum())
    clean[SUPPLIER_TYPE_SOURCE] = _fill_text(clean[SUPPLIER_TYPE_SOURCE], SUPPLIER_TYPE_FILL)
    clean = clean.rename(columns={SUPPLIER_TYPE_SOURCE: SUPPLIER_TYPE})

    # ④ 含税单价为空或为负时，用非空购退单价补正
    price_filled = 0
    if PRICE_FILL_SOURCE in clean.columns:
        price = pd.to_numeric(clean[PRICE_COLUMN], errors="raise").astype("Float64")
        replacement = pd.to_numeric(clean[PRICE_FILL_SOURCE], errors="raise").astype("Float64")
        fill = (price.isna() | price.lt(0)) & replacement.notna()
        price_filled = int(fill.sum())
        clean[PRICE_COLUMN] = price.where(~fill, replacement)

    # ⑤ 采购模式：去"合同"后缀归一，空值标「未标注」；源列不进底表
    if MODE_SOURCE not in clean.columns:
        clean[MODE_SOURCE] = pd.NA
    mode_normalized = int(clean[MODE_SOURCE].isin(MODE_MAP).sum())
    clean[MODE_COLUMN] = (clean[MODE_SOURCE].map(lambda x: MODE_MAP.get(x, x))
                          .astype("object").fillna(MODE_UNLABELED))

    # ⑥ 整行重复：项二业务未拍板，默认保留，只记台账
    duplicate_rows = int(clean.duplicated().sum())
    if drop_duplicates:
        clean = clean.drop_duplicates(keep="first").reset_index(drop=True)

    # ⑦ 溯源列：updated_at = raw 日档的 _fetched_at（取数时间），缺则记本次运行时间
    if UPDATED_AT_COLUMN not in clean.columns:
        if fetched_at is None:
            fetched_at = (str(clean["_fetched_at"].dropna().iloc[0])
                          if "_fetched_at" in clean and clean["_fetched_at"].notna().any()
                          else datetime.now(TIMEZONE).isoformat(timespec="seconds"))
        clean[UPDATED_AT_COLUMN] = fetched_at
        clean[UPDATED_AT_COLUMN] = clean[UPDATED_AT_COLUMN].astype("string")

    missing = [c for c in STANDARD_COLUMNS if c not in clean.columns]
    if missing:
        raise KeyError(f"raw 缺少底表必需字段：{missing}")
    result = clean.loc[:, STANDARD_COLUMNS + [UPDATED_AT_COLUMN]].reset_index(drop=True)

    audit = {
        "rows_before": rows_before, "rows_after": len(result),
        "amount_before_wan": round(amount_before, 1),
        "amount_after_wan": round(float(result[AMOUNT_COLUMN].sum()), 1),
        "text_fixed": text_fixed,
        "supplier_type_filled": supplier_filled,
        "price_filled": price_filled,
        "mode_normalized": mode_normalized,
        "duplicate_rows": duplicate_rows,
        "duplicates_dropped": rows_before - len(result) if drop_duplicates else 0,
    }
    return result, audit
