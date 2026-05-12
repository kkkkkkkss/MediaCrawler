# -*- coding: utf-8 -*-
# URL 平台识别器
# 根据 URL 域名自动判断所属平台，返回 (platform_code, content_id_or_none)

import re
from typing import Optional, Tuple
from urllib.parse import urlparse

# 域名 → 平台代码 映射表
# 键为域名后缀（支持子域名匹配），值为 MediaCrawler 平台代码
_DOMAIN_PLATFORM_MAP = {
    "douyin.com": "dy",
    "iesdouyin.com": "dy",
    "kuaishou.com": "ks",
    "bilibili.com": "bili",
    "b23.tv": "bili",
    "toutiao.com": "toutiao",
    "toutiao.org": "toutiao",
    "ixigua.com": "toutiao",
    "zjurl.cn": "toutiao",
    "xiaohongshu.com": "xhs",
    "xhslink.com": "xhs",
    "weibo.com": "wb",
    "weibo.cn": "wb",
    "tieba.baidu.com": "tieba",
    "zhihu.com": "zhihu",
}


def detect_platform(url: str) -> Tuple[str, Optional[str]]:
    """
    根据 URL 识别平台及可能的作品 ID。

    Returns:
        (platform_code, content_id)
        platform_code: "dy" / "bili" / "ks" / "toutiao" / "xhs" / "wb" / "tieba" / "zhihu" / "unknown"
        content_id: 尽力从 URL 中提取的作品 ID，提取不到为 None
    """
    if not url or not url.strip():
        return "unknown", None

    url = url.strip()
    if not url.startswith("http"):
        url = "https://" + url

    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
    except Exception:
        return "unknown", None

    # 匹配平台
    platform = "unknown"
    for domain_suffix, pf_code in _DOMAIN_PLATFORM_MAP.items():
        if hostname == domain_suffix or hostname.endswith("." + domain_suffix):
            platform = pf_code
            break

    # 尝试提取作品 ID
    content_id = _extract_content_id(platform, url, parsed.path, parsed.query)
    return platform, content_id


def _extract_content_id(
    platform: str, url: str, path: str, query: str
) -> Optional[str]:
    """按平台规则从 URL 路径中提取作品 ID"""

    if platform == "dy":
        # /video/7628682927572997561 或 /note/7637386017688071330
        m = re.search(r"/(?:video|note)/(\d+)", path)
        if m:
            return m.group(1)
        # ?modal_id=xxx
        m = re.search(r"modal_id=(\d+)", query)
        if m:
            return m.group(1)
        return None

    if platform == "bili":
        # /video/BV1xx411c7mZ/
        m = re.search(r"/video/(BV[\w]+)", path)
        if m:
            return m.group(1)
        return None

    if platform == "ks":
        # /short-video/3x3zxz4mjrsc8ke  或  /f/X-XmWUtg8trc1Cv
        m = re.search(r"/(?:short-video|f)/([\w-]+)", path)
        if m:
            return m.group(1)
        # /notice/detail?id=xxx  移动端分享链接
        m = re.search(r"[?&]id=([\w-]+)", query)
        if m:
            return m.group(1)
        return None

    if platform == "toutiao":
        # /article/7633568562946327083/ 或 /i7629519642230538787/ 或 /a7629519642230538787/
        m = re.search(r"/(?:article|i|a)/?(\d{15,})", path)
        if m:
            return m.group(1)
        # weitoutiao.zjurl.cn: /ugc/share/wap/comment/7633726919141622574/
        m = re.search(r"/(?:ugc|share|wap|comment)/(\d{15,})", path)
        if m:
            return m.group(1)
        # ixigua 或其他纯数字路径
        m = re.search(r"/(\d{15,})", path)
        if m:
            return m.group(1)
        return None

    if platform == "xhs":
        # /explore/xxxx  或  /discovery/item/xxxx
        m = re.search(r"/(?:explore|discovery/item)/([\w]+)", path)
        if m:
            return m.group(1)
        return None

    if platform == "wb":
        # /detail/xxx  或  /数字/xxx
        m = re.search(r"/(\d+)/(\w+)", path)
        if m:
            return m.group(2)
        m = re.search(r"/detail/(\w+)", path)
        if m:
            return m.group(1)
        return None

    return None


def group_urls_by_platform(
    rows: list,
) -> dict:
    """
    将从数据库读取的 URL 行按平台分组。
    rows: [{id, url, ...}, ...]
    Returns: {platform_code: [{id, url, content_id, ...}, ...]}
    """
    groups: dict = {}
    for row in rows:
        url = row.get("url", "")
        platform, content_id = detect_platform(url)
        row["_platform"] = platform
        row["_content_id"] = content_id
        groups.setdefault(platform, []).append(row)
    return groups
