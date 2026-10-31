# 场景化编排 + data-ana 通用技能重建方案

## 设计原则

1. **场景是一等单元**。一个场景 = 一个可复跑的分析产品（现有：采购增量分析、采购降本统计）。场景资产各有固定去处：方案与验证 → `notebooks/<场景>/`；实例知识 → 场景档案.md；代码 → `src/analysis` + `src/reporting`（本次不动）；入口 → `scripts/`（本次不动）。
2. **skill 放方法，仓库放实例**。`skills/data-ana/` 只含通用方法论（六阶段 + 门禁 + 模板 + 硬校验），不含采购专属知识；每个场景的知识在 `notebooks/<场景>/场景档案.md`，skill 靠发现档案工作。新增场景 = 建档案 + 走流程，skill 零修改。
3. **调研文档的六个模式直接落进 skill**：证据分级（"待确认"是一等公民）、反向指标必配、口径四件套（分子/分母+统计周期+数据来源+负责人）、硬校验 exit 1、渐进披露（SKILL.md 只放路由）、降级必须留痕。

## 一、目录编排

```
notebooks/
├── 数据清洗/                        # 数据源级共享工作区（采购入库），不动
├── 采购增量分析/                    # 改名自「采购增量降本识别」
│   ├── 场景档案.md                  # 新增：场景入口（见下）
│   ├── 采购增量分析.md              # 方案（已有）
│   └── 采购增量分析.ipynb           # 首格补契约头 + 修失效链接
└── 采购降本统计/                    # 改名自「降本分析统计」
    ├── 场景档案.md                  # 新增
    ├── 采购降本核算数据模型.md       # 已有
    └── 采购降本自动化核算方案.md     # 已有

skills/data-ana/                     # 重建（现在是空壳）
├── SKILL.md
├── references/  01-意图识别 ~ 06-可视化交付（六篇）
├── templates/   场景档案 / 意图确认书 / notebook契约头 / 口径字典.schema.json / 断言清单
└── scripts/check_scenario.py        # 硬校验，失败 exit 1
```

**场景档案.md**（模板落 `templates/`，两个场景各填一份）：场景ID/场景名/状态（研究中|方案已确认|已落地）/一句话意图/业务负责人/数据源/口径正本位置/关键文档链接/验证 notebook 与验证点清单/落地入口/交付物/下一步。头部用 `场景ID: xxx` 行式写法供脚本解析。采购增量分析=已落地（落地入口 `scripts/run_analysis.py`，口径正本暂注明在 `reporting/excel.py` 的 RULE_ROWS，迁移到口径字典是 05 阶段既定待办）；采购降本统计=方案已确认（下一步：DDL + 核算脚本 + 场景注册）。

**契约头**：按你调研文档的落点2，往两个 notebook 首格 markdown 顶部插入五节（本 notebook 回答什么问题 / 口径 key / 输入取数范围 / 预期验证点 3-5 条打勾 / 结论提取到 src 哪个模块）。用脚本改 ipynb JSON，改完 `nbformat.validate` 校验。清洗 notebook 的结论去向写 `src/clean`（待落地）。

**修失效链接**：两个 notebook 里指向 `../docs/...` 的链接改为同目录 md；根目录 `分派与反馈.md` 改指 `notebooks/采购降本统计/...`、`notebooks/数据清洗/...`；`reporting/excel.py`、`scripts/run_analysis.py` 的 docstring 路径同步修正（仅注释，无逻辑改动）。

**README 重写**：场景表（两场景 + 状态 + 入口）、目录映射（notebooks 按场景/数据源、src 分层、scripts 入口、skills 说明）、保留 uv 工作流与取数语义；说明 skill 正本在仓库、未挂载，用法是让 agent 读 `skills/data-ana/SKILL.md`。

## 二、skills/data-ana 内容

**SKILL.md（只放路由，~150 行）**：适用时机（新增场景/继续既有场景/口径或数据变更后重验证）；第一步永远是 `ls notebooks/*/场景档案.md` 发现场景、读档案、定位阶段；六阶段门禁表（阶段 | 产物 | 通过条件）；六条红线（六模式）；资源索引表（哪个 reference 何时加载、何时禁读）；scm-ana 约定速查（产物去向表 + uv 命令）。

**references/ 六篇**（每篇开头写「何时读/何时不要读」，内容从调研报告的逐环节手法 + scm-ana 现状约定合成）：

- `01-意图识别`：封闭提问一次一问；意图确认书字段（业务问题/阶段/本季度首要目标/决策用途与频率/负责人）；四要素不齐不进建模（CHECKPOINT）。
- `02-数据建模`：从指标反推字段而非从表反推；口径字典 schema 与指标/规则两类条目；反向指标设计；缺数据降级表（停止/降为假设/索要）；数据模型文档写法（以 采购降本核算数据模型.md 为范本）。
- `03-取数与清洗`：预检清单（表头行探测/表尾小表/合并表头、\r\n 与 _x000d_ 教训）；七项清洗规则表 + 不改原值铁律；全列方差画像表；scm-ana 约定（raw/processed 双层归档、read_data 门面、清洗研究落 notebooks/数据清洗/）。
- `04-研究验证`：契约头五节；断言清单（边界陷阱逐条 assert）；独立复算法（不复用生产函数、从明细重算逐键比对，对拍 scripts 结论）；换数据重跑清单；以 采购增量分析.ipynb 六节结构为范本。
- `05-稳定脚本`：沉淀判定（规则稳定 + 断言全绿才进 src）；分层去向表（sources/clean/analysis/reporting/pipeline/scripts 各收什么）；场景注册步骤——如实记录现状四处硬编码点（read_data 门面、pipeline.analyze、run_analysis.py、RULE_ROWS）为待办改造清单，新场景落地时执行；测试要求（离线 unittest + 合成数据）。
- `06-可视化交付`：数据形状→图型决策树；每选型红线（瀑布步序按业务因果、散点写"相关不等于因果"等）；什么时候退回表格；openpyxl 现实约束下的选型；交付自检（图数一致/单位/图注）。

**templates/**：场景档案.md、意图确认书.md、notebook契约头.md（可直接粘贴首格）、口径字典.schema.json（指标条目强制四件套+负责人+反向指标，允许显式"待确认"）、断言清单.md。

**scripts/check_scenario.py**（纯 stdlib）：默认扫 `notebooks/*/场景档案.md`；校验必填字段、声明文件存在、已落地场景的入口脚本与 src 模块存在；若目录有 口径字典.json 则按 schema 硬校验（指标四件套缺一即 FAIL）；逐项 PASS/FAIL，任何 FAIL exit 1。

## 三、本次不做（记入档案/05 待办）

- 代码注册表改造（scenarios.py、--scenario/--source、RULE_ROWS 迁 JSON）——你已选后续再做；
- 采购降本统计的核算代码与 DDL；
- src/clean 清洗规则落地（等清洗 notebook 断言全绿）；
- skill 软链挂载（正本仅在仓库，随 git 提交）。

## 四、验证

1. `uv run python -m unittest discover -s tests -t tests` 全绿（src 未动）；
2. `uv run python skills/data-ana/scripts/check_scenario.py` 两个场景全 PASS；
3. 两个 ipynb 改后 `nbformat.validate` 通过、能正常打开；
4. 全仓 grep 无残留 `../docs/` 引用；
5. 不做 git 提交（留给你检查后自己提交）。