"""基于小样例生成可重复的纯虚构演示数据，不读取或写入业务数据库。"""
from __future__ import annotations

import csv
import hashlib
from pathlib import Path
from random import Random
import sys
from decimal import Decimal


ROOT = Path(__file__).resolve().parent
RNG = Random(42)
sys.path.insert(0, str(ROOT.parents[2] / "src"))
from clean.procurement_inbound import STANDARD_COLUMNS, UPDATED_AT_COLUMN


def mock_numeric_id(value: str, domain: str) -> int:
    """生成稳定的虚构数值编码；不能与真实编码建立对应关系。"""
    text = str(value)
    if text.isdigit():
        return int(text)
    digest = hashlib.sha256(f"{domain}:{text}".encode()).digest()
    return 100_000_000 + int.from_bytes(digest[:8], "big") % 800_000_000


def make_processed_receipt(day, company_code, company_name, supplier, item, item_name, spec, unit, category, price, quantity, *, tax=0.13, mode="集中采购"):
    """只构造 processed 真实字段；金额单位为万元。"""
    amount = Decimal(str(price)) * Decimal(str(quantity)) / Decimal("10000")
    row = {
        "出入库日期": day,
        "物料编码": mock_numeric_id(item, "material"), "物料名称": item_name,
        "规格": spec, "主单位": unit,
        "一级分类": "演示一级", "二级分类": "演示二级", "三级分类": "演示三级", "四级分类": "演示四级", "五级分类": category,
        "品牌": "演示品牌", "主品牌": "演示主品牌", "货号": "演示货号",
        "供应商名称": supplier, "供应商类型": "外部单位", "板块": "诊断",
        "公司编码": mock_numeric_id(company_code, "company"), "公司名称": company_name, "公司简称": company_name, "大区": "平台",
        "数据来源系统": "nc", "设备试剂分类": "试剂/耗材", "采购模式": mode,
        "数量": quantity, "采购单价": price, "采购金额(万)": str(amount), "税率": tax,
        "updated_at": "2026-11-03T08:00:00+08:00",
    }
    return {column: row[column] for column in STANDARD_COLUMNS + [UPDATED_AT_COLUMN]}


def read(name: str) -> tuple[list[str], list[dict]]:
    with (ROOT / name).open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        return list(reader.fieldnames or []), list(reader)


def write(name: str, columns: list[str], rows: list[dict]) -> None:
    with (ROOT / name).open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def make_data() -> None:
    protocol_cols, protocols = read("模拟协议版本.csv")
    receipt_cols, receipts = read("模拟采购入库.csv")
    assert receipt_cols == STANDARD_COLUMNS + [UPDATED_AT_COLUMN], "模拟入库字段与 processed 标准字段不一致"
    replacement_cols, replacements = read("模拟替换填报.csv")
    rebate_cols = ["采购组织", "供应商", "物料", "五级分类", "执行日期", "返利比例", "返利条款"]
    rebates = []

    def protocol(agreement, version, supplier_id, supplier, item, price, tax, start, end, *, org="演示总部", mode="集中采购"):
        return dict(zip(protocol_cols, [agreement, version, org, mode, supplier_id, supplier,
                                       item, "演示产品" + item, "100测试/盒", price, tax,
                                       start, end, "DEMO-适用组织"]))

    def receipt(number, day, org, supplier, item, price, quantity, category, spec="100测试/盒", *, tax=0.13, mode="集中采购"):
        # number 仅作为生成器内的场景标记，不写入 processed 字段。
        return make_processed_receipt(day, org, "演示公司" + org[-1], supplier, item,
                                      "演示产品" + item, spec, "盒", category, price, quantity, tax=tax, mode=mode)

    # 每种物料先在旧协议降价，再跨协议降价；部分旧贡献跨年进入存量。
    for i in range(1, 121):
        item = f"MOCK-M{i:04d}"
        supplier_id = f"MOCK-S{(i - 1) % 12 + 1:02d}"
        supplier = f"演示供应商{(i - 1) % 12 + 1:02d}"
        org = f"演示管理组织{(i - 1) % 3 + 1}"
        mode = ["集中采购", "定向采购", "自行采购"][i % 3]
        old, middle, new = 120 + i % 35, 110 + i % 35, 105 + i % 35
        old_start = "2025-12-01" if i % 6 == 0 else "2026-02-01"
        first_start = "2025-09-01" if i % 6 == 0 else "2026-01-01"
        first_end = "2025-11-30" if i % 6 == 0 else "2026-01-31"
        # 含连续、间隔及价格有效期重叠三种情况。
        old_end, new_start = [("2026-06-30", "2026-07-01"),
                              ("2026-05-31", "2026-08-01"),
                              ("2026-08-31", "2026-07-01")][i % 3]
        agreement_a, agreement_b = f"MOCK-A{i:04d}", f"MOCK-B{i:04d}"
        protocols += [protocol(agreement_a, 1, supplier_id, supplier, item, old, 0.13, first_start, first_end, org=org, mode=mode),
                      protocol(agreement_a, 2, supplier_id, supplier, item, middle, 0.13, old_start, old_end, org=org, mode=mode),
                      protocol(agreement_b, 1, supplier_id, supplier, item, new, 0.09 if i % 10 == 0 else 0.13,
                               new_start, "2027-07-31", org=org, mode=mode)]
        # 明确构造 1—9 月的演示记录，供年度累计与目标完成率演示。
        for month in range(1, 10):
            day = f"2026-{month:02d}-15"
            if day >= new_start:
                price = new
            elif old_start <= day <= old_end:
                price = middle
            else:
                continue  # 模拟该物料此月没有业务，不代表真实数据缺档可按零处理。
            receipts.append(receipt(f"MOCK-H{i:04d}-{month:02d}", day, f"MOCK-ORG{(i + month) % 4 + 1}",
                                    supplier, item, price, RNG.randint(5, 50), f"演示类别{(i - 1) % 5 + 1}",
                                    tax=0.09 if price == new and i % 10 == 0 else 0.13, mode=mode))
        for j, day in enumerate([2, 5, 8, 11, 14, 17, 20, 23], start=1):
            company = f"MOCK-ORG{(i + j) % 4 + 1}"
            receipts.append(receipt(f"MOCK-I{i:04d}-{j}", f"2026-10-{day:02d}", company, supplier, item,
                                    new, RNG.randint(5, 50), f"演示类别{(i - 1) % 5 + 1}",
                                    tax=0.09 if i % 10 == 0 else 0.13, mode=mode))
        if i % 4 == 0:
            receipts.append(receipt(f"MOCK-RET{i:04d}", "2026-10-26", f"MOCK-ORG{i % 4 + 1}",
                                    supplier, item, new, -RNG.randint(1, 5), f"演示类别{(i - 1) % 5 + 1}",
                                    tax=0.09 if i % 10 == 0 else 0.13, mode=mode))
        if i % 6 == 0:
            rebates.append(dict(zip(rebate_cols, [f"MOCK-ORG{i % 4 + 1}", supplier, item,
                f"演示类别{(i - 1) % 5 + 1}", "2026-10-01", round(0.02 + (i // 6 % 3) * 0.01, 2), "演示返利条款，仅供反馈"])))

    # 同一物料存在两个符合条件的旧协议，应交采购员复核。
    for i in range(1, 13):
        item, supplier = f"MOCK-AMB{i:03d}", f"演示多协议供应商{i:02d}"
        supplier_id = f"MOCK-AS{i:03d}"
        protocols += [protocol(f"MOCK-D1-{i:03d}", 1, supplier_id, supplier, item, 100, 0.13, "2026-01-01", "2026-08-31"),
                      protocol(f"MOCK-D2-{i:03d}", 1, supplier_id, supplier, item, 95, 0.13, "2026-01-01", "2026-09-30"),
                      protocol(f"MOCK-D3-{i:03d}", 1, supplier_id, supplier, item, 70, 0.13, "2026-10-01", "2027-09-30")]
        receipts.append(receipt(f"MOCK-AMB-I{i:03d}", "2026-10-15", "MOCK-ORG1", supplier, item, 70, 20, "演示待复核类"))

    # 降价后价格回升：先由采购员确认价格性质。
    for i in range(1, 11):
        item, supplier = f"MOCK-UP{i:03d}", f"演示回升供应商{i:02d}"
        supplier_id, agreement = f"MOCK-US{i:03d}", f"MOCK-UP-A{i:03d}"
        protocols += [protocol(agreement, 1, supplier_id, supplier, item, 100, 0.13, "2026-01-01", "2026-01-31"),
                      protocol(agreement, 2, supplier_id, supplier, item, 90, 0.13, "2026-02-01", "2026-09-30"),
                      protocol(agreement, 3, supplier_id, supplier, item, 95, 0.13, "2026-10-01", "2027-09-30")]
        receipts.append(receipt(f"MOCK-UP-I{i:03d}", "2026-10-16", "MOCK-ORG1", supplier, item, 95, 10, "演示待复核类"))

    for kind in ["渠道替换", "品牌替换", "规格替换"]:
        for i in range(1, 16):
            short = {"渠道替换": "CH", "品牌替换": "BR", "规格替换": "SP"}[kind]
            code = f"MOCK-{short}{i:03d}"
            old_item = code + "-OLD" if kind != "渠道替换" else code
            new_item = code + "-NEW" if kind != "渠道替换" else code
            company = f"MOCK-ORG{i % 4 + 1}"
            old_supplier, new_supplier = f"演示旧供应商{short}{i:03d}", f"演示新供应商{short}{i:03d}"
            old_price, new_price, old_factor, new_factor = ((100, 90, 1, 1) if kind == "渠道替换" else
                                                            (100, 45, 100, 50) if kind == "品牌替换" else
                                                            (120, 50, 100, 50))
            replacements.append(dict(zip(replacement_cols, [f"MOCK-R-{short}{i:03d}", kind, company,
                old_supplier, new_supplier, old_item, new_item, old_price, new_price, "2026-08-01", "盒", "盒",
                "盒" if kind == "渠道替换" else "测试", old_factor, new_factor, "演示用业务依据", "已确认"])))
            receipts.append(receipt(f"MOCK-RI-{short}{i:03d}", "2026-10-13", company, new_supplier,
                                    new_item, new_price, RNG.randint(5, 25), "演示替换类", "演示规格"))

    # 统一虚构编码格式，保证与 processed 中物料/公司编码的数值类型一致。
    for row in protocols:
        row["物料编码"] = mock_numeric_id(row["物料编码"], "material")
    for row in replacements:
        row["原物料编码"] = mock_numeric_id(row["原物料编码"], "material")
        row["新物料编码"] = mock_numeric_id(row["新物料编码"], "material")
        row["适用采购主体"] = mock_numeric_id(row["适用采购主体"], "company")
    for row in rebates:
        row["物料"] = mock_numeric_id(row["物料"], "material")
        row["采购组织"] = mock_numeric_id(row["采购组织"], "company")

    for name, columns, rows in [("模拟协议版本-扩展.csv", protocol_cols, protocols),
                                ("模拟采购入库-扩展.csv", receipt_cols, receipts),
                                ("模拟替换填报-扩展.csv", replacement_cols, replacements),
                                ("模拟返利反馈-扩展.csv", rebate_cols, rebates)]:
        write(name, columns, rows)
        print(f"{name}: {len(rows)} 行")


if __name__ == "__main__":
    make_data()
