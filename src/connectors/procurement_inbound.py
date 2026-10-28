#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据中台（easy-data-api）接口客户端 —— 入库记录取数。

对应接口
--------
「推送到workbuddy的供应链库存物料入库日汇总表」（API ID 68）::

    POST http://10.3.245.1:10073/easy-data-api/dazd_prod/workbuddy/ads_wkb_scm_inv_item_in_1d
    协议 HTTP｜返回 JSON｜分页 page_num / page_size｜认证 AK/SK（appKey + signature）

接口定义见``docs/入库记录接口.md``。本模块只负责签名、分页和响应解析，
返回接口原始记录；字段转换由 ``src/cleaning/procurement_inbound.py`` 负责。

凭证
----
两条路径，优先真实环境变量：

1. 项目根目录 ``.env``（已在 ``.gitignore`` 中忽略，推荐）::

       SCM_API_APP_KEY=<appKey>
       SCM_API_APP_SECRET=<appSecret>

2. 临时导出环境变量::

       export SCM_API_APP_KEY=<appKey>
       export SCM_API_APP_SECRET=<appSecret>

凭证变量名固定为上面两个。填完用 ``--check-env`` 验证（会真打一页）。

用法
----
::

    # 通过数据仓统一入口取数
    from pipeline import load_from_connector
    df, meta = load_from_connector("procurement-inbound")

    # 检查 .env 凭证并验证签名
    python -m connectors.procurement_inbound --check-env

    # 单独验证客户端（不需要内网，用内置样例跑通签名/翻页/响应解析）
    python -m connectors.procurement_inbound --self-test

    # 查看解析后的实际请求地址与分页参数
    python -m connectors.procurement_inbound --show-config
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from functools import partial
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


DEFAULT_BASE_URL = "http://10.3.245.1:10073"
ALTERNATE_BASE_URL = "http://hzsdpleasydata01p:10073"
API_PATH = "/easy-data-api/dazd_prod/workbuddy/ads_wkb_scm_inv_item_in_1d"

DEFAULT_PAGE_SIZE = 200
DEFAULT_MAX_PAGES = 2000
DEFAULT_TIMEOUT = 10.0

#: transport 协议：url, headers, body(bytes), timeout(秒) -> 已解析的响应 dict
Transport = Callable[[str, Dict[str, str], bytes, float], Any]


class DataPlatformError(RuntimeError):
    """接口不可达、返回失败或响应结构不符合文档。"""


# --------------------------------------------------------------------------
# 签名
# --------------------------------------------------------------------------
def sign(app_secret: str, timestamp: str) -> str:
    """接口约定的签名：``md5(appSecret + timestamp)``，小写十六进制。"""
    return hashlib.md5(f"{app_secret}{timestamp}".encode("utf-8")).hexdigest()


def _now_stamp() -> str:
    """当前时间戳。接口文档只写「调用时间戳」，未指明单位；按客户端系统惯例用毫秒。"""
    return str(int(time.time() * 1000))


def _join_url(base_url: Optional[str], path: Optional[str]) -> str:
    base = (base_url or "").rstrip("/")
    tail = path or ""
    if tail and not tail.startswith("/"):
        tail = "/" + tail
    return f"{base}{tail}"


def _is_success(code: Any, success_code: Any) -> bool:
    """容错比较：接口可能把 code 返回成 0 / "0"。"""
    return str(code).strip() == str(success_code).strip()


# --------------------------------------------------------------------------
# 默认传输层（标准库，避免额外依赖）
# --------------------------------------------------------------------------
def default_transport(
    url: str,
    headers: Dict[str, str],
    body: bytes,
    timeout: float,
    proxy: Optional[str] = None,
) -> Any:
    """用 http.client 发 POST，可选走 HTTP 代理。

    不用 urllib 是因为它会把请求头名改成 ``Appkey`` 这种形式（``key.capitalize()``），
    而数据中台网关按 ``appKey`` 原样匹配；http.client 保留大小写。

    走代理时（Clash / 公司 HTTP 代理 / SSH 本地转发都行）：
    - 代理收到的是**绝对 URI**（``POST http://10.3.245.1:10073/... HTTP/1.1``），
      由代理自己去连目标，本机不需要有到内网的路由；
    - ``proxy`` 支持 ``http://user:pass@host:port`` 或简写 ``host:port``。
    """
    import base64
    import http.client
    from urllib.parse import urlsplit

    target = urlsplit(url)
    if not target.hostname:
        raise DataPlatformError(f"接口地址不合法：{url}")

    if proxy:
        raw = proxy if "//" in proxy else f"http://{proxy}"
        hop = urlsplit(raw)
        if not hop.hostname:
            raise DataPlatformError(f"代理地址不合法：{proxy}")
        connection = http.client.HTTPConnection(hop.hostname, hop.port or 80, timeout=timeout)
        request_target = url
        headers = dict(headers)
        headers.setdefault("Host", f"{target.hostname}:{target.port or 80}")
        if hop.username:
            token = base64.b64encode(
                f"{hop.username}:{hop.password or ''}".encode()
            ).decode()
            headers["Proxy-Authorization"] = f"Basic {token}"
        where = f"（经代理 {hop.hostname}:{hop.port or 80}）"
    else:
        port = target.port or (443 if target.scheme == "https" else 80)
        path = (target.path or "/") + (f"?{target.query}" if target.query else "")
        connection_cls = (
            http.client.HTTPSConnection if target.scheme == "https" else http.client.HTTPConnection
        )
        connection = connection_cls(target.hostname, port, timeout=timeout)
        request_target = path
        where = ""

    try:
        connection.request("POST", request_target, body=body, headers=headers)
        response = connection.getresponse()
        raw_body = response.read()
        status, reason = response.status, response.reason
    except TimeoutError as exc:
        raise DataPlatformError(f"接口超时（{timeout}s）{where}：{url}") from exc
    except OSError as exc:
        raise DataPlatformError(
            f"接口连接失败{where}（{url}）：{exc}。\n"
            "  未连内网时：配 SCM_API_PROXY 走代理，或先建 SSH 隧道再指向 127.0.0.1"
        ) from exc
    finally:
        connection.close()

    if status >= 400:
        detail = raw_body.decode("utf-8", "replace")[:500]
        hint = f"（代理 {proxy} 返回 502/504 说明代理自己也没连通目标）" if proxy else ""
        raise DataPlatformError(f"HTTP {status} {reason}{where}：{detail}{hint}")

    try:
        return json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise DataPlatformError(f"响应不是合法 JSON：{raw_body[:200]!r}") from exc


# --------------------------------------------------------------------------
# 客户端
# --------------------------------------------------------------------------
class ProcurementInboundClient:
    """采购入库接口客户端，固定协议见同目录接口文档。"""

    def __init__(
        self,
        *,
        app_key: Optional[str] = None,
        app_secret: Optional[str] = None,
        page_size: int = DEFAULT_PAGE_SIZE,
        timeout: Optional[float] = None,
        max_pages: int = DEFAULT_MAX_PAGES,
        transport: Optional[Transport] = None,
    ) -> None:
        self.base_url_env = "SCM_API_BASE_URL"
        env_base = os.environ.get(self.base_url_env, "").strip()
        self.base_url = env_base or DEFAULT_BASE_URL
        self.base_url_source = "环境变量" if env_base else "客户端默认地址"
        self.url = _join_url(self.base_url, API_PATH)
        self.alternates = [_join_url(ALTERNATE_BASE_URL, API_PATH)]
        self.proxy_env = "SCM_API_PROXY"
        self.proxy = os.environ.get(self.proxy_env, "").strip()
        self.app_key_env = "SCM_API_APP_KEY"
        self.app_secret_env = "SCM_API_APP_SECRET"
        self.app_key = app_key if app_key is not None else os.environ.get(self.app_key_env, "")
        self.app_secret = app_secret if app_secret is not None else os.environ.get(self.app_secret_env, "")
        self.page_size = int(page_size)
        self.start_page = 1
        self.max_pages = int(max_pages)
        self.timeout = float(timeout if timeout is not None else os.environ.get("SCM_API_TIMEOUT", DEFAULT_TIMEOUT))
        if self.page_size < 1 or self.max_pages < 1 or self.timeout <= 0:
            raise DataPlatformError("每页条数、翻页上限和超时必须大于 0")
        self.transport: Transport = transport or partial(default_transport, proxy=self.proxy)

    def candidate_urls(self) -> List[str]:
        """当前入口 + 备用入口（去重，保持顺序）。"""
        seen: List[str] = []
        for url in [self.url, *self.alternates]:
            if url and url not in seen:
                seen.append(url)
        return seen

    # -- 请求 ---------------------------------------------------------------
    def headers(self, timestamp: Optional[str] = None) -> Dict[str, str]:
        """请求头：version / appKey / timestamp / signature。"""
        if not self.app_key or not self.app_secret:
            raise DataPlatformError(
                "缺少接口凭证。二选一：\n"
                f"  ① 写进项目根目录 .env：{self.app_key_env}=<appKey> / {self.app_secret_env}=<appSecret>\n"
                f"  ② 或临时 export {self.app_key_env}=... {self.app_secret_env}=...\n"
                "填完用 python -m connectors.procurement_inbound --check-env 验证签名。\n"
                "（appKey/appSecret 见数据中台接口文档「授权信息」，不写入仓库）"
            )
        stamp = timestamp or _now_stamp()
        return {
            "Content-Type": "application/json",
            "version": "v1",
            "appKey": self.app_key,
            "timestamp": stamp,
            "signature": sign(self.app_secret, stamp),
        }

    def fetch_page(self, page_num: int, page_size: Optional[int] = None) -> List[Dict[str, Any]]:
        """取一页数据，返回记录列表。"""
        size = int(page_size or self.page_size)
        body = {
            "page_num": str(page_num),
            "page_size": str(size),
        }
        payload = self.transport(
            self.url,
            self.headers(),
            json.dumps(body, ensure_ascii=False).encode("utf-8"),
            self.timeout,
        )
        return self._rows_of(payload)

    def _rows_of(self, payload: Any) -> List[Dict[str, Any]]:
        if not isinstance(payload, dict):
            raise DataPlatformError(
                f"响应结构异常，期望 JSON 对象，实际 {type(payload).__name__}"
            )
        code = payload.get("code")
        if not _is_success(code, 0):
            message = payload.get("message") or payload
            raise DataPlatformError(f"接口返回失败 code={code}：{message}")

        data = payload.get("data")
        if not isinstance(data, dict) or "data" not in data:
            raise DataPlatformError("响应缺少 data.data 数组，已中止取数")
        rows = data["data"]
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise DataPlatformError("响应字段 data.data 必须是记录数组")
        return rows

    def fetch_all(
        self,
        page_size: Optional[int] = None,
        progress: Optional[Callable[[int, int, int], None]] = None,
    ) -> List[Dict[str, Any]]:
        """翻完所有页。

        终止条件：本页为空或本页条数小于 page_size。接口未返回总条数，所以用这两条
        判断；同时用 max_pages 兜底，防止分页参数失效时无限翻页。
        """
        size = int(page_size or self.page_size)
        rows: List[Dict[str, Any]] = []
        page = self.start_page
        for _ in range(self.max_pages):
            batch = self.fetch_page(page, size)
            if progress:
                progress(page, len(batch), len(rows))
            if not batch:
                break
            rows.extend(batch)
            if len(batch) < size:
                break
            page += 1
        else:
            raise DataPlatformError(
                f"翻页达到上限 {self.max_pages} 页仍未结束（已取 {len(rows)} 行），"
                "请检查分页参数是否被接口忽略"
            )
        return rows

# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _self_test() -> int:
    """不连内网，用内置样例验证签名、翻页、响应解析。"""
    sample = {
        "id": 1,
        "company_code": "3004",
        "company_name": "浙江迪安医疗器械有限公司",
        "company_type": "检验产品",
        "store_code": "2701001",
        "store_name": "合格品仓",
        "inv_inout_date": "2026-09-30",
        "item_num": "11002194",
        "item_trade_name": "低密度脂蛋白胆固醇测定试剂盒",
        "level1_class_name": "体外诊断产品",
        "supplier_name": "杭州云医购供应链科技有限公司",
        "inv_in_qty": "12.000000",
        "purchase_order_amt": "1234.560000",
        "tax_rate": "0.130000",
        "notax_purchase_order_amt": "1234.560000",
        "pt": "2026-10-27",
    }

    calls: List[Dict[str, Any]] = []

    def fake_transport(url, headers, body, timeout):
        calls.append({"url": url, "headers": dict(headers), "body": json.loads(body)})
        page = calls[-1]["body"]["page_num"]
        # 第 1 页满页（2 条）促使继续翻页，第 2 页 1 条触发结束
        rows = [sample, sample] if page == "1" else [sample]
        return {"code": 0, "data": {"data": rows}}

    client = ProcurementInboundClient(
        page_size=2, app_key="demo-key", app_secret="demo-secret", transport=fake_transport
    )
    rows = client.fetch_all()

    checks: List[tuple[str, bool, str]] = []
    expected_sign = sign("demo-secret", calls[0]["headers"]["timestamp"])
    checks.append(("签名 = md5(appSecret+timestamp)", calls[0]["headers"]["signature"] == expected_sign,
                   calls[0]["headers"]["signature"]))
    checks.append(("请求方式与地址", calls[0]["url"].endswith(API_PATH), calls[0]["url"]))
    checks.append(("分页参数按文档示例传字符串",
                   calls[0]["body"] == {"page_num": "1", "page_size": "2"}, str(calls[0]["body"])))
    checks.append(("满页继续翻页、末页停止", len(calls) == 2 and len(rows) == 3, f"{len(calls)} 次请求 / {len(rows)} 行"))
    width = max(len(name) for name, _, _ in checks)
    failed = 0
    for name, ok, detail in checks:
        print(f"  {'✓' if ok else '✗'} {name.ljust(width)}  {detail}")
        failed += 0 if ok else 1
    print(f"\n自测{'通过' if not failed else f'失败（{failed} 项）'}：{len(checks) - failed}/{len(checks)}")
    return 0 if not failed else 1


def _check_env(env_path: Optional[str] = None) -> int:
    """检查 .env / 环境变量里的凭证，并真打一次接口验证签名。"""
    from envfile import DEFAULT_ENV_FILE, describe_env, load_env

    path = Path(env_path) if env_path else DEFAULT_ENV_FILE
    state = "存在" if path.is_file() else "不存在（只读环境变量）"
    print(f"凭证文件  {path} —— {state}")

    applied = load_env(path)
    print(f"本次载入  {', '.join(applied) if applied else '无（环境变量已就绪或文件为空）'}")

    names = ["SCM_API_APP_KEY", "SCM_API_APP_SECRET"]
    print()
    for item in describe_env(names, path):
        print(f"  {item['name']:<20} 就绪={item['ready']}  来源={item['source']}  {item['value']}")

    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        print(f"\n缺 {', '.join(missing)}，填进 {path.name} 后再跑一次。")
        return 1

    client = ProcurementInboundClient()
    print(f"\n验证方式  取第 1 页 1 条 —— POST {client.url}")
    try:
        rows = client.fetch_page(1, 1)
    except DataPlatformError as exc:
        print(f"检查失败：{exc}")
        print("  3004 / app key auth fail → appSecret 填错或漏字符，核对数据中台「授权信息」")
        print("  接口不可达               → 未连内网/VPN，或 base_url 变更")
        return 1
    print(f"检查通过：签名有效，返回 {len(rows)} 条记录。")
    return 0


def _probe(env_path: Optional[str] = None, timeout: float = 6.0) -> int:
    """逐个入口试一次（各取 1 条），告诉你哪个通、哪个不通、下一步填什么。"""
    from envfile import DEFAULT_ENV_FILE, load_env

    load_env(Path(env_path) if env_path else DEFAULT_ENV_FILE)
    client = ProcurementInboundClient()

    print(f"当前入口  {client.url}（来自{client.base_url_source}）")
    print(f"代理      {client.proxy or '未配置'}"
          f"{'（' + client.proxy_env + '）' if client.proxy else ''}")
    print(f"凭证      appKey={'已读' if client.app_key else '缺'} / "
          f"secret={'已读' if client.app_secret else '缺'}")
    print()

    working: List[str] = []
    for url in client.candidate_urls():
        probe = ProcurementInboundClient()
        probe.url = url
        probe.timeout = timeout
        started = time.time()
        try:
            rows = probe.fetch_page(1, 1)
        except DataPlatformError as exc:
            cost = time.time() - started
            print(f"  ✗ {url}（{cost:.1f}s）\n      {str(exc).splitlines()[0]}")
            continue
        cost = time.time() - started
        working.append(url)
        print(f"  ✓ {url}（{cost:.1f}s，返回 {len(rows)} 条）")

    if not working:
        print("\n两个入口都不通。按顺序排查：")
        print("  1. 是否有真正的公司 VPN（EasyConnect/深信服等）—— Clash 管不到 10.3.245.0/24")
        print("  2. 有内网跳板机的话，建隧道后把入口指向本地：")
        print("       ssh -N -L 10073:10.3.245.1:10073 <user>@<跳板机>")
        print("       SCM_API_BASE_URL=http://127.0.0.1:10073")
        print("  3. 有 HTTP 代理的话：SCM_API_PROXY=http://127.0.0.1:7897")
        return 1

    if client.url not in working:
        base = working[0].split("/easy-data-api")[0]
        print("\n当前入口不通，改用可用的那个（写进 .env，不改仓库文件）：")
        print(f"  {client.base_url_env}={base}")
    else:
        print("\n当前入口可用，直接跑：")
        print("  python scripts/sync_data.py --source procurement-inbound")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="数据中台接口客户端（入库记录）")
    parser.add_argument("--self-test", action="store_true", help="用内置样例验证签名/翻页/响应解析")
    parser.add_argument("--show-config", action="store_true", help="打印解析后的接口配置")
    parser.add_argument("--check-env", action="store_true", help="检查 .env 凭证并真打一次接口")
    parser.add_argument("--probe", action="store_true", help="逐个入口探测连通性（IP / 内网域名）")
    parser.add_argument("--env-file", default=None, help="指定 .env 路径，默认项目根目录 .env")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    if args.check_env:
        return _check_env(args.env_file)

    from envfile import load_env

    load_env(Path(args.env_file) if args.env_file else None)
    if args.probe:
        return _probe(args.env_file)

    client = ProcurementInboundClient()
    if args.show_config:
        from envfile import describe_env

        print("来源        procurement-inbound（采购入库）")
        print(f"请求地址    POST {client.url}（来自{client.base_url_source}）")
        if client.alternates:
            print(f"备用入口    {'；'.join(client.alternates)}")
        print(f"代理        {client.proxy or '未配置'}")
        print(f"分页        page_num / page_size，每页 {client.page_size}，起始页 1")
        print(f"超时        {client.timeout} 秒")
        names = ["SCM_API_APP_KEY", "SCM_API_APP_SECRET"]
        print("凭证        " + "；".join(
            f"{item['name']}={'已就绪' if item['ready'] == '是' else '未配置'}（{item['source']}）"
            for item in describe_env(names)
        ))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
