# -*- coding: utf-8 -*-
# httpx 工具：统一创建 AsyncClient + Set-Cookie 回写辅助
from typing import Dict, Optional

import httpx
import config


def make_async_client(**kwargs) -> httpx.AsyncClient:
    """创建统一配置的 httpx.AsyncClient。

    从配置文件读取 DISABLE_SSL_VERIFY（默认 False，即开启 SSL 验证）。
    仅在使用企业代理、Burp、mitmproxy 等中间人代理时才需将其设为 True。
    """
    kwargs.setdefault("verify", not getattr(config, "DISABLE_SSL_VERIFY", False))
    return httpx.AsyncClient(**kwargs)


def merge_response_cookies(response: httpx.Response,
                           cookie_dict: Dict[str, str],
                           headers: Dict[str, str]) -> bool:
    """
    从 httpx 响应中提取 Set-Cookie，合并到 cookie_dict 和 headers["Cookie"]。
    返回 True 表示有更新，False 表示无变化。
    各平台 client 在 request() 后调用此函数实现 cookie 自动续期。
    """
    updated = False
    set_cookies = response.headers.get_list("set-cookie") if hasattr(response.headers, "get_list") else []
    if not set_cookies:
        raw = response.headers.get("set-cookie", "")
        if raw:
            set_cookies = [raw]

    for sc in set_cookies:
        name_val = sc.split(";")[0].strip()
        if "=" in name_val:
            name, _, val = name_val.partition("=")
            name, val = name.strip(), val.strip()
            if name and val and cookie_dict.get(name) != val:
                cookie_dict[name] = val
                updated = True

    if updated:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookie_dict.items())
    return updated
