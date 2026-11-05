# 供应链数据分析

本项目把采购数据接入、清洗和分析结果保存在本地 DuckDB，并通过 notebook 确认口径、本地服务查看结果与逐笔明细。

## 数据分层

`data/scm.duckdb`：

| 层 | 表 | 说明 |
| --- | --- | --- |
| raw | `raw.procurement_inbound` | 采购入库原始明细，保留源字段 |
| processed | `processed.procurement_inbound` | 清洗后的共享明细 |
| outputs | `outputs.procurement_increment__detail` | 采购增量物料报表，按业务结束月保存 |
| outputs | `outputs.procurement_increment__monthly` | 物料月度数量及含税金额 |
| meta | `meta.partitions`、`meta.output_periods` | 业务日期、数据指纹、归档日期和参数 |

数据库包含业务数据，不提交 Git。

## 代码边界

| 位置 | 作用 |
| --- | --- |
| `src/storage.py` | 通用 DuckDB 表与分区读写、结果事务；不依赖具体来源或场景 |
| `src/read_data.py`、`src/write_data.py` | 明确的 raw、processed、outputs 读写入口 |
| `src/sources/`、`src/clean/` | 数据源接入与各数据集清洗 |
| `src/analysis/` | 场景分析，只接收数据并返回结果 |
| `src/services/`、`src/web/` | 场景查询、下钻及本地页面 |
| `scripts/` | 导入、清洗、分析与服务入口 |

采购入库的接入、清洗和分析分别由独立模块负责，统一写入 DuckDB。新增数据源只补自己的接入、清洗和命名读写入口；新增分析补自己的分析、结果及查询代码，复用通用存储。

## 使用

```bash
uv sync
# 从 .local-data 的采购入库来源依次生成 raw、processed 和月度分析结果
uv run python scripts/run_procurement_pipeline.py 2026-09

# 也可分步执行
uv run python scripts/sync_data.py
uv run python scripts/build_processed.py --start 2026-09 --dry-run
uv run python scripts/build_processed.py

# 默认计算上一个自然月；也可以指定历史月份
uv run python scripts/archive_procurement_increment.py
uv run python scripts/archive_procurement_increment.py 2026-09

# 采购降本模拟：先将 notebook 生成的快照及模拟输入导入独立 DuckDB
# 重新导入会覆盖演示库中的临时修改
uv run python scripts/import_savings_demo.py

# 本机看板：采购增量 http://127.0.0.1:8765
# 采购降本模拟演示 http://127.0.0.1:8765/savings
uv run python scripts/run_dashboard.py
# 端口被占用时可指定其他端口
uv run python scripts/run_dashboard.py --port 18765

# 发布/只读演示：构建精简库（去掉 raw，93.9 MB → 32.8 MB），再用环境变量指向它
uv run python scripts/build_publish_db.py
SCM_DB_PATH=outputs/publish/scm.duckdb PORT=8080 uv run python scripts/run_dashboard.py

# 生成可分享的概览页：单文件 HTML（数据内联）+ 每月 CSV，输出到 outputs/share
uv run python scripts/build_share_dashboard.py

# 离线测试
uv run python -m unittest discover -s tests -t tests

# 导出钉钉派发任务：默认最新归档周期的优先谈判物料
uv run python scripts/export_dispatch_tasks.py
uv run python scripts/export_dispatch_tasks.py 2026-09 --priority all
```

采购降本页面展示虚构数据，与采购增量共用本地服务。页面从独立的 `data/procurement_savings_demo.duckdb` 读取，数据库中 `demo_savings` 下保存模拟输入和核算结果；更新结果后点击页面的“刷新数据”即可重新读取。修改原始模拟输入并不会自动重新核算：须完整运行[模拟演示 notebook](notebooks/采购降本统计/采购降本模拟演示.ipynb) 生成快照，再运行导入命令；重新导入会覆盖演示库中的临时修改。演示库不写入正式业务数据库。

`outputs/share` 是分享用的概览页：复用本机看板的页面版式，单文件 HTML 内联全部已归档周期与月度趋势，支持切月、搜索、指标筛选、分页、趋势图和导出 CSV，可直接上传资料库或放到任意静态托管；逐笔入库明细需要服务端查询，分享页不包含，页面内已注明。

一个日期参数表示整年、整月或当天；两个参数表示闭区间。已建立的数据集在指定窗口无记录时返回保留字段的空 DataFrame；数据库或数据集未建立、processed 已知缺档或过期时明确报错：

```python
from read_data import read_raw_procurement_inbound, read_processed_procurement_inbound, read_outputs

raw = read_raw_procurement_inbound('2026-09')
processed = read_processed_procurement_inbound('2025-10', '2026-09')
report = read_outputs('procurement_increment', '2026-09-30')
monthly = read_outputs('procurement_increment', '2026-09-30', 'monthly')
```

首次执行接入时创建数据库；缺少整月数据时分析入口会停止。

## 两个版本

对外查看和本地调试分开走，互不影响。分享版没有另写页面：两个场景的文档整份来自 `src/web` 下的源页面，构建时内嵌进同一个文件。

| 版本 | 用途 | 入口 | 数据范围 |
| --- | --- | --- | --- |
| 本地版 | 测试、调样式、核对口径 | `uv run python scripts/run_dashboard.py` → `/` 采购增量、`/savings` 采购降本 | 读 DuckDB，含逐笔入库明细 |
| 分享版 | 发给别人看，只读 | <https://workbuddy.link/p/8ROEouucp0FHCZBDngtL15> | 单文件内联全部数据，不含逐笔明细 |

分享版是**一个页面两个场景**：页头的「采购增量 / 采购降本」在页面内切换，和本地服务的导航一致。构建产物与资料库结构对应：

```
outputs/share/供应链分析.html      → 空间根下的「供应链分析」页面
outputs/share/采购增量/*.csv       → 「采购增量」文件夹，按月 10 张
outputs/share/采购降本/*.csv       → 「采购降本」文件夹，按模拟库真实表 9 张
```

本地版随算随改，刷新即生效；分享版是构建产物，只有重新构建并重新导入才会变。更新流程：

```bash
# 1. 重建分享产物（一个页面 + 两个数据目录）
uv run python scripts/build_share_dashboard.py

# 2. 覆盖导入：页面
LIB="/Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/resources/plugins/workbuddy-builtin/skills/library"
SPACE=TSTg1P1GzqTaGCAzB4Fuyw   # 供应链数据分析

python3 "$LIB/page/import_html.py" outputs/share/供应链分析.html \
  --space-id $SPACE --file-name 供应链分析.html \
  --node-block-id 8ROEouucp0FHCZBDngtL15

# 3. 覆盖导入：数据表（--parent-id 指文件夹，--database-id 指该表节点）
python3 "$LIB/database/import_csv.py" outputs/share/采购增量/采购增量-2026-10.csv \
  --space-id $SPACE --parent-id yYlhbruX6uaPAvFH56FPkp --database-id U2ShUksLKIuFlnVMwUK5Lo

python3 "$LIB/database/import_csv.py" outputs/share/采购降本/receipts.csv \
  --space-id $SPACE --parent-id ZWEukloUkJab7egtalEfwR --database-id 1jNsBVPFCl5zRQLJ7Dxspo

# 4. 更新发布（链接不变）
python3 "$LIB/page/publish_page.py" --node-id 8ROEouucp0FHCZBDngtL15
```

**节点 id 对照**（覆盖导入必须带，否则会新建出重复节点）：

```
采购增量 文件夹 yYlhbruX6uaPAvFH56FPkp     采购降本 文件夹 ZWEukloUkJab7egtalEfwR
2026-01 vH0qJ0ZBCiZpS9DetnHVfO             candidates  iLNbxR3fnKKhAOyuvw2kwP
2026-02 WnoJYSknRDJAdPntcAivvT             detail      fM4C9z743j14R5AZAtYmmK
2026-03 vxJlhYqqUwdWZnvaeI5TMS             exceptions  BbIsR4JykBNtIbaYCCuNna
2026-04 DFCJo6tAOHetBqRMNfWpCS             protocols   K3poatJycFHfiVczOwvk2Q
2026-05 o6N6G72AphnqkvKg33hhkT             rebates     wh7kjFOp08CZ8gVHDplmJQ
2026-06 r0usBHpInqd1f8PpW6MmEQ             receipts    1jNsBVPFCl5zRQLJ7Dxspo
2026-07 DPBzaSEklosj8Y6odxwkNt             relations   5zdY4ikXXkcmAGCbVo0MSN
2026-08 GJkOhexDLJ88fpZxDxYH7z             targets     ikd9tlqzC4at4TNwhljR7j
2026-09 qtOlSDf9sHV4DifUk8mhXk             tax_changes O41i3rHTCQhRVpQLCQf587
2026-10 U2ShUksLKIuFlnVMwUK5Lo
```

要暂停对外访问，用 `page/unpublish_page.py --node-id 8ROEouucp0FHCZBDngtL15` 下线。

## 方案和确认

- [采购增量分析方案](notebooks/采购增量分析/分析方案.md)
- [采购增量指标验证](notebooks/采购增量分析/采购增量分析.ipynb)
- [采购入库清洗确认](notebooks/数据清洗/采购入库清洗确认.ipynb)
- [数据分析 skill](.agents/skills/data-ana/SKILL.md)
- [采购业务多维表同步 skill](.agents/skills/aitable-sync/SKILL.md)：分别处理采购增量任务派发和采购降本填报、复核；降本目标表及同步流程尚待确认与实现。
