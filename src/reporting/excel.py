#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Excel 报告导出 —— 报告、月度数据、规则说明、图表四个工作表。

样式与字段口径沿用 ``notebooks/采购增量分析/采购增量分析.ipynb`` 中已确认的实现，
方案见 ``notebooks/采购增量分析/采购增量分析.md`` 第六节。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pandas as pd
from openpyxl import load_workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.chart.label import DataLabelList
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

RULE_ROWS = [
    ("报表用途", "识别采购量持续增长且具备谈判价值的物料，形成月度谈判候选清单。"),
    ("分析范围", "仅覆盖检验、检验产品、诊断三个板块；以源数据最大采购年月为结束月份，向前取12个月。"),
    ("供应商范围", "仅保留「财务内部外供应商类型 = 外部单位」的记录，关联单位之间不是市场议价关系，不进入候选。剔除的行数和金额见运行摘要。"),
    ("采购量口径", "汇总「数量」，含退货冲减（退回供应商、采购退货为负数）。有数量无金额的记录（免费供货、配套耗材）计入数量但不计入金额。"),
    ("金额口径", "汇总「采购不含税金额(万)」，求和前已包含红字冲减。金额为0的记录会拉低加权平均价，均价仅作参考。"),
    ("金额准入", "最近6个月累计采购不含税金额大于5万元的物料才进入后续分析。"),
    ("连续采购准入", "最近3个月存在零采购月份的物料直接排除，不参与优先级分类。"),
    ("新引入", "前3个月平均采购量不大于0（新上量或前期净退货）的物料，没有增长基线，趋势指数留空，单列一档、不占谈判名额，按最近12个月采购金额排序。"),
    ("优先谈判", "采购量趋势指数大于等于20%，且最近1个月环比增长率大于等于10%或连续增长月数大于等于2。表示采购量已明显提升且近期增长仍在延续，应优先核实需求原因并准备谈判。"),
    ("观察", "未达到优先谈判条件，但采购量趋势指数大于等于10%或最近3个月采购量占比大于等于30%。表示已出现增长信号或近期需求集中，建议按月跟踪。"),
    ("不建议", "不满足优先谈判或观察条件。当前采购量增长信号不足，暂不建议因采购增量启动谈判。"),
    ("最近1个月环比增长率", "(本月采购量-上月采购量)/上月采购量；仅当上月采购量大于0时计算。"),
    ("采购量趋势指数", "最近3个月平均采购量/前3个月平均采购量-1，用于判断近期采购量是否整体上台阶。基期为0时留空。基期极小时该指数会被放大，需结合采购量一起看。"),
    ("连续增长月数", "截至当前月，采购量环比连续为正的月份数。"),
    ("最近3个月采购量占比", "最近3个月采购总量/最近12个月采购总量，用于判断需求是否集中在近期释放。"),
    ("使用提示", "报表提供采购增量证据和优先级，不代替对一次性备货、项目采购、价格、替代供应商和质量表现的业务判断。"),
]

HEADER_DISPLAY_MAP = {
    "最近3个月平均采购量": "最近3个月\n平均采购量",
    "前3个月平均采购量": "前3个月\n平均采购量",
    "采购量趋势指数": "采购量趋势\n指数",
    "最近1个月环比增长率": "最近1个月\n环比增长率",
    "连续增长月数": "连续增长\n月数",
    "最近3个月采购量占比": "最近3个月\n采购量占比",
    "最近12个月累计采购不含税金额(万)": "最近12个月累计采购\n不含税金额(万)",
    "单一供应商供货占比": "单一供应商\n供货占比",
}

HEADER_WIDTH_MAP = {
    "最近3个月平均采购量": 16, "前3个月平均采购量": 16, "采购量趋势指数": 14,
    "最近1个月环比增长率": 18, "连续增长月数": 12, "最近3个月采购量占比": 16,
    "最近12个月累计采购不含税金额(万)": 20, "单一供应商供货占比": 16, "谈判优先级": 12,
}

PERCENT_COLUMNS = ["采购量趋势指数", "最近1个月环比增长率", "最近3个月采购量占比", "单一供应商供货占比"]
NUMBER_COLUMNS = ["最近3个月平均采购量", "前3个月平均采购量", "最近12个月累计采购不含税金额(万)"]


def _format_columns(sheet, df: pd.DataFrame) -> None:
    """统一设置列宽、表头样式与数字格式。"""
    column_index = {column: idx + 1 for idx, column in enumerate(df.columns)}
    for header_cell in sheet[1]:
        original = header_cell.value
        if original in HEADER_DISPLAY_MAP:
            header_cell.value = HEADER_DISPLAY_MAP[original]
        header_cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        sheet.column_dimensions[header_cell.column_letter].width = HEADER_WIDTH_MAP.get(original, 12)

    for column in NUMBER_COLUMNS:
        if column in column_index:
            for cells in sheet.iter_cols(min_col=column_index[column], max_col=column_index[column], min_row=2):
                for cell in cells:
                    cell.number_format = "0.00"
    for column in PERCENT_COLUMNS:
        if column in column_index:
            for cells in sheet.iter_cols(min_col=column_index[column], max_col=column_index[column], min_row=2):
                for cell in cells:
                    cell.number_format = "0.00%"


def _write_summary_sheet(writer, result: Dict[str, Any]) -> None:
    """运行摘要工作表：让人不打开明细也知道这轮跑出了什么。"""
    meta = result["meta"]
    report_df = result["report"]
    priority_summary = result["priority_summary"]

    priority_counts = priority_summary.set_index("谈判优先级")["物料数"].to_dict()
    priority_amount = priority_summary.set_index("谈判优先级")["采购金额"].to_dict()

    rows = [
        ("分析结束月份", meta["window_end"]),
        ("分析窗口", f"{meta['window_start']} ~ {meta['window_end']}（共 {result['config']['window_months']} 个月）"),
        ("板块范围", "、".join(result["config"]["boards"])),
        ("供应商范围", meta.get("supplier_scope", "全部供应商")),
    ]
    if meta.get("supplier_excluded_rows"):
        rows.append((
            "因供应商范围剔除",
            f"{meta['supplier_excluded_rows']:,} 行 / {meta['supplier_excluded_amount']:,.1f} 万元",
        ))
    rows += [
        ("金额准入门槛", f"最近 {result['config']['recent_filter_months']} 个月累计采购不含税金额 > {result['config']['min_recent_amount_wan']} 万元"),
        ("过滤后明细行数", meta["filtered_rows"]),
        ("覆盖物料数", meta["filtered_materials"]),
        ("报告物料数", len(report_df)),
        ("优先谈判数", int(priority_counts.get("优先谈判", 0))),
        ("优先谈判覆盖金额（万元）", round(float(priority_amount.get("优先谈判", 0)), 1)),
        ("观察数", int(priority_counts.get("观察", 0))),
        ("不建议数", int(priority_counts.get("不建议", 0))),
        ("新引入数", int(priority_counts.get("新引入", 0))),
    ]

    # 数据溯源：归档日说明这一版数据的时点，业务覆盖月份说明它含哪些月，两者分开看
    if meta.get("data_source"):
        rows.append(("数据来源", meta["data_source"]))
    if meta.get("snapshot_date"):
        rows.append(("数据归档日", meta["snapshot_date"]))
    if meta.get("data_month_range"):
        rng = meta["data_month_range"]
        rows.append(("数据覆盖月份", f"{rng.get('start')} ~ {rng.get('end')}（{rng.get('count')} 个月）"))
    if meta.get("data_file"):
        rows.append(("数据文件", meta["data_file"]))

    df = pd.DataFrame(rows, columns=["指标", "数值"])
    df.to_excel(writer, sheet_name="运行摘要", index=False)
    sheet = writer.sheets["运行摘要"]
    sheet.freeze_panes = "A2"
    sheet.column_dimensions["A"].width = 28
    sheet.column_dimensions["B"].width = 56
    for header_cell in sheet[1]:
        header_cell.alignment = Alignment(horizontal="center", vertical="center")
        header_cell.font = Font(bold=True, color="FFFFFF")
        header_cell.fill = PatternFill("solid", fgColor="1F4E78")
    for row in sheet.iter_rows(min_row=2):
        row[0].font = Font(bold=True, color="1F4E78")
        for cell in row:
            cell.alignment = Alignment(vertical="center", wrap_text=True)


def _add_chart_sheet(workbook, result: Dict[str, Any]) -> None:
    """图表页：优先级覆盖规模 + 优先谈判 Top 15。"""
    report_df = result["report"]
    if "图表" in workbook.sheetnames:
        del workbook["图表"]
    chart_sheet = workbook.create_sheet("图表")
    chart_sheet.sheet_view.showGridLines = False

    navy_fill = PatternFill("solid", fgColor="1F4E78")
    light_blue_fill = PatternFill("solid", fgColor="D9EAF7")
    pale_blue_fill = PatternFill("solid", fgColor="EAF2F8")
    yellow_fill = PatternFill("solid", fgColor="FFF2CC")
    thin_side = Side(style="thin", color="D9E2F3")
    table_border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)

    chart_sheet.merge_cells("A1:H1")
    chart_sheet["A1"] = "采购增量谈判概览"
    chart_sheet["A1"].fill = navy_fill
    chart_sheet["A1"].font = Font(bold=True, color="FFFFFF", size=16)
    chart_sheet["A1"].alignment = Alignment(horizontal="left", vertical="center")
    chart_sheet.row_dimensions[1].height = 30

    chart_sheet.merge_cells("A2:H2")
    chart_sheet["A2"] = "用于判断谈判候选覆盖的采购规模，并优先锁定金额最高的优先谈判物料。金额单位：万元。"
    chart_sheet["A2"].fill = light_blue_fill
    chart_sheet["A2"].alignment = Alignment(vertical="center", wrap_text=True)
    chart_sheet.row_dimensions[2].height = 28

    for label_range, label in [("A4:B4", "优先谈判候选数"), ("D4:E4", "优先谈判覆盖金额（万元）")]:
        chart_sheet.merge_cells(label_range)
        cell = chart_sheet[label_range.split(":")[0]]
        cell.value = label
        cell.fill = pale_blue_fill
        cell.font = Font(bold=True, color="1F4E78", size=10)
        cell.alignment = Alignment(horizontal="center", vertical="center")

    priority_amount = report_df.loc[report_df["谈判优先级"] == "优先谈判", "最近12个月累计采购不含税金额(万)"].sum()
    chart_sheet.merge_cells("A5:B6")
    chart_sheet["A5"] = int((report_df["谈判优先级"] == "优先谈判").sum())
    chart_sheet.merge_cells("D5:E6")
    chart_sheet["D5"] = float(priority_amount)
    for cell_ref in ["A5", "D5"]:
        cell = chart_sheet[cell_ref]
        cell.font = Font(bold=True, color="1F4E78", size=18)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = table_border
    chart_sheet["A5"].number_format = "#,##0"
    chart_sheet["D5"].number_format = "#,##0.0"

    priority_summary = result["priority_summary"].rename(columns={"采购金额": "采购金额万元"})
    last_row = len(priority_summary) + 2          # 表头在第 2 行，数据行 3 开始
    for col_index, value in enumerate(["谈判优先级", "物料数", "采购金额(万元)"], start=10):
        chart_sheet.cell(2, col_index, value)
    for row_index, row in priority_summary.iterrows():
        excel_row = row_index + 3
        chart_sheet.cell(excel_row, 10, row["谈判优先级"])
        chart_sheet.cell(excel_row, 11, int(row["物料数"]))
        chart_sheet.cell(excel_row, 12, float(row["采购金额万元"]))

    top_materials = (
        report_df.loc[report_df["谈判优先级"] == "优先谈判"]
        .nlargest(15, "最近12个月累计采购不含税金额(万)")
        .reset_index(drop=True)
    )
    for col_index, value in enumerate(["排名", "物料名称", "核心供应商", "采购金额(万元)", "图表标签"], start=10):
        chart_sheet.cell(8, col_index, value)
    for row_index, row in top_materials.iterrows():
        excel_row = row_index + 9
        material_name = str(row["物料名称"])
        short_name = material_name[:18] + ("..." if len(material_name) > 18 else "")
        chart_sheet.cell(excel_row, 10, row_index + 1)
        chart_sheet.cell(excel_row, 11, material_name)
        chart_sheet.cell(excel_row, 12, row["核心供应商名称"])
        chart_sheet.cell(excel_row, 13, float(row["最近12个月累计采购不含税金额(万)"]))
        chart_sheet.cell(excel_row, 14, f"Top {row_index + 1}  {short_name}")

    for row in chart_sheet.iter_rows(min_row=2, max_row=last_row, min_col=10, max_col=12):
        for cell in row:
            cell.border = table_border
            cell.alignment = Alignment(vertical="center")
    for row in chart_sheet.iter_rows(min_row=8, max_row=23, min_col=10, max_col=14):
        for cell in row:
            cell.border = table_border
            cell.alignment = Alignment(vertical="center", wrap_text=True)
    for header_range in ["J2:L2", "J8:N8"]:
        for cell in chart_sheet[header_range][0]:
            cell.fill = navy_fill
            cell.font = Font(bold=True, color="FFFFFF")
            cell.alignment = Alignment(horizontal="center", vertical="center")
    for row_index in range(3, last_row + 1):
        chart_sheet.cell(row_index, 12).number_format = "#,##0.0"
    for row_index in range(9, 24):
        chart_sheet.cell(row_index, 13).number_format = "#,##0.0"
        chart_sheet.row_dimensions[row_index].height = 30

    priority_chart = BarChart()
    priority_chart.type = "col"
    priority_chart.style = 10
    priority_chart.title = "各优先级覆盖采购金额（万元）"
    priority_chart.y_axis.title = "采购金额（万元）"
    priority_chart.height = 10
    priority_chart.width = 18
    priority_chart.add_data(
        Reference(chart_sheet, min_col=12, min_row=2, max_row=last_row), titles_from_data=True
    )
    priority_chart.set_categories(Reference(chart_sheet, min_col=10, min_row=3, max_row=last_row))
    priority_chart.legend = None
    priority_chart.dataLabels = DataLabelList()
    priority_chart.dataLabels.showVal = True
    chart_sheet.add_chart(priority_chart, "A8")

    top_chart = BarChart()
    top_chart.type = "bar"
    top_chart.style = 10
    top_chart.title = "优先谈判物料 Top 15（最近12个月采购金额，万元）"
    top_chart.x_axis.title = "采购金额（万元）"
    top_chart.height = 18
    top_chart.width = 18
    top_chart.add_data(
        Reference(chart_sheet, min_col=13, min_row=8, max_row=23), titles_from_data=True
    )
    top_chart.set_categories(Reference(chart_sheet, min_col=14, min_row=9, max_row=23))
    top_chart.legend = None
    chart_sheet.add_chart(top_chart, "A22")

    chart_sheet.merge_cells("A52:H53")
    chart_sheet["A52"] = "阅读方式：先看各优先级覆盖金额，判断谈判候选的整体规模；再看Top 15，优先核实金额较高物料的需求增长原因并准备谈判。"
    chart_sheet["A52"].fill = yellow_fill
    chart_sheet["A52"].alignment = Alignment(vertical="center", wrap_text=True)
    chart_sheet["A52"].font = Font(color="7F6000", size=10)

    for column, width in {"A": 12, "B": 12, "C": 4, "D": 16, "E": 12, "F": 4, "G": 12,
                          "H": 12, "J": 14, "K": 40, "L": 32, "M": 16, "N": 26}.items():
        chart_sheet.column_dimensions[column].width = width


def export_excel(result: Dict[str, Any], output_path: Path) -> Path:
    """导出 Excel 报告（运行摘要/报告/月度数据/规则说明/图表）。"""
    report_df = result["report"]
    monthly = result["monthly"]
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        _write_summary_sheet(writer, result)

        report_df.to_excel(writer, sheet_name="报告", index=False)
        report_sheet = writer.sheets["报告"]
        report_sheet.freeze_panes = "A2"
        report_sheet.row_dimensions[1].height = 36
        _format_columns(report_sheet, report_df)

        monthly.to_excel(writer, sheet_name="月度数据", index=False)

        rule_df = pd.DataFrame(RULE_ROWS, columns=["模块", "规则说明"])
        rule_df.to_excel(writer, sheet_name="规则说明", index=False)
        rule_sheet = writer.sheets["规则说明"]
        rule_sheet.freeze_panes = "A2"
        rule_sheet.column_dimensions["A"].width = 24
        rule_sheet.column_dimensions["B"].width = 100
        for header_cell in rule_sheet[1]:
            header_cell.alignment = Alignment(horizontal="center", vertical="center")
        for row in rule_sheet.iter_rows(min_row=2):
            row[0].alignment = Alignment(vertical="top", wrap_text=True)
            row[1].alignment = Alignment(vertical="top", wrap_text=True)
            rule_sheet.row_dimensions[row[0].row].height = 42

    workbook = load_workbook(output_path)
    _add_chart_sheet(workbook, result)
    workbook.save(output_path)
    return output_path


if __name__ == "__main__":
    print("本模块由 scripts/run_analysis.py 调用，不单独运行。")
