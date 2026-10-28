#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读取项目根目录的 ``.env``，把接口凭证送进环境变量。

为什么不用 python-dotenv
------------------------
项目要能在只装了 pandas 的机器上跑，.env 的语法又只有 ``KEY=VALUE`` 这一种用法，
标准库几十行足够，少一个依赖少一个装不上的理由。

规则
----
- 文件位置：项目根目录 ``.env``（可用 ``--env-file`` 指定其它路径）
- 支持：``KEY=VALUE``、``export KEY=VALUE``、``#`` 注释、空行、单/双引号包裹
- **真实环境变量优先**：进程里已经存在的变量不会被 .env 覆盖，
  所以临时改一次值可以直接 ``SCM_API_APP_SECRET=xxx python3 ...`` 覆盖，无需改文件
- 文件不存在时静默跳过（凭证也允许全部走环境变量）

用法
----
::

    from envfile import load_env, describe_env

    load_env()                                  # 载入 .env（不覆盖已有变量）
    describe_env(["SCM_API_APP_KEY"])           # 查某个变量的来源与是否就绪
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

from paths import PROJECT_ROOT

#: 默认 .env 路径
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"


def parse_env_file(path: Path) -> Dict[str, str]:
    """解析 .env 文件，返回键值对（不写入环境变量）。"""
    if not path.is_file():
        return {}

    values: Dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        values[key] = _clean_value(raw_value.strip())
    return values


def _clean_value(value: str) -> str:
    """去掉引号与外层空白；未加引号时支持行尾注释。"""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        inner = value[1:-1]
        return inner.replace("\\n", "\n") if value[0] == '"' else inner
    for marker in (" #", "\t#"):
        index = value.find(marker)
        if index != -1:
            value = value[:index]
    return value.strip()


def load_env(
    path: Optional[Path] = None,
    override: bool = False,
) -> Dict[str, str]:
    """载入 .env 到 ``os.environ``，返回**本次实际写入**的变量。

    Args:
        path: .env 路径，默认项目根目录。
        override: 是否覆盖已存在的环境变量（默认 False，真实环境变量优先）。
    """
    env_path = Path(path) if path else DEFAULT_ENV_FILE
    applied: Dict[str, str] = {}
    for key, value in parse_env_file(env_path).items():
        if not override and os.environ.get(key):
            continue
        os.environ[key] = value
        applied[key] = value
    return applied


def mask(value: str, keep: int = 4) -> str:
    """脱敏展示：保留前几位，其余用 *。用于日志与 CLI 输出。"""
    if not value:
        return "（空）"
    if len(value) <= keep:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * 8}（{len(value)} 位）"


def describe_env(
    names: List[str],
    path: Optional[Path] = None,
) -> List[Dict[str, str]]:
    """逐个变量报告：是否就绪、值从哪来（环境变量 / .env）。"""
    env_path = Path(path) if path else DEFAULT_ENV_FILE
    from_file = parse_env_file(env_path)

    report: List[Dict[str, str]] = []
    for name in names:
        value = os.environ.get(name, "")
        if value:
            source = "环境变量" if name not in from_file else "环境变量 / .env"
        elif name in from_file:
            source = ".env（已读到文件但未载入，检查是否为空值）"
        else:
            source = "未配置"
        report.append(
            {
                "name": name,
                "ready": "是" if value else "否",
                "source": source,
                "value": mask(value),
            }
        )
    return report
