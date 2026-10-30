# 供应链降本管理

面向采购降本管理的数据分析项目：围绕采购降本的全生命周期流程，从**数据获取**、**识别降本机会**、**降本执行与追踪跟进**，到**降本核算与统计**，形成可持续运转的业务闭环。

## 全生命周期流程

![采购降本闭环流程](assets/flow.svg)

| 环节 | 内容 | 状态 |
| --- | --- | --- |
| ① 数据获取 | 读取上游导出的采购入库文件，原样归档 | 已实现 |
| ② 识别机会 | 采购增量分析，识别采购量增长信号，产出谈判候选清单 | 已实现 |
| ③ 分派执行 | 谈判候选分派给采购员，跟进执行与反馈 | 业务方案（见 `docs/`） |
| ④ 降本统计 | 基于事实核算实际降本，输出统计报告和看板 | 业务方案（见 `docs/`） |

当前已实现①②：采购入库取数、归档、采购增量分析和报告导出。③④对应程序尚未实现，业务方案文档保存在 `docs/`。

## 目录组织

代码按业务环节分层，与 `docs/` 的目录一一对应：

| 业务环节 | 文档 | 代码 |
| --- | --- | --- |
| 数据接入 | `docs/数据接入/` | `src/sources/`、`src/pipeline.py`、`src/archive.py` |
| 降本识别 | `docs/降本识别/` | `src/analysis/` |
| 降本分析 | `docs/降本分析/` | 待实现 |
| 分派与反馈 | `docs/分派与反馈.md` | 待实现 |

```text
scm-ana/
├── README.md
├── pyproject.toml                 # 依赖及本地模块安装
├── .local-data/                   # 上游导出的入库明细 Excel（不进版本库）
├── notebooks/
│   └── 采购增量分析.ipynb          # 探索、推演和中间结果核对
├── src/
│   ├── paths.py                   # 项目路径与上海时区
│   ├── archive.py                 # 归档读写：路径规则与原子写
│   ├── pipeline.py                # 数据接入编排：取数 → 归档 → 读取
│   ├── sources/                   # 数据接入层：每个数据源一个模块
│   │   ├── __init__.py            # 来源注册表
│   │   ├── procurement_inbound.py  # 采购入库来源口径
│   │   ├── local_excel.py          # 本地 Excel 读取器
│   │   └── library_parquet.py      # 备用资料库下载
│   ├── analysis/                  # 降本识别层：每个识别分析一个模块
│   │   ├── __init__.py            # 分析注册表
│   │   └── procurement_increment.py  # 采购增量分析
│   └── reporting/                 # Excel 和 HTML 导出
├── scripts/
│   ├── sync_data.py               # 数据接入入口：归档
│   └── run_analysis.py            # 降本识别入口：跑分析、导报告
├── docs/
│   ├── 数据接入/采购入库.md         # 来源文件、归档规则与清洗方案
│   ├── 降本识别/采购增量分析.md
│   ├── 降本分析/采购降本自动化核算方案.md
│   ├── 降本分析/采购降本核算数据模型.md
│   └── 分派与反馈.md
├── tests/                         # 离线测试
├── data/
│   ├── raw/                       # 来源文件原样归档，按来源和归档日存放
│   └── processed/                 # 业务侧清洗后的底表
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

路径统一为 `data/<层>/<来源>/<YYYY>/<YYYY-MM>/<YYYY-MM-DD>/data.parquet`，对所有来源通用。
raw 是来源文件的原样转换：列名、取值和单位都不动，只追加 `_source` 和 `_fetched_at` 两列元数据。
清洗由业务侧单独处理，底表写入 `data/processed/`；分析优先读底表，没有底表时直接读 raw。

目录日期默认是转换当天（`--mode business-date` 时为业务日期）。每天保存一版完整快照，不跨日覆盖。
分析默认读取最新一版，可指定历史归档日；每天的全量快照不会合并累加。

## 快速开始

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .

# 把上游导出的入库明细 Excel 放进 .local-data/ 后执行
python scripts/sync_data.py --source procurement-inbound
python scripts/run_analysis.py --analysis procurement-increment
```

查看归档、读取历史版本：

```bash
python scripts/sync_data.py --list
python scripts/run_analysis.py --snapshot-date 2026-10-25 --dry-run
```

`--snapshot-date` 选择数据版本；`--end-month` 选择业务分析结束月，两个参数含义不同。
报告写入 `outputs/reports/<归档日>/`，内容包含使用的归档路径和转换时间。

使用 notebooks：

```bash
python -m pip install -e '.[notebooks]'
python -m ipykernel install --user --name scm-ana --display-name 'scm-ana'
python -m jupyterlab
```

在 Jupyter 中选择 `scm-ana` 内核。离线验证：

```bash
python -m unittest discover -s tests -v
python -m sources.local_excel --read
```

## 文档

| 内容 | 位置 |
| --- | --- |
| 来源文件、归档规则、清洗方案、新增数据源 | [采购入库](docs/数据接入/采购入库.md) |
| 采购增量口径和报表字段 | [采购增量分析](docs/降本识别/采购增量分析.md) |
| 降本核算业务方案 | [采购降本自动化核算方案](docs/降本分析/采购降本自动化核算方案.md) |
| 核算数据模型建议 | [采购降本核算数据模型](docs/降本分析/采购降本核算数据模型.md) |
| 分派反馈规划 | [分派与反馈](docs/分派与反馈.md) |
