"""指标结果归档：同一份结果落 parquet + json 两份，供看板取数。

```python
from reporting.metrics import archive_metrics

paths = archive_metrics(
    report,                       # 主表：一行一业务粒度（§4.1 验证过的指标）
    scene="采购增量分析",
    snapshot_date="2026-09-30",   # 业务结束日，不是跑数日
    tables={"月度数据": monthly, "优先级汇总": priority_summary},
    meta={"window_start": "2025-10-01", "window_end": "2026-09-30",
          "entrypoint": "uv run python scripts/run_analysis.py"},
)
paths["parquet"]   # data/outputs/采购增量分析/2026/2026-09/2026-09-30/data.parquet
paths["json"]      # 同目录 data.json —— 第五步可视化直接读这一份
```

分工与理由：

- **parquet** 是复算与对拍的事实件：pandas 直接读，行数金额逐项可比；
- **json** 是给网页的数据契约：一份数据两处消费，不让人在 HTML 里再解析 parquet；
- 两份同目录同日期，看板、报告、脚本都指得出「这份数哪来的」；
- 路径规则沿用 ``archive`` 的 ``data/<层>/<名称>/<YYYY>/<YYYY-MM>/<YYYY-MM-DD>/``。
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import pandas as pd

import archive
from paths import TIMEZONE

__all__ = ["archive_metrics", "build_payload", "read_outputs", "json_path_for"]

#: 主表在 json ``tables`` 里的键名
MAIN_TABLE = "detail"


def json_path_for(scene: str, snapshot_date: str) -> Path:
    """与 data.parquet 同目录的 data.json。"""
    return archive.snapshot_path(archive.SCENE_LAYER, scene, snapshot_date).with_name("data.json")


def _records(frame: pd.DataFrame) -> Dict[str, Any]:
    """DataFrame → 可直接 JSON 化的块：列名 + 逐行记录（NaN 变 null，numpy 类型转原生）。"""
    return {
        "columns": [str(column) for column in frame.columns],
        "rows": json.loads(frame.to_json(orient="records", force_ascii=False, date_format="iso")),
    }


def build_payload(tables: Mapping[str, pd.DataFrame],
                  meta: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """拼出 json 结构，不落盘 —— 直接在 notebook 里看、或给 HTML 内嵌时用。"""
    return {
        "meta": dict(meta or {}),
        "tables": {name: _records(frame) for name, frame in tables.items()},
    }


def _write_json(payload: Dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".data-", suffix=".part",
                                     delete=False) as file:
        temporary = Path(file.name)
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=1),
                             encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def archive_metrics(frame: pd.DataFrame, scene: str, snapshot_date: str,
                    tables: Optional[Mapping[str, pd.DataFrame]] = None,
                    meta: Optional[Mapping[str, Any]] = None) -> Dict[str, Path]:
    """把指标结果归档成 parquet + json，返回两个路径。

    Args:
        frame: 主表（明细粒度，一行一业务粒度），落到 ``data.parquet``。
        scene: 场景名，与 ``notebooks/<场景>/`` 同名。
        snapshot_date: 业务结束日 ``YYYY-MM-DD``；不是跑数日——同一天重跑原子覆盖。
        tables: 看板还要的其它块（月度、分组汇总），一并进 json，不落 parquet。
        meta: 口径留痕（窗口、来源文件、脚本入口、数据时点），写进 json ``meta``。
    """
    payload_meta = {
        "scene": scene,
        "snapshot_date": snapshot_date,
        "generated_at": datetime.now(TIMEZONE).isoformat(timespec="seconds"),
        "rows": int(len(frame)),
    }
    payload_meta.update(dict(meta or {}))

    parquet_path = archive.write_snapshot(
        frame, archive.SCENE_LAYER, scene, snapshot_date, check_fetched_at=False,
    )
    payload = build_payload({MAIN_TABLE: frame, **dict(tables or {})}, payload_meta)
    return {"parquet": parquet_path, "json": _write_json(payload, json_path_for(scene, snapshot_date))}


def read_outputs(scene: str, snapshot_date: Optional[str] = None) -> tuple[pd.DataFrame, dict]:
    """读回一版指标归档；日期省略时取最新一版。第五步只从这里取数。"""
    return archive.read_snapshot(archive.SCENE_LAYER, scene, snapshot_date)
