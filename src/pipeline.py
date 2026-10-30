"""流水线：把取数 → 分析 → 导出串成一条命令。

```python
import pipeline
from analysis.procurement_increment import Config

result = pipeline.analyze(Config(start_date="2025-10-01", end_date="2026-09-30"))
result["report_path"]    # outputs/reports/<业务结束日>/<结束月>采购增量分析报告.xlsx
```

分工：取数 ``src/read_data.py``、分析 ``src/analysis/``、导出 ``src/reporting/``。
导入（Excel → raw 日档）是另一条链路，入口 ``scripts/sync_data.py``。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from paths import OUTPUTS_ROOT

__all__ = ["analyze"]


def analyze(config: Any, out_dir: Optional[Path] = None, dry_run: bool = False) -> Dict[str, Any]:
    """跑一遍采购增量分析，默认把 Excel 报告写到 ``outputs/reports/<业务结束日>/``。

    Args:
        config: ``analysis.procurement_increment.Config``，窗口与阈值都在里面。
        out_dir: 输出根目录，默认项目 ``outputs/``。
        dry_run: 只算不写文件。

    Returns:
        分析结果字典；不 dry_run 时多一个 ``report_path``。
    """
    from analysis.procurement_increment import export, run_analysis

    result = run_analysis(config)
    if dry_run:
        return result
    result["report_path"] = export(result, Path(out_dir) if out_dir else OUTPUTS_ROOT)
    return result
