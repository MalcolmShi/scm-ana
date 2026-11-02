"""采购增量：按确认口径从 processed 明细计算报表。"""
from __future__ import annotations

import numpy as np
import pandas as pd


REQUIRED = ["出入库日期", "物料编码", "物料名称", "规格", "主单位", "供应商名称", "供应商类型", "板块", "数量", "采购金额(万)"]


def _filter_details(frame: pd.DataFrame, end_month: str):
    """时间、业务范围和近半年含税金额门槛。"""
    missing = [c for c in REQUIRED if c not in frame]
    if missing:
        raise ValueError(f"processed 缺少字段：{missing}")
    essential = ["出入库日期", "物料编码", "供应商名称", "供应商类型", "板块", "数量", "采购金额(万)"]
    if frame[essential].isna().any().any():
        raise ValueError("processed 分析必需字段存在空值")
    end = pd.Period(end_month, freq="M")
    months = pd.period_range(end - 11, end, freq="M")
    dates = pd.to_datetime(frame["出入库日期"], errors="raise")
    df = frame.loc[dates.between(months[0].start_time, end.end_time)
                   & frame["板块"].isin(["检验", "检验产品", "诊断"])
                   & frame["供应商类型"].eq("外部单位")].copy()
    df["采购月"] = pd.to_datetime(df["出入库日期"]).dt.to_period("M")
    amount6 = df.loc[df["采购月"].isin(months[-6:])].groupby("物料编码")["采购金额(万)"].sum()
    df = df.loc[df["物料编码"].isin(amount6[amount6 > 5].index)].copy()
    first = ["物料编码", "物料名称", "规格", "主单位", "供应商名称", "采购月", "数量", "采购金额(万)", "供应商类型", "板块"]
    df = df[first + [c for c in df if c not in first]]
    if df.empty:
        raise ValueError("没有符合范围和金额门槛的物料")

    return df, end, months


def _monthly_totals(df: pd.DataFrame, months: pd.PeriodIndex):
    """按物料和月份汇总，补齐无采购的月份。"""
    monthly = df.groupby(["物料编码", "采购月"], as_index=False).agg(
        月采购量=("数量", "sum"), 月含税金额万=("采购金额(万)", "sum"))
    qty = monthly.pivot(index="物料编码", columns="采购月", values="月采购量").reindex(columns=months).fillna(0)
    amt = monthly.pivot(index="物料编码", columns="采购月", values="月含税金额万").reindex(columns=months).fillna(0)
    qty.columns.name = amt.columns.name = "采购月"
    monthly = pd.concat([qty.stack().rename("月采购量"), amt.stack().rename("月含税金额万")], axis=1).reset_index()

    return monthly, qty


def _growth_metrics(qty: pd.DataFrame):
    """全部增长指标使用相同的月度净数量。"""
    metrics = pd.DataFrame(index=qty.index)
    metrics["最近3个月平均采购量"] = qty.iloc[:, -3:].mean(axis=1)
    metrics["前3个月平均采购量"] = qty.iloc[:, -6:-3].mean(axis=1)
    metrics["采购量趋势指数"] = metrics["最近3个月平均采购量"] / metrics["前3个月平均采购量"].where(metrics["前3个月平均采购量"] > 0) - 1
    previous = qty.shift(axis=1)
    mom = qty / previous.where(previous > 0) - 1
    metrics["最近1个月环比增长率"] = mom.iloc[:, -1]
    metrics["连续增长月数"] = mom.gt(0).iloc[:, ::-1].cumprod(axis=1).sum(axis=1).astype(int)
    total = qty.sum(axis=1)
    metrics["最近3个月采购量占比"] = qty.iloc[:, -3:].sum(axis=1) / total.where(total != 0)
    first_half = qty.iloc[:, :6].mean(axis=1)
    metrics["半年同比"] = qty.iloc[:, 6:].mean(axis=1) / first_half.where(first_half > 0) - 1
    quarters = pd.DataFrame({i: qty.iloc[:, i * 3:i * 3 + 3].mean(axis=1) for i in range(4)})
    metrics["逐季抬升次数"] = quarters.diff(axis=1).iloc[:, 1:].gt(0).sum(axis=1)
    last3 = qty.iloc[:, -3:]
    metrics["备货嫌疑"] = (last3.max(axis=1) / last3.sum(axis=1).where(last3.sum(axis=1) > 0)).ge(0.7) & metrics["半年同比"].lt(0)

    return metrics


def _supplier_metrics(df: pd.DataFrame):
    """按窗口内含税净额确定核心供应商。"""
    suppliers = (df.groupby(["物料编码", "供应商名称"], as_index=False, dropna=False)["采购金额(万)"].sum()
                 .sort_values(["物料编码", "采购金额(万)", "供应商名称"], ascending=[True, False, True]))
    suppliers = suppliers.drop_duplicates("物料编码").rename(columns={"供应商名称": "核心供应商名称", "采购金额(万)": "核心供应商含税金额万"})
    supplier_total = df.groupby("物料编码")["采购金额(万)"].sum().rename("最近12个月含税金额万")
    suppliers = suppliers.join(supplier_total, on="物料编码")
    suppliers["单一供应商供货占比"] = suppliers["核心供应商含税金额万"] / suppliers["最近12个月含税金额万"].where(suppliers["最近12个月含税金额万"] != 0)

    return suppliers


def _build_report(df, qty, metrics, suppliers, end):
    """应用近期采购门槛、确定分档并保留来源字段。"""
    attributes = [c for c in df if c != "物料编码"]
    def values(series):
        unique = list(pd.unique(series))
        return unique[0] if len(unique) == 1 else unique
    attrs = df.groupby("物料编码", as_index=False, dropna=False)[attributes].agg(values)
    report = attrs.merge(metrics.reset_index(), on="物料编码", validate="one_to_one").merge(suppliers, on="物料编码", validate="one_to_one")
    rejected = qty.iloc[:, -3:].le(0).any(axis=1)
    report = report.loc[~report["物料编码"].isin(rejected[rejected].index)].copy()
    baseline = report["前3个月平均采购量"] > 0
    step = (report["采购量趋势指数"] >= .20) & ((report["最近1个月环比增长率"] >= .10) | (report["连续增长月数"] >= 2))
    climb = (report["逐季抬升次数"] >= 2) & (report["半年同比"] >= .50)
    report["谈判优先级"] = np.select([baseline & (step | climb), baseline & (report["采购量趋势指数"] >= .10)], ["优先谈判", "观察"], default="不建议")
    free = df.loc[df["采购金额(万)"].eq(0)].groupby("物料编码")["数量"].sum()
    total_qty = df.groupby("物料编码")["数量"].sum()
    report["免费数量占比"] = report["物料编码"].map(free.reindex(total_qty.index, fill_value=0) / total_qty.where(total_qty != 0))
    report["主单位多值"] = report["物料编码"].map(df.groupby("物料编码")["主单位"].nunique().gt(1))
    report["业务结束月"] = str(end)
    order = {"优先谈判": 0, "观察": 1, "不建议": 2}
    report = (report.assign(_顺序=report["谈判优先级"].map(order))
              .sort_values(["_顺序", "采购量趋势指数", "最近12个月含税金额万"], ascending=[True, False, False], na_position="last")
              .drop(columns="_顺序").reset_index(drop=True))
    priority = ["业务结束月", "物料编码", "物料名称", "规格", "主单位", "谈判优先级", "核心供应商名称", "最近12个月含税金额万", "采购量趋势指数", "最近1个月环比增长率", "连续增长月数", "单一供应商供货占比", "最近3个月平均采购量", "前3个月平均采购量", "半年同比", "逐季抬升次数", "最近3个月采购量占比", "备货嫌疑", "免费数量占比", "主单位多值"]
    report = report[priority + [c for c in report if c not in priority]]
    # 来源属性每列固定为列表，跨月份也不会因单值/多值变化而改变 schema。
    multi_columns = list(attributes)
    for col in attributes:
        report[col] = pd.Series([v if isinstance(v, list) else [v] for v in report[col]], index=report.index, dtype=object)
        if col == "采购月":
            report[col] = pd.Series([[str(x) for x in v] for v in report[col]], index=report.index, dtype=object)
    return report, int(rejected.sum()), multi_columns



def analyze(frame: pd.DataFrame, end_month: str) -> tuple[dict[str, pd.DataFrame], dict]:
    """从 processed 明细计算物料报表和月度表，不读取或写入数据。"""
    df, end, months = _filter_details(frame, end_month)
    monthly, qty = _monthly_totals(df, months)
    metrics = _growth_metrics(qty)
    suppliers = _supplier_metrics(df)
    report, excluded, list_columns = _build_report(df, qty, metrics, suppliers, end)
    monthly["采购月"] = monthly["采购月"].astype(str)
    return {"detail": report, "monthly": monthly}, {
        "input_rows": len(frame), "filtered_rows": len(df), "filtered_materials": len(qty),
        "excluded_materials": excluded, "list_columns": list_columns,
    }
