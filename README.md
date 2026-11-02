# 供应链降本管理

面向采购降本管理的数据分析项目：围绕采购降本的全生命周期流程，从**数据获取**、**识别降本机会**、**降本执行与追踪跟进**，到**降本核算与统计**，形成可持续运转的业务闭环。

## 全生命周期流程

![采购降本闭环流程](assets/flow.svg)

| 环节 | 内容 | 状态 |
| --- | --- | --- |
| ① 数据获取 | 读取上游导出的采购入库文件，原样归档 | 已实现 |
| ② 识别机会 | 采购增量分析，识别采购量增长信号，产出谈判候选清单 | 新口径验证中，旧脚本待调整 |
| ③ 分派执行 | 谈判候选分派给采购员，跟进执行与反馈 | 业务方案（见 [分派与反馈](分派与反馈.md)） |
| ④ 降本统计 | 基于事实核算实际降本，输出统计报告和看板 | 方案已确认（见 [采购降本自动化核算方案](notebooks/采购降本统计/采购降本自动化核算方案.md)） |

## 分析场景

**场景是本项目的一等单元**：一个场景 = 一个可复跑的分析产品，方案确认与研究验证在 `notebooks/<场景>/`，稳定代码在 `src/`，入口在 `scripts/`。每个场景一份 `分析方案.md` 从第一步写到第五步（五节追加式，见 [模板](.agents/skills/data-ana/templates/分析方案.md)；存量方案文档下次动工时并入）。

| 场景 | 状态 | 入口 | 交付物 |
| --- | --- | --- | --- |
| [采购增量分析](notebooks/采购增量分析/分析方案.md) | 新口径验证中 | `notebooks/采购增量分析/采购增量分析.ipynb` | 月度候选报表及归档方案待确认 |
| [采购降本统计](notebooks/采购降本统计/采购降本自动化核算方案.md) | 方案已确认 | 待落地（DDL + 核算脚本） | 月度降本统计报表（六类核算口径） |

`notebooks/数据清洗/` 是数据源级共享工作区（不属于任何场景）：接入方案与清洗研究放这里，产出被所有场景复用。

新增分析场景：按 [分析方案模板](.agents/skills/data-ana/templates/分析方案.md) 建文档，从第一步（业务理解与指标构建）开始；第 0~5 步方法见 [data-ana SKILL.md](.agents/skills/data-ana/SKILL.md)。

## 目录组织

```text
scm-ana/
├── README.md
├── pyproject.toml                 # 项目元数据、依赖与 hatchling 构建配置
├── uv.lock / .python-version      # 依赖锁定（进版本库）/ 固定 Python 3.12
├── .local-data/                   # 上游导出的入库明细 Excel（不进版本库）
├── notebooks/                     # 按场景组织：方案确认 + 研究验证
│   ├── 采购增量分析/               #   方案 md + 验证 notebook
│   ├── 采购降本统计/               #   核算方案 md + 数据模型 md
│   └── 数据清洗/                  #   数据源级共享：接入方案 + 清洗研究 notebook
├── .agents/skills/data-ana/       # data-ana 技能（ZCode 自动发现；第 0~5 步）
│   ├── SKILL.md                   #   进入方式 + 三条铁律 + 三层数据 + 步骤路由
│   ├── references/                #   各步方法：00~04（第五步看板说明在 SKILL.md 内）
│   └── templates/                 #   分析方案 / 数据清洗模版 / 指标验证模版
├── .agents/skills/visualize/      # visualize 技能（上游 MIT，内置；出单文件静态 HTML 看板）
├── src/                           # 代码分层：与场景解耦的引擎
│   ├── paths.py                   # 项目路径与上海时区
│   ├── archive.py                 # 存储层：parquet 归档的路径规则与读写（raw/processed/outputs 三层）
│   ├── read_data.py               # 显式读取 raw、processed 和 outputs
│   ├── pipeline.py                # 流水线：analyze()，取数 → 分析 → 导出
│   ├── sources/procurement_inbound.py  # 数据源层：口径 + 取数 + Excel 导入（一来源一模块）
│   ├── analysis/procurement_increment.py  # 分析层：一场景一模块（Config + run_analysis + export）
│   ├── reporting/excel.py         # 导出层：Excel 报告
│   └── reporting/metrics.py       # 导出层：指标结果归档 metrics + json（data/outputs 落两份）
├── scripts/
│   ├── sync_data.py               # 导入入口：Excel 导出 → raw 日档
│   └── run_analysis.py            # 分析入口：跑增量分析、导报告
├── tests/                         # 离线测试：test_archive / test_metrics / test_inbound / test_increment
├── data/
│   ├── raw/                       # 来源文件原样归档，按来源和业务日存放
│   ├── processed/                 # 清洗底表（清洗规则确认后由第三步脚本写入）
│   └── outputs/                   # 指标结果归档：每张表 data.parquet + data.json（看板取数）
├── outputs/                       # 交付物（不进 data/）
│   ├── reports/<业务结束日>/       #   报表、Excel 报告
│   └── dashboard/<场景>/           #   单文件静态看板 <业务结束日>.html，双击打开，不部署
└── assets/                        # 文档图示
```

`src/` 下直接放 Python 模块，由 `uv sync` 以 editable 方式装进 `.venv`，脚本和 notebooks 直接 `import read_data`，不修改 `sys.path`。新增分析从 notebook 开始，业务确认后提取到 `src/`。

## 按日归档

```text
data/raw/procurement-inbound/2026/2026-10/2026-10-25/data.parquet
data/processed/procurement-inbound/2026/2026-10/2026-10-25/data.parquet
data/outputs/采购增量分析/2026/2026-09/2026-09-30/data.parquet
data/outputs/采购增量分析/2026/2026-09/2026-09-30/data.json
```

路径统一为 `data/<层>/<名称>/<YYYY>/<YYYY-MM>/<YYYY-MM-DD>/data.parquet`，一天一个业务日期。
来源 id（raw / processed）用小写连字符；outputs 层的 `<名称>` 是**场景名**，与 `notebooks/<场景>/` 同名。
当前 `reporting.metrics.archive_metrics()` 将主表写为 `data.parquet`，主表和其他表写入 `data.json`；
多表 parquet 与批次归档尚未实现。
raw 是来源文件的原样转换：列名、取值和单位都不动，只追加 `_source` 和 `_fetched_at` 两列元数据。
清洗后的底表写入 `data/processed/`。新的读取接口显式指定层，不自动回退；旧版 `read_inbound()`
仍保留逐日优先 processed、缺失时回退 raw 的行为，仅供旧代码兼容。

目录日期 = 记录里的「出入库日期」；节假日没有入库就没有文件。同一业务日重复归档原子替换当天文件。

## 快速开始

环境由 uv 管理：Python 版本固定在 `.python-version`（3.12），依赖锁在 `uv.lock`，`uv sync` 会建好 `.venv`
并以 editable 方式装上本项目的 `src/`。

```bash
uv sync          # 首次或 pyproject 变更后执行，含 dev 组（jupyterlab、ipykernel）

# 当前已确认的含税口径在指标验证 notebook 中；旧分析脚本尚未与该口径对齐
uv run jupyter lab notebooks/采购增量分析/采购增量分析.ipynb
```

查看已有归档：

```bash
uv run python scripts/sync_data.py --list
```

显式读取各层，普通接口返回 DataFrame：

```python
from read_data import read_raw_procurement_inbound, read_processed_procurement_inbound, read_outputs

raw = read_raw_procurement_inbound("2026-09")
processed = read_processed_procurement_inbound("2025-10", "2026-09")
report = read_outputs("采购增量分析", "2026-09-30")  # 已存在的主结果表
```

一个日期参数表示整年、整月或当天；两个参数表示闭区间。采购入库 processed 读取会检查已知 raw 日档
是否缺少对应 processed；业务覆盖和输入版本一致性尚未核实，可用对应 `_meta()` 接口查看说明。
`read_outputs` 当前只支持指定业务日的主表，尚不支持指定结果表或历史批次。

使用 notebooks（`uv sync` 已装好 jupyterlab 和 ipykernel）：

```bash
uv run python -m ipykernel install --user --name scm-ana --display-name 'scm-ana'
uv run jupyter lab notebooks/采购增量分析/采购增量分析.ipynb
```

在 Jupyter 或 VS Code 中选择 `scm-ana` 内核（不要选其他环境的内核，否则导入不到 `read_data`）。离线验证：

```bash
uv run python -m unittest discover -s tests -t tests
```

## 文档

| 内容 | 位置 |
| --- | --- |
| 采购增量分析：口径与报表字段 / 研究验证 | [方案](notebooks/采购增量分析/采购增量分析.md) · [验证 notebook](notebooks/采购增量分析/采购增量分析.ipynb) |
| 采购降本统计：核算方案 / 数据模型 | [核算方案](notebooks/采购降本统计/采购降本自动化核算方案.md) · [数据模型](notebooks/采购降本统计/采购降本核算数据模型.md) |
| 数据接入与清洗方案（数据源级，含新增来源指引） | [采购入库接入及清洗](notebooks/数据清洗/采购入库接入及清洗.md) · [清洗研究](notebooks/数据清洗/采购入库数据清洗.ipynb) |
| 数据分析技能方法论（第 0~5 步 + 三条铁律 + 三层数据） | [data-ana SKILL.md](.agents/skills/data-ana/SKILL.md) |
| 看板技术侧（版式、图表、主题、导出） | [visualize 技能](.agents/skills/visualize/SKILL.md) |
| 技能设计思路调研 | [数据分析技能设计思路调研](数据分析技能设计思路调研.md) |
| 分派反馈规划 | [分派与反馈](分派与反馈.md) |
