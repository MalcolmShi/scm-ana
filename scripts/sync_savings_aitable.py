#!/usr/bin/env python3
"""采购降本：按月 upsert 同步 demo 数据到钉钉多维表。

对应 references/procurement-savings.md 的「已建目标表」约定。

用法：
  uv run python scripts/sync_savings_aitable.py            # 默认同步最近一个完整自然月
  uv run python scripts/sync_savings_aitable.py 2026-10    # 指定核算月份

同步范围（仅系统生成的三张表；替换关系/年度目标/返利反馈为采购员填报，系统只读不写）：
  - 降本初稿   fYsZ9n8  聚合键：核算月份+物料编码+供应商名称+原含税价+现含税价+核算类型+新增存量
  - 待确认事项 pW6mNiE  业务键：出入库月份+物料编码+供应商名称+采购单价+原因
  - 税率变化   fp0xEJ1  业务键：供应商编码+物料编码+新协议编号+新版本号

保护规则：
  - 反馈字段（初稿：复核状态/采购意见/复核人；待确认：处理状态/反馈说明/采购负责人）永不写入；
  - 已有记录只按差异更新系统字段，值未变化的记录不发更新；
  - 单批 ≤30 条；写后回读核对当月条数与金额，结果异常时停止并报告，不盲目重放。

切换正式数据：改 DB_PATH / 源表名 / 字段名映射后复用同一业务键与保护规则。
"""

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import duckdb

BASE_ID = "XPwkYGxZV3RPp5xXirjvgKMkWAgozOKL"
DB_PATH = "data/procurement_savings_demo.duckdb"
TABLE_IDS = {"降本初稿": "fYsZ9n8", "待确认事项": "pW6mNiE", "税率变化": "fp0xEJ1"}
BATCH_SIZE = 30

# 各表：业务键（本地字段名）+ 系统字段 + 反馈字段（只建不写）+ 新建默认值
SYNC_SPEC = {
    "降本初稿": {
        "keys": ["核算月份", "物料编码", "供应商名称", "原含税价", "现含税价", "核算类型", "新增存量"],
        "query_month_field": "核算月份",
        "protected": ["复核状态", "采购意见", "复核人"],
        "create_defaults": {"复核状态": "待复核"},
    },
    "待确认事项": {
        "keys": ["出入库月份", "物料编码", "供应商名称", "采购单价", "原因"],
        "query_month_field": "出入库月份",
        "protected": ["处理状态", "反馈说明", "采购负责人"],
        "create_defaults": {"处理状态": "待确认"},
    },
    "税率变化": {
        "keys": ["供应商编码", "物料编码", "新协议编号", "新版本号"],
        "query_month_field": None,  # 记录量小，全量拉取后本地按键对齐
        "month_source_field": "新价格生效日期",  # 本地按该字段前缀筛当月
        "protected": [],
        "create_defaults": {},
    },
}


# ------------------------------------------------------------------ 基础
def dws(args, timeout=180):
    cmd = ["dws", "aitable"] + args + ["--format", "json"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    payload = None
    for stream in (r.stdout, r.stderr):
        s = (stream or "").strip()
        if s.startswith("{"):
            try:
                payload = json.loads(s)
                break
            except json.JSONDecodeError:
                pass
    if payload is None:
        raise RuntimeError(f"dws 输出不可解析: {r.stdout[:300]} | stderr: {r.stderr[:300]}")
    if payload.get("error"):
        raise RuntimeError(f"dws 调用失败: {json.dumps(payload['error'], ensure_ascii=False)[:500]}")
    if not payload.get("success") and "data" not in payload:
        raise RuntimeError(f"dws 返回异常: {json.dumps(payload, ensure_ascii=False)[:500]}")
    return payload.get("data")


def field_map(table_id):
    got = dws(["field", "get", "--base-id", BASE_ID, "--table-id", table_id])
    tables = got.get("tables") or [got]
    return {f["fieldName"]: f["fieldId"] for t in tables for f in t.get("fields", [])}


def fnum(v, nd=4):
    return None if v is None else round(float(v), nd)


def month_key(v):
    s = str(v or "")
    return s[:7] if len(s) >= 7 else s


# ------------------------------------------------------------------ 本地数据
def load_month(month):
    con = duckdb.connect(DB_PATH, read_only=True)

    detail = con.execute("""
        SELECT 物料名称, 核算月份, CAST(物料编码 AS VARCHAR) 物料编码, 供应商名称,
               CAST(MIN(公司编码) AS VARCHAR) 公司编码, MIN(公司名称) 公司名称, MIN(五级分类) 五级分类,
               核算类型, MIN(降本来源) 降本来源, 原含税价, MIN(原计价单位) 原计价单位,
               现含税价, MIN(新计价单位) 新计价单位,
               SUM(实际入库数量) 实际入库数量, SUM(核算数量) 核算数量, MIN(核算单位) 核算单位,
               MIN(单位降本) 单位降本, SUM(降本金额) 降本金额, MIN(核算开始) 核算开始, MAX(核算结束) 核算结束,
               新增存量, COUNT(*) 明细笔数,
               STRFTIME(MIN(TRY_CAST(出入库日期 AS TIMESTAMP)), '%Y-%m-%d') 首次入库日期,
               STRFTIME(MAX(TRY_CAST(出入库日期 AS TIMESTAMP)), '%Y-%m-%d') 末次入库日期
        FROM demo_savings.detail
        WHERE 核算月份 = ?
        GROUP BY 物料名称, 核算月份, 物料编码, 供应商名称, 原含税价, 现含税价, 核算类型, 新增存量
        ORDER BY 物料编码
    """, [month]).fetchall()
    初稿 = [{
        "物料名称": r[0], "核算月份": r[1], "物料编码": r[2], "供应商名称": r[3],
        "公司编码": r[4], "公司名称": r[5], "五级分类": r[6], "核算类型": r[7], "降本来源": r[8],
        "原含税价": fnum(r[9]), "原计价单位": r[10], "现含税价": fnum(r[11]), "新计价单位": r[12],
        "实际入库数量": fnum(r[13], 2), "核算数量": fnum(r[14], 2), "核算单位": r[15],
        "单位降本": fnum(r[16]), "降本金额": fnum(r[17], 2), "核算开始": r[18], "核算结束": r[19],
        "新增存量": r[20], "明细笔数": r[21], "首次入库日期": r[22], "末次入库日期": r[23],
    } for r in detail]

    exc = con.execute("""
        SELECT CAST(物料编码 AS VARCHAR), 原因, 供应商名称, 采购单价, 数量,
               STRFTIME(TRY_CAST(出入库日期 AS TIMESTAMP), '%Y-%m-%d'),
               CAST(公司编码 AS VARCHAR), 公司名称, 五级分类, 采购负责人
        FROM demo_savings.exceptions
        WHERE STRFTIME(TRY_CAST(出入库日期 AS TIMESTAMP), '%Y-%m') = ?
        ORDER BY 物料编码
    """, [month]).fetchall()
    待确认 = [{
        "出入库月份": month, "物料编码": r[0], "原因": r[1], "供应商名称": r[2],
        "采购单价": fnum(r[3]), "数量": r[4], "出入库日期": r[5], "公司编码": r[6],
        "公司名称": r[7], "五级分类": r[8], "负责人姓名": r[9],
    } for r in exc]

    tax = con.execute("""
        SELECT 产品名称, 供应商编码, 供应商名称, CAST(物料编码 AS VARCHAR), 规格, 管理组织, 采购模式,
               旧协议编号, CAST(旧版本号 AS VARCHAR), 原税率,
               旧价格生效日期, 旧价格失效日期, 新协议编号, CAST(新版本号 AS VARCHAR), 新税率,
               新价格生效日期, 新价格失效日期
        FROM demo_savings.tax_changes
        WHERE 新价格生效日期 LIKE ? || '%'
    """, [month]).fetchall()
    税率 = [{
        "产品名称": r[0], "供应商编码": r[1], "供应商名称": r[2], "物料编码": r[3], "规格": r[4],
        "管理组织": r[5], "采购模式": r[6], "旧协议编号": r[7], "旧版本号": r[8], "原税率": fnum(r[9]),
        "旧价格生效日期": r[10], "旧价格失效日期": r[11], "新协议编号": r[12], "新版本号": r[13],
        "新税率": fnum(r[14]), "新价格生效日期": r[15], "新价格失效日期": r[16],
    } for r in tax]

    con.close()
    return {"降本初稿": 初稿, "待确认事项": 待确认, "税率变化": 税率}


# ------------------------------------------------------------------ 远端读取
def fetch_remote_rows(table_id, month_field_fid, month, key_fids, extra_fids=()):
    """按月份过滤（month_field_fid 为空则全量）拉取全部字段，返回 [(recordId, key_tuple, cells_dict)]。
    不做 field-ids 裁剪：diff 比对需要全部系统字段。"""
    args = ["record", "query", "--base-id", BASE_ID, "--table-id", table_id, "--limit", "100"]
    if month_field_fid:
        filters = {"operator": "and",
                   "operands": [{"operator": "eq", "operands": [month_field_fid, month]}]}
        args += ["--filters", json.dumps(filters)]
    rows, cursor = [], None
    seen_cursors = set()
    while True:
        cmd = args + ([ "--cursor", cursor] if cursor else [])
        data = dws(cmd)
        for rec in data.get("records", []):
            cells = rec.get("cells", {})
            key = tuple(_norm_key(cells.get(fid)) for fid in key_fids)
            rows.append((rec["recordId"], key, cells))
        cursor = data.get("nextCursor")
        if not cursor or cursor in seen_cursors:
            break
        seen_cursors.add(cursor)
    return rows


def _norm_key(v):
    """键值归一：两侧同规则。singleSelect 回读为 {id,name}，数字回读为字符串，统一为 name/float。"""
    if isinstance(v, dict):
        v = v.get("name")
    elif isinstance(v, list):
        if not v:
            return None
        v = v[0].get("name") if isinstance(v[0], dict) else v[0]
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return round(float(v), 4)
    s = str(v).strip()
    if not s:
        return None
    try:
        return round(float(s), 4)
    except ValueError:
        return s


# ------------------------------------------------------------------ 同步
def sync_table(name, month, rows_local):
    spec = SYNC_SPEC[name]
    tid = TABLE_IDS[name]
    fmap = field_map(tid)
    fid = {cn: fmap[cn] for cn in fmap}
    key_fids = [fid[k] for k in spec["keys"]]
    month_fid = fid[spec["query_month_field"]] if spec["query_month_field"] else None
    # 全量拉取型表：额外取月份来源字段，本地按前缀筛当月
    extra_need = spec.get("month_source_field")
    extra_need_fid = fid[extra_need] if extra_need else None

    remote = fetch_remote_rows(tid, month_fid, month, key_fids, extra_fids=[extra_need_fid] if extra_need_fid else [])
    if extra_need_fid and not month_fid:
        remote = [(rid, key, cells) for rid, key, cells in remote
                  if str(cells.get(extra_need_fid) or "").startswith(month)]
    remote_by_key = {}
    for rid, key, cells in remote:
        remote_by_key.setdefault(key, []).append((rid, cells))

    created = updated = unchanged = 0
    skipped_dup_keys = []

    for row in rows_local:
        key = tuple(_norm_key(row.get(k)) for k in spec["keys"])
        writable = {cn: v for cn, v in row.items()
                    if cn in fmap and cn not in spec["protected"] and v is not None}
        matches = remote_by_key.get(key, [])
        if not matches:
            cells = {fid[cn]: v for cn, v in writable.items()}
            for cn, v in spec.get("create_defaults", {}).items():
                cells[fid[cn]] = v
            dws(["record", "create", "--base-id", BASE_ID, "--table-id", tid,
                 "--records", json.dumps([{"cells": cells}], ensure_ascii=False)])
            created += 1
            continue
        if len(matches) > 1:
            skipped_dup_keys.append(key)  # 键冲突不盲改，先报告
            continue
        rid, remote_cells = matches[0]
        diff = {}
        for cn, v in writable.items():
            rv = _norm_key(remote_cells.get(fid[cn]))
            lv = _norm_key(v)
            if rv != lv:
                diff[fid[cn]] = v
        if diff:
            dws(["record", "update", "--base-id", BASE_ID, "--table-id", tid,
                 "--records", json.dumps([{"recordId": rid, "cells": diff}], ensure_ascii=False)])
            updated += 1
        else:
            unchanged += 1

    # 多余远端行（本地没有的键）：只报告，不删除
    local_keys = {tuple(_norm_key(r.get(k)) for k in spec["keys"]) for r in rows_local}
    extra = sum(1 for rid, key, _ in remote if key not in local_keys)

    print(f"[sync] {name} {month}: 本地 {len(rows_local)} | 新建 {created} | 更新 {updated} | "
          f"一致 {unchanged} | 远端多余 {extra}"
          + (f" | 键冲突 {len(skipped_dup_keys)} 组(未处理)" if skipped_dup_keys else ""))
    return {"local": len(rows_local), "created": created, "updated": updated,
            "unchanged": unchanged, "extra": extra, "dup": len(skipped_dup_keys)}


def verify_month(name, month, local_rows=None):
    if name == "降本初稿":
        rows = local_rows if local_rows is not None else load_month(month)["降本初稿"]
        exp_cnt = len(rows)
        exp_amt = round(sum(r["降本金额"] for r in rows), 2)
        fmap = field_map(TABLE_IDS[name])
        got = dws(["record", "stats", "--base-id", BASE_ID, "--table-id", TABLE_IDS[name],
                   "--filters", json.dumps({"operator": "and", "operands": [
                       {"operator": "eq", "operands": [fmap["核算月份"], month]}]}),
                   "--stats", json.dumps([
                       {"fieldId": fmap["物料名称"], "statsType": "COUNT"},
                       {"fieldId": fmap["降本金额"], "statsType": "SUM"}])])
        res = got["results"]
        if isinstance(res, list):
            res = res[0]["results"]
        cnt = next(r["value"] for r in res if r["statsType"] == "COUNT")
        amt = round(next(r["value"] for r in res if r["statsType"] == "SUM"), 2)
        ok = cnt == exp_cnt and amt == exp_amt
        print(f"[verify] {name} {month}: 表内 {cnt} 行/{amt} 元 vs 本地聚合 {exp_cnt} 行/{exp_amt} 元 "
              f"[{'OK' if ok else 'MISMATCH'}]")
        return ok
    return True


def main():
    if len(sys.argv) > 1:
        month = sys.argv[1]
    else:
        today = date.today()
        prev = date(today.year, today.month, 1)
        from datetime import timedelta
        month = (prev - timedelta(days=1)).strftime("%Y-%m")
    print(f"[sync] 目标月份: {month}")
    data = load_month(month)
    summary = {}
    for name in ["降本初稿", "待确认事项", "税率变化"]:
        summary[name] = sync_table(name, month, data[name])
    ok = verify_month("降本初稿", month, data["降本初稿"])
    Path("outputs").exists() or Path("outputs").mkdir(parents=True)
    Path(f"outputs/savings_sync_{month}.json").write_text(
        json.dumps({"month": month, "summary": summary, "verified": ok},
                   ensure_ascii=False, indent=1))
    if summary["降本初稿"]["dup"] or not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
