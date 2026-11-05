#!/usr/bin/env python3
"""初始化钉钉「采购降本」Base 的 6 张数据表，并从本地演示库写入数据。

用途（对应 .workbuddy/skills/aitable-sync/references/procurement-savings.md）：
  uv run python scripts/init_savings_aitable.py structure  # 建表与字段（幂等，已存在则跳过）
  uv run python scripts/init_savings_aitable.py data       # 转换演示数据并分批写入（带断点台账）
  uv run python scripts/init_savings_aitable.py verify     # 回读核对行数与关键金额

约定：
  - 演示库人员均为虚构姓名，user 类型字段一律留空，不编造 userId；
  - 编码类字段写入文本，保留完整值；税率/返利比例保持小数（0.13=13%），不乘 100；
  - 初稿按 核算月份+物料编码+供应商名称+原含税价+现含税价+核算类型+新增存量 聚合；
  - 复核/反馈字段只建不写，归采购员维护；单批写入 ≤30 条，失败即停，不盲目重放。
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import duckdb

BASE_NAME = "采购降本"
BASE_ID = "XPwkYGxZV3RPp5xXirjvgKMkWAgozOKL"
DB_PATH = "data/procurement_savings_demo.duckdb"
LEDGER_PATH = Path("outputs/savings_aitable_ledger.json")
BATCH_SIZE = 30

# ---------------------------------------------------------------- 字段定义
# 每张表：前 15 个字段随 table create 建立，其余走 field create。

def T(name):
    return {"fieldName": name, "type": "text"}

def N(name):
    return {"fieldName": name, "type": "number"}

def SEL(name, *options):
    return {"fieldName": name, "type": "singleSelect",
            "config": {"options": [{"name": o} for o in options]}}

def U(name):
    return {"fieldName": name, "type": "user"}

TABLES = {
    "替换关系": [
        T("替换单号"), SEL("替换类型", "渠道替换", "品牌替换", "规格替换"),
        T("适用采购主体"), T("原供应商"), T("新供应商"), T("原物料编码"), T("新物料编码"),
        N("原含税价"), N("现含税价"), T("价格生效日期"), T("原计价单位"), T("新计价单位"),
        T("可比单位"), N("原换算系数"), N("新换算系数"),
        T("业务依据"), SEL("复核状态", "待复核", "已确认", "有异议"),
    ],
    "年度目标": [
        T("目标名称"), T("年度"), T("负责人姓名"), U("采购负责人"), N("年度目标金额"),
    ],
    "返利反馈": [
        T("返利记录"), T("采购组织"), T("供应商"), T("物料"), T("五级分类"),
        T("执行日期"), N("返利比例"), T("返利条款"),
    ],
    "降本初稿": [
        T("物料名称"), T("核算月份"), T("物料编码"), T("供应商名称"), T("公司编码"),
        T("公司名称"), T("五级分类"), SEL("核算类型", "直接降价", "品牌替换", "渠道替换", "规格替换"),
        T("降本来源"), N("原含税价"), T("原计价单位"), N("现含税价"), T("新计价单位"),
        N("实际入库数量"), N("核算数量"),
        T("核算单位"), N("单位降本"), N("降本金额"), T("核算开始"), T("核算结束"),
        SEL("新增存量", "新增", "存量"), N("明细笔数"), T("首次入库日期"), T("末次入库日期"),
        SEL("复核状态", "待复核", "已确认", "有异议"), T("采购意见"), U("复核人"),
    ],
    "待确认事项": [
        T("物料编码"), SEL("原因", "价格回升待采购员确认", "多个上游待复核",
                        "当前价格无可确认的前序价格", "未匹配有效协议价格或替换关系"),
        T("供应商名称"), N("采购单价"), N("数量"), T("出入库日期"), T("公司编码"),
        T("公司名称"), T("五级分类"), T("负责人姓名"), U("采购负责人"),
        SEL("处理状态", "待确认", "已确认", "关闭"), T("反馈说明"),
    ],
    "税率变化": [
        T("产品名称"), T("供应商编码"), T("供应商名称"), T("物料编码"), T("规格"),
        T("管理组织"), T("采购模式"), T("旧协议编号"), T("旧版本号"), N("原税率"),
        T("旧价格生效日期"), T("旧价格失效日期"), T("新协议编号"), T("新版本号"), N("新税率"),
        T("新价格生效日期"), T("新价格失效日期"),
    ],
}

TABLE_IDS = {}  # tableName -> tableId，structure 阶段填充并落盘


# ---------------------------------------------------------------- dws 调用
def dws(args, timeout=120):
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
    if not payload.get("success"):
        raise RuntimeError(f"dws 调用失败: {json.dumps(payload.get('error', payload), ensure_ascii=False)[:500]}")
    return payload["data"]


def dws_retry(args, tries=5, delay=2.0):
    """建表后元数据存在同步延迟，ResourceNotFound 时按节奏重试。"""
    last = None
    for i in range(tries):
        try:
            return dws(args)
        except RuntimeError as e:
            last = e
            if "ResourceNotFound" in str(e) and i < tries - 1:
                time.sleep(delay)
                continue
            raise
    raise last


def norm_date(v):
    """演示库日期为 VARCHAR，统一为 YYYY-MM-DD 文本。"""
    if v is None:
        return None
    s = str(v).strip()
    return s[:10] if len(s) >= 10 else s


# ---------------------------------------------------------------- structure
def ensure_base():
    """Base 已由前序步骤创建；这里按名称搜索兜底，防止 ID 变动。"""
    global BASE_ID
    data = dws(["base", "search", "--query", BASE_NAME])
    for b in data.get("bases", []):
        if b["baseName"] == BASE_NAME:
            if b["baseId"] != BASE_ID:
                BASE_ID = b["baseId"]
            return BASE_ID
    created = dws(["base", "create", "--name", BASE_NAME])
    BASE_ID = created["baseId"]
    print(f"[base] 已创建 {BASE_NAME}: {BASE_ID}")
    return BASE_ID


def ensure_table(name, fields):
    existing = dws(["base", "get", "--base-id", BASE_ID])
    tables = {t["tableName"]: t["tableId"] for t in existing.get("tables", [])}
    if name in tables:
        TABLE_IDS[name] = tables[name]
        print(f"[table] {name} 已存在 ({tables[name]})")
    else:
        head = fields[:15]
        created = dws(["table", "create", "--base-id", BASE_ID, "--name", name,
                       "--fields", json.dumps(head, ensure_ascii=False)])
        tid = created["tables"][0]["tableId"] if "tables" in created else created["tableId"]
        TABLE_IDS[name] = tid
        print(f"[table] 已创建 {name} ({tid})，首建 {len(head)} 字段")
    # 补齐剩余字段（跳过已存在的同名字段）
    got = dws_retry(["field", "get", "--base-id", BASE_ID, "--table-id", TABLE_IDS[name]])
    have = set()
    for t in (got.get("tables") or [got]):
        for f in t.get("fields", []):
            have.add(f["fieldName"])
    full = {f["fieldName"] for f in fields}
    for spec in fields:
        if spec["fieldName"] in have:
            continue
        # field create 可能返回 success 但未落库：创建后轮询确认可见，最多重试 3 轮
        landed = False
        for attempt in range(3):
            cmd = ["field", "create", "--base-id", BASE_ID, "--table-id", TABLE_IDS[name],
                   "--name", spec["fieldName"], "--type", spec["type"]]
            if spec.get("config"):
                cmd += ["--config", json.dumps(spec["config"], ensure_ascii=False)]
            dws(cmd)
            for _ in range(6):
                time.sleep(2)
                chk = dws_retry(["field", "get", "--base-id", BASE_ID, "--table-id", TABLE_IDS[name]])
                names_now = {f["fieldName"] for t in (chk.get("tables") or [chk])
                             for f in t.get("fields", [])}
                if spec["fieldName"] in names_now:
                    landed = True
                    break
            if landed:
                break
            print(f"[field] {name}.{spec['fieldName']} 未落库，重试 {attempt + 1}/3")
        if not landed:
            raise RuntimeError(f"{name}.{spec['fieldName']} 三轮创建后仍不可见")
        print(f"[field] {name}.{spec['fieldName']} 已创建并确认可见")
    # 完整性校验
    got = dws_retry(["field", "get", "--base-id", BASE_ID, "--table-id", TABLE_IDS[name]])
    names = {f["fieldName"] for t in (got.get("tables") or [got]) for f in t.get("fields", [])}
    missing = set(full) - names
    if missing:
        raise RuntimeError(f"{name} 缺字段: {missing}")


def phase_structure():
    ensure_base()
    for name, fields in TABLES.items():
        ensure_table(name, fields)
    outdir = Path("outputs")
    if not outdir.exists():
        outdir.mkdir(parents=True)
    outdir.joinpath("savings_aitable_tables.json").write_text(
        json.dumps({"baseId": BASE_ID, "tableIds": TABLE_IDS}, ensure_ascii=False, indent=2))
    print(f"[done] 结构就绪: {json.dumps(TABLE_IDS, ensure_ascii=False)}")


# ---------------------------------------------------------------- data
def fnum(v, nd=4):
    return None if v is None else round(float(v), nd)


def load_data():
    con = duckdb.connect(DB_PATH, read_only=True)
    data = {}

    rows = con.execute("""
        SELECT 演示替换编号, 替换类型, CAST(适用采购主体 AS VARCHAR), 原供应商, 新供应商,
               CAST(原物料编码 AS VARCHAR), CAST(新物料编码 AS VARCHAR),
               原含税价, 现含税价, norm_date, 原计价单位, 新计价单位, 可比单位,
               原换算系数, 新换算系数, 业务依据, 复核状态
        FROM (SELECT *, STRFTIME(TRY_CAST(价格生效日期 AS TIMESTAMP), '%Y-%m-%d') AS norm_date
              FROM demo_savings.source_replacements)
    """).fetchall()
    data["替换关系"] = [{
        "替换单号": r[0], "替换类型": r[1], "适用采购主体": r[2], "原供应商": r[3], "新供应商": r[4],
        "原物料编码": r[5], "新物料编码": r[6], "原含税价": r[7], "现含税价": r[8],
        "价格生效日期": r[9], "原计价单位": r[10], "新计价单位": r[11], "可比单位": r[12],
        "原换算系数": r[13], "新换算系数": r[14], "业务依据": r[15], "复核状态": (r[16] or "").strip() or None,
    } for r in rows]

    rows = con.execute("""
        SELECT 年度 || '-' || 采购负责人, CAST(年度 AS VARCHAR), 采购负责人, 年度目标金额
        FROM demo_savings.targets
    """).fetchall()
    data["年度目标"] = [{
        "目标名称": r[0], "年度": r[1], "负责人姓名": r[2], "年度目标金额": fnum(r[3], 2),
    } for r in rows]

    rows = con.execute("""
        SELECT CAST(物料 AS VARCHAR) || '-' || 供应商 || '-' || 执行日期,
               CAST(采购组织 AS VARCHAR), 供应商, CAST(物料 AS VARCHAR), 五级分类,
               执行日期, 返利比例, 返利条款
        FROM demo_savings.source_rebates
    """).fetchall()
    data["返利反馈"] = [{
        "返利记录": r[0], "采购组织": r[1], "供应商": r[2], "物料": r[3], "五级分类": r[4],
        "执行日期": r[5], "返利比例": fnum(r[6], 6), "返利条款": r[7],
    } for r in rows]

    rows = con.execute("""
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
        GROUP BY 物料名称, 核算月份, 物料编码, 供应商名称, 原含税价, 现含税价, 核算类型, 新增存量
        ORDER BY 核算月份, 物料编码
    """).fetchall()
    data["降本初稿"] = [{
        "物料名称": r[0], "核算月份": r[1], "物料编码": r[2], "供应商名称": r[3],
        "公司编码": r[4], "公司名称": r[5], "五级分类": r[6], "核算类型": r[7], "降本来源": r[8],
        "原含税价": fnum(r[9], 4), "原计价单位": r[10], "现含税价": fnum(r[11], 4), "新计价单位": r[12],
        "实际入库数量": fnum(r[13], 2), "核算数量": fnum(r[14], 2), "核算单位": r[15],
        "单位降本": fnum(r[16], 4), "降本金额": fnum(r[17], 2), "核算开始": r[18], "核算结束": r[19],
        "新增存量": r[20], "明细笔数": r[21], "首次入库日期": r[22], "末次入库日期": r[23],
        "复核状态": "待复核",
    } for r in rows]
    data["_初稿_合计"] = round(sum(r["降本金额"] for r in data["降本初稿"]), 2)

    rows = con.execute("""
        SELECT CAST(物料编码 AS VARCHAR), 原因, 供应商名称, 采购单价, 数量,
               STRFTIME(TRY_CAST(出入库日期 AS TIMESTAMP), '%Y-%m-%d'),
               CAST(公司编码 AS VARCHAR), 公司名称, 五级分类, 采购负责人
        FROM demo_savings.exceptions ORDER BY 演示行号
    """).fetchall()
    data["待确认事项"] = [{
        "物料编码": r[0], "原因": r[1], "供应商名称": r[2], "采购单价": fnum(r[3], 4), "数量": r[4],
        "出入库日期": r[5], "公司编码": r[6], "公司名称": r[7], "五级分类": r[8],
        "负责人姓名": r[9], "处理状态": "待确认",
    } for r in rows]

    rows = con.execute("""
        SELECT 产品名称, 供应商编码, 供应商名称, CAST(物料编码 AS VARCHAR), 规格, 管理组织, 采购模式,
               旧协议编号, CAST(旧版本号 AS VARCHAR), 原税率,
               旧价格生效日期, 旧价格失效日期, 新协议编号, CAST(新版本号 AS VARCHAR), 新税率,
               新价格生效日期, 新价格失效日期
        FROM demo_savings.tax_changes
    """).fetchall()
    data["税率变化"] = [{
        "产品名称": r[0], "供应商编码": r[1], "供应商名称": r[2], "物料编码": r[3], "规格": r[4],
        "管理组织": r[5], "采购模式": r[6], "旧协议编号": r[7], "旧版本号": r[8], "原税率": fnum(r[9], 4),
        "旧价格生效日期": r[10], "旧价格失效日期": r[11], "新协议编号": r[12], "新版本号": r[13],
        "新税率": fnum(r[14], 4), "新价格生效日期": r[15], "新价格失效日期": r[16],
    } for r in rows]

    con.close()
    return data


# 用户字段永远不写入；写入时按目标表字段集过滤，避免带出未建字段。
USER_FIELDS = {"采购负责人", "复核人"}
WRITE_SKIP = USER_FIELDS | {"_初稿_合计"}


def phase_data():
    conf = json.loads(Path("outputs/savings_aitable_tables.json").read_text())
    base_id = conf["baseId"]
    table_ids = conf["tableIds"]
    data = load_data()

    ledger = json.loads(LEDGER_PATH.read_text()) if LEDGER_PATH.exists() else {}

    for name in ["替换关系", "年度目标", "返利反馈", "降本初稿", "待确认事项", "税率变化"]:
        records = data[name]
        tid = table_ids[name]
        got = dws(["field", "get", "--base-id", base_id, "--table-id", tid])
        fmap = {}
        for t in (got.get("tables") or [got]):
            for f in t.get("fields", []):
                fmap[f["fieldName"]] = f["fieldId"]

        done = set(ledger.get(name, []))
        batches = [records[i:i + BATCH_SIZE] for i in range(0, len(records), BATCH_SIZE)]
        print(f"[data] {name}: {len(records)} 行 / {len(batches)} 批，已完成 {len(done)} 批")
        for bi, batch in enumerate(batches):
            if bi in done:
                continue
            payload = []
            for row in batch:
                cells = {}
                for k, v in row.items():
                    if k in WRITE_SKIP or k not in fmap or v is None:
                        continue
                    cells[fmap[k]] = v
                payload.append({"cells": cells})
            dws(["record", "create", "--base-id", base_id, "--table-id", tid,
                 "--records", json.dumps(payload, ensure_ascii=False)], timeout=180)
            done.add(bi)
            ledger[name] = sorted(done)
            if not LEDGER_PATH.parent.exists():
                LEDGER_PATH.parent.mkdir(parents=True)
            LEDGER_PATH.write_text(json.dumps(ledger, ensure_ascii=False, indent=1))
            time.sleep(0.2)
        print(f"[data] {name} 写入完成")

    print(f"[done] 初稿降本金额合计(本地): {data['_初稿_合计']}")


# ---------------------------------------------------------------- verify
def phase_verify():
    conf = json.loads(Path("outputs/savings_aitable_tables.json").read_text())
    base_id = conf["baseId"]
    table_ids = conf["tableIds"]
    data = load_data()
    ok = True
    for name in TABLES:
        tid = table_ids[name]
        got = dws(["field", "get", "--base-id", base_id, "--table-id", tid])
        tables = got.get("tables") or [got]
        first_fid = tables[0]["fields"][0]["fieldId"]
        st = dws(["record", "stats", "--base-id", base_id, "--table-id", tid,
                  "--stats", json.dumps([{"fieldId": first_fid, "statsType": "COUNT"}])])
        raw = json.dumps(st, ensure_ascii=False)
        expect = len(data[name])
        # 提取统计值：返回结构可能是 [{"value": N}] 或 {"value": N}
        def find_count(obj):
            if isinstance(obj, dict):
                if "value" in obj:
                    return obj["value"]
                for v in obj.values():
                    r = find_count(v)
                    if r is not None:
                        return r
            elif isinstance(obj, list):
                for v in obj:
                    r = find_count(v)
                    if r is not None:
                        return r
            return None
        actual = find_count(st)
        flag = "OK" if actual == expect else "MISMATCH"
        if actual != expect:
            ok = False
        print(f"[verify] {name}: 表内 {actual} 行 / 期望 {expect} 行 [{flag}]")
    print(f"[verify] 降本初稿本地降本金额合计: {data['_初稿_合计']}（下一步与 psql 对账）")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    phase = sys.argv[1] if len(sys.argv) > 1 else "structure"
    {"structure": phase_structure, "data": phase_data, "verify": phase_verify}[phase]()
