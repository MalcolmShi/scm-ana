"""清洗层：raw → processed 明细底表。

数据源一个入口一个模块：``clean.procurement_inbound`` 是采购入库的全部清洗实现
（规则、台账、归档），这里只做转发，保持 ``from clean import ...`` 一个口子。
"""
from clean.procurement_inbound import (
    STANDARD_COLUMNS,
    UPDATED_AT_COLUMN,
    build_processed,
    clean_inbound,
)

__all__ = ["build_processed", "clean_inbound", "STANDARD_COLUMNS", "UPDATED_AT_COLUMN"]
