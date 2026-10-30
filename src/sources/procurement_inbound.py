"""采购入库来源：本地 Excel 导出 → raw 归档 → 清洗底表。

来源文件放在项目根目录 ``.local-data/``，读取后原样归档，字段改名和口径对齐
由业务侧在 ``data/processed/`` 完成。完整方案见 ``docs/数据接入/采购入库.md``。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .local_excel import LocalExcelSource, month_range

#: 来源 id，与归档目录名一致
SOURCE_ID = "procurement-inbound"

#: 别名，业务方口头说法都归到这里
ALIASES = ("采购入库", "入库明细")

#: 业务日期列：按业务日期分档时用它切分
DATE_COLUMN = "出入库日期"

#: 导出文件的金额单位已经是万元，代码不做单位换算
AMOUNT_UNIT = "万元"


class ProcurementInboundSource(LocalExcelSource):
    """采购入库：上游导出入库明细 Excel。"""

    source_id = SOURCE_ID
    aliases = ALIASES
    date_column = DATE_COLUMN
    amount_unit = AMOUNT_UNIT

    def __init__(self, dir_path: Optional[Path] = None, files=None) -> None:
        super().__init__(dir_path=dir_path, files=files)

    @staticmethod
    def coverage(frame) -> dict:
        """业务月份覆盖范围，由记录里的日期算出。"""
        return month_range(frame)
