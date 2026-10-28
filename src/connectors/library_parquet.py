#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 WorkBuddy 资料库（团队空间）拉取二进制数据文件到本地缓存。

为什么需要这个模块
------------------
资料库里的 parquet 属于 ``drive`` 文件节点：服务端不解析、不建索引，也**没有
parquet 查询引擎**。任何消费方——包括其他同事的 Agent——想读到里面的数据，
唯一路径都是「取短时效下载链接 → 下载到本地 → 本地解析」。

所以共享空间里的 parquet 是「云端存放 + 本地消费」模式，不是「云端直查」模式。
本模块把这条链路封装起来，并加上版本校验缓存，避免每次跑批都重复下载 5MB+。

用法
----
Notebook / 脚本里::

    from library_parquet import load_parquet, fetch_parquet

    df = load_parquet("入库明细")                      # 取数并直接读成 DataFrame
    df = load_parquet("入库明细", columns=[...])        # 列裁剪，快很多
    path = fetch_parquet("入库明细")                    # 只需要本地路径时

命令行::

    python3 library_parquet.py --list
    python3 library_parquet.py --fetch 入库明细
    python3 library_parquet.py --verify 入库明细
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

__all__ = ["fetch_parquet", "load_parquet", "list_datasets", "verify_cache", "DATASETS"]


# --------------------------------------------------------------------------
# 数据集注册表：名字 -> 资料库节点 id（/space/d/<node_id> 里的那一段）
# 新增数据集时，把对应文件的资料库链接贴进来解出 node_id 填到这里即可。
# --------------------------------------------------------------------------
DATASETS: Dict[str, str] = {
    "入库明细": "kQAeV1u46MYRAv7D7vBRk4",
}

#: 空间 id，仅用于提示和排障
SPACE_ID = "TSTg1P1GzqTaGCAzB4Fuyw"

#: 缓存根目录。默认落在用户主目录，避免污染 git 仓库。
DEFAULT_CACHE_ROOT = Path(
    os.environ.get("SCM_ANA_LIBRARY_CACHE", Path.home() / ".cache" / "scm-ana" / "library")
)


class LibraryFileError(RuntimeError):
    """拉取资料库文件失败。"""


# --------------------------------------------------------------------------
# 定位 library skill
# --------------------------------------------------------------------------
def _find_library_skill_dir() -> Path:
    """找到 WorkBuddy 自带的 library skill 目录。

    优先用环境变量覆盖，其次是桌面端安装路径，最后扫用户级插件缓存——
    队友的机器安装位置可能不同，所以要多处兜底。
    """
    candidates: List[Path] = []

    override = os.environ.get("WORKBUDDY_LIBRARY_SKILL_DIR")
    if override:
        candidates.append(Path(override).expanduser())

    candidates.append(
        Path(
            "/Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/resources"
            "/plugins/workbuddy-builtin/skills/library"
        )
    )

    cache_root = Path.home() / ".workbuddy" / "plugins" / "cache"
    if cache_root.is_dir():
        candidates.extend(sorted(cache_root.glob("*/workbuddy-builtin/*/skills/library")))

    for candidate in candidates:
        if (candidate / "space_api.py").is_file() and (candidate / "drive").is_dir():
            return candidate

    raise LibraryFileError(
        "找不到 WorkBuddy library skill 目录，无法调用资料库接口。\n"
        "请设置环境变量 WORKBUDDY_LIBRARY_SKILL_DIR 指向该目录后重试。"
    )


def _run(cmd: List[str]) -> str:
    """执行子命令并返回 stdout，失败时抛出带原始输出的异常。"""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise LibraryFileError(
            f"命令执行失败（exit {proc.returncode}）: {' '.join(cmd)}\n{proc.stderr.strip()}"
        )
    return proc.stdout


def _node_info(node_id: str) -> Dict[str, Any]:
    """查询节点信息，重点取 version（内容替换后会递增，用于缓存失效判断）。"""
    skill_dir = _find_library_skill_dir()
    out = _run(
        [sys.executable, str(skill_dir / "space_api.py"),
         "space.workspace.node-info", "--node-id", node_id]
    )
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "error" in payload:
                raise LibraryFileError(f"查询节点失败: {payload['error']}")
            return payload.get("data", {}).get("node", {})
    raise LibraryFileError(f"无法解析节点信息: {out[:300]}")


def _download_link(node_id: str) -> Dict[str, Any]:
    """取短时效预签名下载链接（有效期约 2 小时，用前才取，不要缓存）。"""
    skill_dir = _find_library_skill_dir()
    out = _run(
        [sys.executable, str(skill_dir / "drive" / "get_download_link.py"), "--node-id", node_id]
    )
    for line in out.splitlines():
        if line.startswith("KS_DRIVE_DOWNLOAD "):
            return json.loads(line[len("KS_DRIVE_DOWNLOAD "):])
        if line.startswith("{"):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "error" in payload:
                raise LibraryFileError(f"取下载链接失败: {payload['error']}")
    raise LibraryFileError(f"无法解析下载链接响应: {out[:300]}")


# --------------------------------------------------------------------------
# 下载与校验
# --------------------------------------------------------------------------
def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_valid_parquet(path: Path) -> bool:
    """校验 parquet 魔数——头部和尾部都应是 PAR1。"""
    try:
        with path.open("rb") as handle:
            if handle.read(4) != b"PAR1":
                return False
            handle.seek(-4, os.SEEK_END)
            return handle.read(4) == b"PAR1"
    except (OSError, ValueError):
        return False


def _download(url: str, dest: Path) -> None:
    """流式下载到临时文件后原子改名，中断不会留下半个坏文件。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent), prefix=".dl-", suffix=".part")
    os.close(tmp_fd)
    tmp_path = Path(tmp_name)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "scm-ana-library/1.0"})
        with urllib.request.urlopen(request, timeout=300) as response, tmp_path.open("wb") as handle:
            shutil.copyfileobj(response, handle, length=1024 * 1024)
        shutil.move(str(tmp_path), str(dest))
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def _cache_paths(node_id: str, file_name: str, cache_root: Optional[Path]) -> "tuple[Path, Path]":
    root = Path(cache_root) if cache_root else DEFAULT_CACHE_ROOT
    safe_name = "".join(ch for ch in file_name if ch not in '\\/:*?"<>|').strip() or "data.bin"
    target_dir = root / node_id
    return target_dir / safe_name, target_dir / f"{safe_name}.meta.json"


# --------------------------------------------------------------------------
# 对外接口
# --------------------------------------------------------------------------
def fetch_parquet(
    node_id: str,
    cache_root: Optional[Path] = None,
    force: bool = False,
    file_name: Optional[str] = None,
) -> Path:
    """把资料库里的文件拉到本地缓存，返回本地路径。

    缓存命中（节点 version 未变且文件哈希一致）时不会重复下载。

    Args:
        node_id: 资料库节点 id，或 ``DATASETS`` 里注册的数据集名字（两种都接受）。
        cache_root: 缓存根目录，默认为 ``~/.cache/scm-ana/library``。
        force: 忽略缓存强制重新下载。
        file_name: 指定本地文件名，默认沿用云端文件名。
    """
    node_id = DATASETS.get(node_id, node_id)
    node = _node_info(node_id)
    if not node:
        raise LibraryFileError(f"节点 {node_id} 不存在或无权访问。")

    remote_version = node.get("version")
    remote_name = file_name or f"{node.get('title', 'data')}.{node.get('ext', 'bin')}"
    target, meta_path = _cache_paths(node_id, remote_name, cache_root)

    if not force and target.is_file() and meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            meta = {}
        if (
            meta.get("version") == remote_version
            and meta.get("size") == target.stat().st_size
            and meta.get("sha256") == _sha256(target)
        ):
            return target

    link = _download_link(node_id)
    url = link.get("download_url")
    if not url:
        raise LibraryFileError("资料库未返回下载链接，请确认当前账号对该空间有访问权限。")

    target.parent.mkdir(parents=True, exist_ok=True)
    _download(url, target)

    if not _is_valid_parquet(target) and target.suffix.lower() == ".parquet":
        target.unlink(missing_ok=True)
        raise LibraryFileError("下载内容不是有效的 parquet 文件（魔数校验失败），已丢弃，请重试。")

    meta_path.write_text(
        json.dumps(
            {
                "node_id": node_id,
                "file_name": remote_name,
                "version": remote_version,
                "size": target.stat().st_size,
                "sha256": _sha256(target),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "source": f"https://www.workbuddy.cn/space/d/{node_id}",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return target


def load_parquet(
    dataset: str = "入库明细",
    columns: Optional[Iterable[str]] = None,
    cache_root: Optional[Path] = None,
    force: bool = False,
) -> "Any":
    """取数并直接读成 DataFrame。

    Args:
        dataset: ``DATASETS`` 注册表里的名字，或直接传 node_id。
        columns: 只读这些列——parquet 支持列裁剪，能显著减少 IO 和内存。
        cache_root: 缓存根目录。
        force: 强制重新下载。
    """
    import pandas as pd  # 延迟导入，让纯取数场景不必装 pandas

    node_id = DATASETS.get(dataset, dataset)
    path = fetch_parquet(node_id, cache_root=cache_root, force=force)
    return pd.read_parquet(path, columns=list(columns) if columns else None)


def list_datasets() -> Dict[str, str]:
    """列出已注册的数据集。"""
    return dict(DATASETS)


def verify_cache(dataset: str = "入库明细", cache_root: Optional[Path] = None) -> Dict[str, Any]:
    """校验本地缓存与云端版本是否一致，不一致则需要重新拉取。"""
    node_id = DATASETS.get(dataset, dataset)
    node = _node_info(node_id)
    remote_version = node.get("version")
    remote_name = f"{node.get('title', 'data')}.{node.get('ext', 'bin')}"
    target, meta_path = _cache_paths(node_id, remote_name, cache_root)

    if not target.is_file():
        return {"ok": False, "reason": "本地无缓存", "local": None, "remote_version": remote_version}

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        meta = {}

    current_hash = _sha256(target)
    checks = {
        "version_match": meta.get("version") == remote_version,
        "size_match": meta.get("size") == target.stat().st_size,
        "hash_match": meta.get("sha256") == current_hash,
    }
    return {
        "ok": all(checks.values()),
        "reason": "缓存与云端一致" if all(checks.values()) else "缓存已过期或损坏",
        "checks": checks,
        "local": str(target),
        "size": target.stat().st_size,
        "remote_version": remote_version,
        "local_version": meta.get("version"),
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _main(argv: List[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="从 WorkBuddy 资料库拉取数据文件到本地缓存"
    )
    parser.add_argument("--list", action="store_true", help="列出已注册数据集")
    parser.add_argument("--fetch", metavar="DATASET", help="拉取指定数据集")
    parser.add_argument("--verify", metavar="DATASET", help="校验本地缓存是否最新")
    parser.add_argument("--force", action="store_true", help="忽略缓存强制重新下载")
    parser.add_argument("--cache-root", type=Path, default=None, help="自定义缓存根目录")
    args = parser.parse_args(argv)

    try:
        if args.list or not (args.fetch or args.verify):
            print("已注册数据集:")
            for name, node_id in DATASETS.items():
                print(f"  {name:<12} {node_id}   https://www.workbuddy.cn/space/d/{node_id}")
            if not args.fetch and not args.verify:
                return 0

        if args.fetch:
            path = fetch_parquet(
                DATASETS.get(args.fetch, args.fetch),
                cache_root=args.cache_root,
                force=args.force,
            )
            size_mb = path.stat().st_size / 1024 / 1024
            print(f"已就绪: {path}  ({size_mb:.2f} MB)")
            return 0

        if args.verify:
            report = verify_cache(
                DATASETS.get(args.verify, args.verify), cache_root=args.cache_root
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["ok"] else 1
    except LibraryFileError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
