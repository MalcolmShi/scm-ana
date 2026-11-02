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

# 本机看板：打开 http://127.0.0.1:8765
uv run python scripts/run_dashboard.py
# 端口被占用时可指定其他端口
uv run python scripts/run_dashboard.py --port 18765

# 离线测试
uv run python -m unittest discover -s tests -t tests
```

一个日期参数表示整年、整月或当天；两个参数表示闭区间。已建立的数据集在指定窗口无记录时返回保留字段的空 DataFrame；数据库或数据集未建立、processed 已知缺档或过期时明确报错：

```python
from read_data import read_raw_procurement_inbound, read_processed_procurement_inbound, read_outputs

raw = read_raw_procurement_inbound('2026-09')
processed = read_processed_procurement_inbound('2025-10', '2026-09')
report = read_outputs('procurement_increment', '2026-09-30')
monthly = read_outputs('procurement_increment', '2026-09-30', 'monthly')
```

首次执行接入时创建数据库；缺少整月数据时分析入口会停止。

## 方案和确认

- [采购增量分析方案](notebooks/采购增量分析/分析方案.md)
- [采购增量指标验证](notebooks/采购增量分析/采购增量分析.ipynb)
- [采购入库清洗确认](notebooks/数据清洗/采购入库清洗确认.ipynb)
- [数据分析 skill](.agents/skills/data-ana/SKILL.md)
