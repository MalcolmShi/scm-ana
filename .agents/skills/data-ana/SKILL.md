---
name: data-ana
description: 场景化数据分析：把业务问题做成可复跑的分析产品（方案文档 + 确认 notebook + 稳定脚本 + 看板）。用户要分析数据、核对口径、出报表或看板时使用。
---

# data-ana

一个场景 = 一份方案文档 + 一组确认 notebook + 一套稳定脚本 + 一个看板。
全部状态在 `notebooks/<场景>/分析方案.md`，头部记「当前步骤」。

## 进入

1. `ls notebooks/*/分析方案.md` 发现场景。
2. 没有 → 新场景：先走第 0 步（uv 环境 + 依赖 + 目录 + 取数模块），
   再复制 `templates/分析方案.md` 建档，从第一步开始。
3. 有 → 读该场景方案，按头部「当前步骤」**只做这一步**，再读对应 references 一篇，不一次全读。

## 三条铁律

1. **一份文档走全程**：各步依次追加在同一份方案上，不另开文件；头部「当前步骤」随手更新。
2. **notebook 是确认回路**：清洗与指标在 notebook 里每步分开、逐条输出，业务确认后才落笔文档、
   才沉淀脚本（一次跑完再给人看 = 没有确认）。明细一律以**命名 dataframe** 反馈：
   - 全量明细交 dataframe，说明变量名与行数，业务用 Data Wrangler 逐笔翻；
   - 禁止 head/tail/sample/Top-N 截断——截掉的可能恰好是业务要看的；
   - 输出格只放汇总表（按组/按月，一组一行，全量）；不导 Excel、不整表渲染 HTML。
3. **后步可回退**：前步结论不成立，回对应节改并记原因，不在后步绕过。

## 三层数据

| 层 | 位置 | 谁产出 | 装什么 |
| --- | --- | --- | --- |
| raw | `data/raw/` | 第二步 sync_data | 原始日档，只追加永不改 |
| processed | `data/processed/` | 第三步清洗脚本（3.4） | 清洗后标准明细 |
| outputs | `data/outputs/` | 第四步指标脚本（4.3） | 指标结果：`data.parquet` + `data.json` |

只从下游层取数：第三步读 raw、第四步读 processed、第五步读 outputs。outputs 两份同一次运行产出。

## 步骤

| 步 | 做什么 | 产出 | 方法 |
| --- | --- | --- | --- |
| 零 | 项目初始化 | 环境、依赖、目录、取数模块 | [00](references/00-项目初始化.md) |
| 一 | 业务理解与指标构建 | §一 目标 / 痛点 / 参考逻辑 / 指标 / 反推字段清单 | [01](references/01-业务理解与指标构建.md) |
| 二 | 数据源与归档 | §二 业务供数 / 字段对照 / 归档方案 | [02](references/02-数据源与归档.md) |
| 三 | 数据清洗与聚合 | §三 异常确认 / 清洗逻辑 / 聚合逻辑 + processed 归档 + 清洗脚本 | [03](references/03-数据清洗与聚合.md) |
| 四 | 指标验证与稳定脚本 | §四 逐步验证 / 口径调整 / 输出表清单（notebook 里按真实报表确认）+ 指标脚本 + parquet、json 归档 | [04](references/04-指标验证与稳定脚本.md) |
| 五 | 可视化看板 | §五 版式与语义约定 + 单文件静态看板（不部署） | 见下 |

**第五步·看板**：只吃第四步的 `data.json`，不算新数；数据内嵌进 HTML，`outputs/dashboard/<场景>/<业务结束日>.html`，
双击打开即交付，不部署。页面用 visualize 技能（`.agents/skills/visualize/`）生成，不给它补算新指标；
颜色方向（降本=绿、成本上升=红）、单位、小数位、空值≠0 写进 §五，一处一个样就算没做完。

## 命令

```bash
uv run python -m unittest discover -s tests -t tests   # 测试
uv run python scripts/sync_data.py --list               # 数据导入（Excel → raw 日档）
uv run python scripts/run_analysis.py --dry-run         # 跑一个已落地的分析
```
