---
name: dingtalk-dispatch
description: 把 DuckDB outputs 的采购增量物料导出为派发任务，写入钉钉多维表（AI表格）固定目标并派发给采购负责人执行。用户要派发采购谈判任务、更新钉钉多维表、按五级分类分配负责人时使用。
---

# dingtalk-dispatch

以 DuckDB outputs 为唯一数据前提：读取 `outputs.procurement_increment__detail`（默认最新归档周期的「优先谈判」物料），导出派发任务 JSON，再写入钉钉 AI表格的**固定目标表**派发给采购负责人。数据导出与钉钉写入两步分离，钉钉侧不计算任何指标。

## 固定目标表（不要新建 Base）

目标记录在 `config/dingtalk_target.json`，每月派发都写这一张表：

| 项 | 值 |
| --- | --- |
| Base | 降本识别 `14lgGw3P8vvePdElTn1v0p3A85daZ90D` |
| 数据表 | 采购增量识别 `qzEedcw` |
| 视图 | `N0XUl5q` |
| 主字段 | 业务结束月（第一列） |

- 表已建好 18 个字段，**不再建表、不加字段**；缺字段先向用户确认后处理。
- 钉钉 API 限制：主字段（首列）建表后无法更换，业务结束月必须保持第一列；不要用 `view-update` 把其他字段排到它前面，会报 `PRIMARY_FIELD_CANNOT_BE_MOVED`。

## 数据口径

1. 记录只能来自 `scripts/export_dispatch_tasks.py` 的输出 `outputs/dingtalk_dispatch_<YYYY-MM>.json`，不得手工编造或修改记录值。
2. 负责人规则在 `src/dispatch_rules.py`，按物料**五级分类**精确匹配；未命中的分类落到「待分配」，导出时会列出分类和条数。规则来自采购团队分工（袁冬月/徐丹/程新定/李晓玉/史迪威/林飞燕/王林霞/黄桃）：**通用类设备（离心机、纯水机、生物安全柜、其他辅助类设备等）归李晓玉**；检测专用设备跟随对应方法负责人（洗板机→史迪威、分光光度计→林飞燕、核酸蛋白仪→程新定）；荧光计、生物芯片分析仪、血糖及血糖相关参数分析仪、幽门螺旋杆菌分析仪、其他免疫分析仪、染色体待施敏超确认，新增分类先补具体规则再兜底。
3. 默认取最新归档周期的「优先谈判」；指定历史周期用位置参数 `YYYY-MM`，其他优先级用 `--priority`（`all` 不过滤）。周期未归档时先运行 `uv run python scripts/archive_procurement_increment.py <YYYY-MM>`，不绕过 outputs 直接读 processed。

## 步骤

1. **导出**：`uv run python scripts/export_dispatch_tasks.py [YYYY-MM] [--priority 优先谈判]`
   核对条数、负责人分布与未匹配分类提示；「待分配」异常时先补 `src/dispatch_rules.py` 再重跑。
2. **写入固定表**：`dws aitable +record-batch-create --base-id <baseId> --table-id <tableId>`，按导出 JSON 的 `records` 转成 `{"cells":{fieldId:值}}`，按 100 条分片传参（437 条约 5 片）。
   - `cells` 键必须用目标表的 fieldId，先用 `+table-get` 取「字段名→fieldId」；
   - **采购负责人是人员字段（user）**，写入格式 `[{"userId":"...","corpId":"ding401e19383a789223"}]`，userId 取 `src/dispatch_rules.py` 的 `OWNER_USER_IDS`；「待分配」写空，不传字符串；
   - 写入接口偶发读回延迟：返回 `read-back returned N created records, want M` 的 unknown 状态时**不要重跑**，从返回或报错里取 `clientToken`，用 `+record-write-result --client-token <token>` 核对实际落库数，等于分片条数后才写下一片，不足即停并报告。
3. **校验**：`+data-query` 按「物料编码 COUNT、最近12个月含税金额万 SUM」核对总数与金额合计，按采购负责人分组核对分布；完成后向用户返回表链接、条数和金额汇总。
4. **重复周期**：同一「业务结束月」重跑时用 `+record-upsert-by-key`（唯一键「业务结束月+物料编码」）更新，避免重复记录；`+record-batch-create` 只用于新增月份。

## 钉钉操作规则

- 所有 dws 命令必须加 `--format json`；执行前用 `dws schema "aitable +<命令>" --compact` 确认参数，不猜测字段名和参数值。
- 写操作（写记录、改表）必须先向用户展示操作摘要（目标表、条数、影响范围），用户明确同意后加 `--yes`；`confirmation_required` 时按门禁协议处理，不用管道喂答案。
- 删除表/字段/记录属不可逆危险操作：展示影响范围，用户明确确认后才能加 `--yes`。
- 采购负责人为文本字段；需要 @人、发待办联动时先确认通讯录姓名，再评估换成成员类型字段。

## 字段清单（顺序即列序，业务结束月为首列）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| 业务结束月 | 文本（主字段） | YYYY-MM，唯一键之一 |
| 物料编码 | 文本 | 唯一键之一 |
| 物料名称 | 文本 | |
| 规格 | 文本 | |
| 主单位 | 文本 | |
| 五级分类 | 文本 | 负责人规则匹配依据 |
| 核心供应商名称 | 文本 | |
| 谈判优先级 | 单选（优先谈判/观察/不建议） | 默认只派发优先谈判 |
| 采购负责人 | 人员（单选） | userId 取 `dispatch_rules.OWNER_USER_IDS`；待分配留空 |
| 采购负责人_旧 | 文本 | 历史备份列，核对无误后可删 |
| 任务状态 | 单选（待处理/谈判中/已反馈/已完成/已关闭） | 导出固定「待处理」 |
| 最近12个月采购净量 | 数字 | |
| 最近12个月含税金额万 | 数字 | |
| 核心供应商含税金额万 | 数字 | |
| 采购量趋势指数 | 数字 | |
| 连续增长月数 | 数字 | |
| 最近1个月环比增长率% | 数字 | 导出已 ×100 |
| 单一供应商供货占比% | 数字 | 导出已 ×100 |
| 最近3个月采购量占比% | 数字 | 导出已 ×100 |
