# 采购增量识别：派发和跟踪

本场景是采购增量识别出的谈判候选物料，不是采购降本核算。

## 已有目标表

| 配置 | 当前值 |
|---|---|
| Base | 降本识别 |
| Base ID | `14lgGw3P8vvePdElTn1v0p3A85daZ90D` |
| 数据表 | 采购增量识别 |
| Table ID | `qzEedcw` |
| View ID | `N0XUl5q` |
| 地址 | `https://alidocs.dingtalk.com/i/nodes/14lgGw3P8vvePdElTn1v0p3A85daZ90D?iframeQuery=entrance%3Ddata%26sheetId%3DqzEedcw` |
| 主字段 | 业务结束月，文本 `YYYY-MM` |
| 去重定位 | 业务结束月＋物料编码 |

持续使用这一张表，通过业务结束月区分周期。目标配置保存在 `config/dingtalk_target.json`；该配置记载新增记录会触发通知施敏超，写入前核对实际自动化状态。

## 数据及字段

从 DuckDB `outputs.procurement_increment__detail` 导出，默认最新已归档月份的「优先谈判」物料。每条记录包含以下 18 个字段：

| 字段 | 内容 |
|---|---|
| 业务结束月 | 文本，`YYYY-MM` |
| 物料编码、物料名称、规格、主单位、五级分类 | 物料识别信息；目标表的物料编码为文本，写入前转换为文本 |
| 核心供应商名称 | 核心供应商名称 |
| 谈判优先级 | 优先谈判、观察或不建议 |
| 采购负责人 | 钉钉人员字段，按下方规则转换为真实用户 |
| 任务状态 | 新记录默认为「待处理」，后续由采购员维护 |
| 最近12个月采购净量 | 数字 |
| 最近12个月含税金额万、核心供应商含税金额万 | 数字，单位万元 |
| 采购量趋势指数、连续增长月数 | 数字 |
| 最近1个月环比增长率%、单一供应商供货占比%、最近3个月采购量占比% | 数字；导出时已将比例乘以 100 |

## 导出和负责人

```bash
uv run python scripts/export_dispatch_tasks.py
uv run python scripts/export_dispatch_tasks.py 2026-10
uv run python scripts/export_dispatch_tasks.py 2026-10 --priority all
```

输出 `outputs/dingtalk_dispatch_<YYYY-MM>.json`，包含 `records`、`rows`、`owner_summary`、`unmapped_categories` 和 `owner_user_ids`。**当前脚本在 `records[*].采购负责人` 中导出姓名，`owner_user_ids` 另提供姓名到 userId 的映射；姓名不是多维表人员字段的写入值。** 写入前按姓名查映射，取得真实 userId，再由钉钉执行环境按人员字段契约组装。缺少映射时停止该条写入并核对人员，不猜 ID 或静默写空。当前脚本不会自动完成这一转换或验证缺失映射。

| 负责人 | userId | 负责人 | userId |
|---|---|---|---|
| 袁冬月 | `yuandy1` | 徐丹 | `xudan6` |
| 程新定 | `chengxd1` | 李晓玉 | `lixy61` |
| 史迪威 | `shidw2` | 林飞燕 | `linfy1` |
| 王林霞 | `wanglx6` | 黄桃 | `huangtao7` |
| 施敏超 | `shimc2` | | |

脚本按五级分类识别负责人，未匹配分类兜底施敏超，并列在 `unmapped_categories`；这一兜底仅适用于采购增量，不适用于采购降本。指定月份未归档时先完成归档，不用入库流水替代归档结果。

## 同步和反馈

读取目标表实际字段 ID 和类型；新月份新增记录，同一月份按「业务结束月＋物料编码」定位已有记录。只更新系统生成字段，保留采购员维护的任务状态、反馈和负责人修正；不要用新记录默认的「待处理」覆盖已有记录。

写后核对月份、条数、负责人及金额。跟踪反馈按实际表字段读取；本地脚本目前只导出任务，尚未实现多维表反馈回写。
