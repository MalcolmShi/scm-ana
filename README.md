# 供应链数据分析

面向采购降本管理的数据分析项目：围绕采购降本的全生命周期流程，从**数据获取**、**识别降本机会**、**降本执行与追踪跟进**，到**降本核算与统计**，形成可持续运转的业务闭环。

## 全生命周期流程

![采购降本闭环流程](assets/flow.svg)

| 环节 | 内容 | 状态 |
| --- | --- | --- |
| ① 数据获取 | 从数据中台接口取采购入库数据，清洗后按日归档 | 已实现 |
| ② 识别机会 | 采购增量分析，识别采购量增长信号，产出谈判候选清单 | 已实现 |
| ③ 分派执行 | 谈判候选分派给采购员，跟进执行与反馈 | 业务方案（见 `docs/`） |
| ④ 降本统计 | 基于事实核算实际降本，输出统计报告和看板 | 业务方案（见 `docs/`） |

当前已实现①②：采购入库取数、清洗、采购增量分析和报告导出。③④对应程序尚未实现，业务方案文档保存在 `docs/`。

## 文件组织

```text
scm-ana/
├── README.md
├── pyproject.toml                 # 依赖及本地模块安装
├── .env.example                   # 本地凭证模板
├── notebooks/
│   └── 采购增量分析.ipynb          # 探索、推演和中间结果核对
├── src/
│   ├── connectors/                # 按来源组织接口调用
│   │   ├── procurement_inbound.py
│   │   └── library_parquet.py      # 备用资料库下载
│   ├── cleaning/
│   │   └── procurement_inbound.py  # 入库字段、类型、单位和年月转换
│   ├── data.py                    # 通用的按日归档读写
│   ├── pipeline.py                # 来源选择、取数、清洗和归档
│   ├── procurement.py             # 稳定的采购增量计算
│   ├── reporting/                 # Excel 和 HTML 导出
│   ├── envfile.py                 # 凭证载入
│   └── paths.py                   # 项目路径与上海时区
├── scripts/                       # 命令行入口
│   ├── sync_data.py
│   ├── peek_api.py
│   └── procurement_analysis.py
├── docs/                          # 接口、数据约定、分析口径和业务方案
├── tests/                         # 离线测试
├── data/
│   ├── raw/                       # 接口原始记录，按来源和抓取日归档
│   └── processed/                 # 同日归档的清洗底表
├── outputs/                       # 生成的报告和看板
└── assets/                        # 文档图示
```

`src/` 下直接放 Python 模块，通过本地安装导入；脚本和 notebooks 都复用同一份代码，不修改 `sys.path`。
新增分析从 notebook 开始，稳定计算再提取到 `src/`。业务流程用于解释工作，不再用于划分顶层目录。

## 按日归档

```text
data/raw/procurement-inbound/2026/2026-10/2026-10-25/data.parquet
data/processed/procurement-inbound/2026/2026-10/2026-10-25/data.parquet
```

日期表示上海时区的**抓取日**。每天保存一版完整快照，不跨日覆盖；同一天重复同步更新当天文件。
raw 保留接口字段和金额单位，processed 做清洗。两层均带 `_source` 和 `_fetched_at`。

分析默认读取最新一版，可指定历史归档日。每天的全量快照不会合并累加。
数据业务覆盖范围由记录里的日期计算，与抓取日分别展示。

## 快速开始

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .

cp .env.example .env  # 已有 .env 时跳过
chmod 600 .env
# 填写接口凭证后执行
python scripts/sync_data.py --source procurement-inbound
python scripts/procurement_analysis.py
```

查看归档、读取历史版本：

```bash
python scripts/sync_data.py --list
python scripts/procurement_analysis.py --snapshot-date 2026-10-25 --dry-run
```

`--snapshot-date` 选择数据版本；`--end-month` 选择业务分析结束月，两个参数含义不同。
报告写入 `outputs/reports/<抓取日>/`，内容包含使用的归档路径和抓取时间。

使用 notebooks：

```bash
python -m pip install -e '.[notebooks]'
python -m ipykernel install --user --name scm-ana --display-name 'scm-ana'
python -m jupyterlab
```

在 Jupyter 中选择 `scm-ana` 内核。离线验证：

```bash
python -m unittest discover -s tests -v
python -m connectors.procurement_inbound --self-test
```

## 文档

| 内容 | 位置 |
| --- | --- |
| 归档规则、取数、读取和新增来源 | [数据接入](docs/数据接入.md) |
| 入库接口参数、认证和字段 | [入库记录接口](docs/入库记录接口.md) |
| 采购增量口径和报表字段 | [采购增量分析](docs/采购增量分析.md) |
| 降本核算业务方案 | [采购降本自动化核算方案](docs/采购降本自动化核算方案.md) |
| 核算数据模型建议 | [采购降本核算数据模型](docs/采购降本核算数据模型.md) |
| 分派反馈规划 | [分派与反馈](docs/分派与反馈.md) |
