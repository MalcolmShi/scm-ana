#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按日期抽查接口数据 —— 翻页拉取 → 本地按日期筛 → 打印摘要 / 存 parquet。

为什么要「翻页 + 本地筛选」
--------------------------
入库记录接口的入参只有 ``page_num`` / ``page_size``，**没有日期参数**，服务端不做
日期下推。所以「取某一天的数据」只能是：翻若干页 → 在本地按 ``pt``（分区日期）或
``inv_inout_date``（出入库日期）过滤。

先小页数探一下 ``pt`` 的分布，再决定翻多少页，比一上来拉全量便宜得多。

用法
----
::

    # 看前 3 页里 pt 的分布（判断 pt 到底是快照日还是业务日）
    python scripts/peek_api.py --days 3

    # 取分区日期为 2026-10-25 的记录，最多翻 20 页（20 × 200 = 4000 行）
    python scripts/peek_api.py --pt 2026-10-25 --max-pages 20

    # 按出入库日期筛，并把命中结果存成 parquet 给业务核对
    python scripts/peek_api.py --date-field inv_inout_date --pt 2026-10-25 --save

    # 内网拉不到时，可以让内网同事存一份响应 JSON，本地回放验证解析与映射
    python scripts/peek_api.py --from-file /tmp/resp.json --pt 2026-10-25

退出码：0 = 命中数据；2 = 连通/鉴权失败；3 = 翻完了但没命中目标日期。
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from paths import PROJECT_ROOT
from connectors.procurement_inbound import ProcurementInboundClient, DataPlatformError
from envfile import DEFAULT_ENV_FILE, load_env
from cleaning.procurement_inbound import clean_procurement
from pipeline import resolve_source_id

#: 摘要里展示的关键列（存在才显示，适配原始和清洗后的字段名）
SUMMARY_COLUMNS = [
    "板块",
    "公司名称",
    "仓库名称",
    "物料编码",
    "物料名称",
    "出入库日期",
    "inv_inout_date",
    "数量合计",
    "inv_in_qty",
    "采购不含税金额(万)",
    "notax_purchase_order_amt",
    "供应商名称",
    "pt",
]


def fetch_rows(
    client: ProcurementInboundClient,
    max_pages: int,
    page_size: int,
) -> List[Dict[str, Any]]:
    """逐页拉取，最多 max_pages 页；每页打印一行进度。"""
    rows: List[Dict[str, Any]] = []
    page = client.start_page
    for _ in range(max_pages):
        batch = client.fetch_page(page, page_size)
        print(f"  第 {page} 页：{len(batch)} 行")
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < page_size:
            break
        page += 1
    return rows


def filter_rows(
    rows: List[Dict[str, Any]],
    field: str,
    target: Optional[str],
) -> List[Dict[str, Any]]:
    if not target:
        return rows
    return [row for row in rows if str(row.get(field) or "")[:10] == target]


def print_pt_distribution(rows: List[Dict[str, Any]], top: int = 5) -> None:
    counter = Counter(str(row.get("pt")) for row in rows)
    if not counter:
        return
    print(f"\npt（分区日期）分布 共 {len(counter)} 个值：")
    for value, count in counter.most_common(top):
        print(f"  {value:<12} {count:>6} 行")
    if len(counter) > top:
        print(f"  …… 另有 {len(counter) - top} 个")


def summarize(frame, label: str) -> None:
    if frame is None or len(frame) == 0:
        print(f"\n{label}：0 行")
        return

    print(f"\n{label}：{len(frame)} 行")

    def count_of(column: str) -> Optional[int]:
        return int(frame[column].nunique()) if column in frame.columns else None

    companies = count_of("公司名称") or count_of("company_name")
    items = count_of("物料编码") or count_of("item_num")
    suppliers = count_of("供应商名称") or count_of("supplier_name")
    if companies:
        print(f"  公司 {companies} 家 | 物料 {items} 个 | 供应商 {suppliers} 家")

    for column in ("出入库日期", "inv_inout_date"):
        if column in frame.columns:
            dates = frame[column].dropna().astype(str)
            if len(dates):
                print(f"  出入库日期范围：{dates.min()} ~ {dates.max()}")
                break

    qty_col = "数量合计" if "数量合计" in frame.columns else "inv_in_qty"
    if qty_col in frame.columns:
        qty = frame[qty_col].astype(float)
        print(f"  数量合计：{qty.sum():,.2f}（其中为 0 的行 {int((qty == 0).sum())} 行）")

    amount_col = "采购不含税金额(万)"
    if amount_col in frame.columns:
        print(f"  采购不含税金额：{frame[amount_col].astype(float).sum():,.4f} 万元")

    print("\n前 5 行：")
    columns = [c for c in SUMMARY_COLUMNS if c in frame.columns][:8]
    with_pd = frame[columns].head(5)
    print(with_pd.to_string(index=False, max_colwidth=28))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="按日期抽查入库记录接口数据")
    parser.add_argument("--source", default="procurement-inbound")
    parser.add_argument("--pt", default=None, help="目标日期 YYYY-MM-DD（默认按 pt 分区日期筛）")
    parser.add_argument("--date-field", default="pt",
                        help="筛选字段，默认 pt；可选 inv_inout_date（出入库日期）")
    parser.add_argument("--max-pages", type=int, default=5,
                        help="最多翻几页，默认 5；传 0 表示翻到底（受 max_pages 上限约束）")
    parser.add_argument("--page-size", type=int, default=200, help="每页条数，默认 200")
    parser.add_argument("--days", type=int, default=0,
                        help="只看 pt 分布时用：等价于 --max-pages N 且不筛日期")
    parser.add_argument("--from-file", default=None, help="回放已保存的响应 JSON（离线）")
    parser.add_argument("--save", action="store_true",
                        help="把命中结果存成 parquet（data/peek/<来源>/<日期>/）")
    parser.add_argument("--env-file", default=None)
    args = parser.parse_args(argv)

    load_env(Path(args.env_file) if args.env_file else DEFAULT_ENV_FILE)

    source_id = resolve_source_id(args.source)
    client = ProcurementInboundClient()
    target = args.pt
    field = args.date_field
    max_pages = args.days or args.max_pages
    if max_pages <= 0:  # 0 = 翻到底
        max_pages = client.max_pages

    if args.from_file:
        payload = json.loads(Path(args.from_file).read_text(encoding="utf-8"))
        try:
            rows = client._rows_of(payload)
        except DataPlatformError as exc:
            print(f"响应解析失败：{exc}")
            return 2
        print(f"回放文件 {args.from_file}：读入 {len(rows)} 行")
    else:
        print(f"接口 {client.url}")
        print(f"翻页 每页 {args.page_size} 条，最多 {max_pages} 页"
              f"（目标 {field} = {target or '不限'}）")
        try:
            rows = fetch_rows(client, max_pages, args.page_size)
        except DataPlatformError as exc:
            print(f"\n取数失败：{exc}")
            print("  排查：① 是否连内网/VPN（curl 同一地址应能返回 JSON）"
                  " ② .env 凭证是否完整（--check-env）")
            return 2

    print(f"\n累计 {len(rows)} 行")
    print_pt_distribution(rows)

    hit = filter_rows(rows, field, target)
    frame = clean_procurement(pd.DataFrame(hit)) if hit else None
    label = f"{field} = {target}" if target else "全部"
    summarize(frame, label)

    if target and not hit:
        print(f"\n提示：已翻的 {max_pages} 页里没有 {field} = {target}。")
        print("  · 目标日期可能不在前几页（接口按 id 升序，日期不保证有序）→ 加大 --max-pages")
        print("  · 或 pt 是快照日期而非业务日期 → 换 --date-field inv_inout_date 再试")
        return 3

    if args.save and frame is not None and len(frame):
        day = target or datetime.now().strftime("%Y-%m-%d")
        out_dir = PROJECT_ROOT / "data" / "peek" / source_id / day[:7] / day
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / "peek.parquet"
        frame.to_parquet(out, index=False)
        size_kb = out.stat().st_size / 1024
        print(f"\n已存 {out.relative_to(PROJECT_ROOT)}（{len(frame)} 行，{size_kb:.1f} KB）")
        print("  只做抽查用；正式取数走 scripts/sync_data.py")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
