"""降本识别层：分析注册表。

每新增一个识别分析做两件事：

1. 在本目录新增 ``<analysis>.py``，实现计算；
2. 在 ``REGISTRY`` 注册。

对应方案文档在 ``docs/降本识别/``。分析只消费归档数据，不自己取数。

注册的分析模块需要提供：

- ``Config``：本次运行的配置（dataclass）；
- ``run_analysis(config, source="local")``：执行入口，返回结果字典；
- ``export(result, out_dir)``（可选）：导出报告，返回文件路径。
"""
from __future__ import annotations

from typing import Dict, List

from . import procurement_increment

#: 分析 id → 分析模块
REGISTRY: Dict[str, object] = {
    "procurement-increment": procurement_increment,
}


def analysis_ids() -> List[str]:
    """已注册的分析 id 列表。"""
    return list(REGISTRY)


def get(analysis_id: str):
    """取出分析模块。"""
    if analysis_id not in REGISTRY:
        raise KeyError(f"未知分析「{analysis_id}」，可选 {', '.join(REGISTRY)}")
    return REGISTRY[analysis_id]
