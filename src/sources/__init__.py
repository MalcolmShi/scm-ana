"""数据接入层：来源注册表。

每接入一个数据源做三件事：

1. 在本目录新增 ``<source>.py``，实现读取与口径（对应 ``docs/数据接入/<来源>.md``）；
2. 在 ``REGISTRY`` 注册来源；
3. 在 ``docs/数据接入/`` 新增同名方案文档。

注册表只负责「来源 id → 实现类」的映射，具体取数、归档和分析口径都在各自模块里。
"""
from __future__ import annotations

from typing import Dict, List

from .procurement_inbound import ProcurementInboundSource

#: 来源 id → 实现类
REGISTRY: Dict[str, type] = {
    ProcurementInboundSource.source_id: ProcurementInboundSource,
}

#: 别名 → 来源 id，例如「采购入库」「入库明细」都指向 procurement-inbound
ALIASES: Dict[str, str] = {
    alias: source_id
    for source_id, source in REGISTRY.items()
    for alias in getattr(source, "aliases", ())
}


class UnknownSourceError(RuntimeError):
    """来源未注册。"""


def resolve(source_id: str) -> str:
    """把别名或来源 id 归一成来源 id。"""
    resolved = ALIASES.get(source_id, source_id)
    if resolved not in REGISTRY:
        raise UnknownSourceError(f"未知数据来源「{source_id}」，可选 {', '.join(REGISTRY)}")
    return resolved


def get(source_id: str):
    """取出来源实现类。"""
    return REGISTRY[resolve(source_id)]


def source_ids() -> List[str]:
    """已注册的来源 id 列表。"""
    return list(REGISTRY)
