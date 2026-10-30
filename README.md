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
| 数据接入 | `docs/数据接入/` | `src/archive.py`、`src/sources/`、`src/read_data.py` |
| 降本识别 | `docs/降本识别/` | `src/analysis/` |
| 降本分析 | `docs/降本分析/` | 待实现 |
| 分派与反馈 | `docs/分派与反馈.md` | 待实现 |

```text
scm-ana/
├── README.md
├── pyproject.toml                 # 项目元数据、依赖与 hatchling 构建配置
├── uv.lock                        # 依赖锁定，uv sync 生成，进版本库
├── .python-version                # 固定 Python 3.12
├── .local-data/                   # 上游导出的入库明细 Excel（不进版本库）
├── notebooks/
│   ├── 采购增量分析.ipynb          # 探索、推演和中间结果核对
│   └── 数据源测试.ipynb            # 数据源核对：归档清单、字段、质量
├── src/
│   ├── paths.py                   # 项目路径与上海时区
│   ├── archive.py                 # 存储层：parquet 归档的路径规则与读写
│   ├── read_data.py               # 取数门面：read_inbound(start, end, columns)
│   ├── pipeline.py                # 流水线：analyze()，取数 → 分析 → 导出
│   ├── sources/                   # 数据源层：每个来源一个模块
│   │   ├── __init__.py
│   │   └── procurement_inbound.py  # 采购入库：口径 + 取数 + Excel 导入
│   ├── analysis/                  # 分析层：每个分析一个模块
│   │   ├── __init__.py
│   │   └── procurement_increment.py  # 采购增量分析
│   └── reporting/                 # 导出层
│       ├── __init__.py
│       └── excel.py               # Excel 报告
├── scripts/
│   ├── sync_data.py               # 导入入口：Excel 导出 → raw 日档
│   └── run_analysis.py            # 分析入口：跑分析、导报告
├── docs/
│   ├── 数据接入/采购入库.md         # 来源文件、归档规则与清洗方案
│   ├── 降本识别/采购增量分析.md
│   ├── 降本分析/采购降本自动化核算方案.md
│   ├── 降本分析/采购降本核算数据模型.md
│   └── 分派与反馈.md
├── tests/                         # 离线测试：test_archive / test_inbound
├── data/
│   ├── raw/                       # 来源文件原样归档，按来源和归档日存放
│   └── processed/                 # 业务侧清洗后的底表
├── outputs/                       # 生成的报告和看板
└── assets/                        # 文档图示
```

`src/` 下直接放 Python 模块，由 `uv sync` 以 editable 方式装进 `.venv`，脚本和 notebooks 直接 `import read_data`，
不修改 `sys.path`。新增分析从 notebook 开始，稳定计算再提取到 `src/`。业务流程用于解释工作，不再用于划分顶层目录。

## 按日归档

```text
data/raw/procurement-inbound/2026/2026-10/2026-10-25/data.parquet
data/processed/procurement-inbound/2026/2026-10/2026-10-25/data.parquet
```

路径统一为 `data/<层>/<来源>/<YYYY>/<YYYY-MM>/<YYYY-MM-DD>/data.parquet`，一天一个业务日期。
raw 是来源文件的原样转换：列名、取值和单位都不动，只追加 `_source` 和 `_fetched_at` 两列元数据。
清洗由业务侧单独处理，底表写入 `data/processed/`；分析优先读底表，没有底表时直接读 raw。

目录日期 = 记录里的「出入库日期」；节假日没有入库就没有文件。同一业务日重复归档原子替换当天文件。

## 快速开始

环境由 uv 管理：Python 版本固定在 `.python-version`（3.12），依赖锁在 `uv.lock`，`uv sync` 会建好 `.venv`
并以 editable 方式装上本项目的 `src/`。

```bash
uv sync          # 首次或 pyproject 变更后执行，含 dev 组（jupyterlab、ipykernel）

# 分析直接读 data/raw 的日档 parquet，无需上游文件
uv run python scripts/run_analysis.py
```

查看归档、只跑数不落文件：

```bash
uv run python scripts/sync_data.py --list
uv run python scripts/run_analysis.py --start-date 2025-10-01 --end-date 2026-09-30 --dry-run
```

取数就一句，返回 DataFrame。参数个数决定语义，参数精度决定粒度：

```python
from read_data import read_inbound

read_inbound("2026-09-30")          # 一天
read_inbound("2026-09")             # 一个月
read_inbound("2026")                # 一年
read_inbound("2026-01", "2026-09")  # 一个范围，两边都含
read_inbound(columns=["出入库日期", "数量"])   # 全部日期 + 列裁剪
```

核对字段和覆盖用 `notebooks/数据源测试.ipynb`。

`--start-date` / `--end-date` 选择取数窗口（写法和 `read_inbound` 一致），`--end-month` 选择业务结束月，
`--window-months` 决定分析窗口长度。报告写入 `outputs/reports/<业务结束日>/`，内容包含数据文件与转换时间。

使用 notebooks（`uv sync` 已装好 jupyterlab 和 ipykernel）：

```bash
uv run python -m ipykernel install --user --name scm-ana --display-name 'scm-ana'
uv run jupyter lab notebooks/数据源测试.ipynb
```

在 Jupyter 或 VS Code 中选择 `scm-ana` 内核（不要选其他环境的内核，否则导入不到 `read_data`）。离线验证：

```bash
uv run python -m unittest discover -s tests -t tests
```

## 文档

| 内容 | 位置 |
| --- | --- |
| 来源文件、归档规则、清洗方案、新增数据源 | [采购入库](docs/数据接入/采购入库.md) |
| 采购增量口径和报表字段 | [采购增量分析](docs/降本识别/采购增量分析.md) |
| 降本核算业务方案 | [采购降本自动化核算方案](docs/降本分析/采购降本自动化核算方案.md) |
| 核算数据模型建议 | [采购降本核算数据模型](docs/降本分析/采购降本核算数据模型.md) |
| 分派反馈规划 | [分派与反馈](docs/分派与反馈.md) |
