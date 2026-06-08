# -*- coding: utf-8 -*-
# URL 平台识别器
# 根据 URL 域名自动判断所属平台，返回 (platform_code, content_id_or_none)
# content_id 为 None 时，url_check 调度器会尝试重定向解析或原始URL直接获取

import re
from typing import Optional, Tuple
from urllib.parse import urlparse, parse_qs

# 域名 → 平台代码 映射表
# 键为域名后缀（支持子域名匹配），值为 MediaCrawler 平台代码
_DOMAIN_PLATFORM_MAP = {
    "douyin.com": "dy",
    "iesdouyin.com": "dy",
    "kuaishou.com": "ks",
    "bilibili.com": "bili",
    "b23.tv": "bili",          # B站短链，需重定向解析获取 BV号
    "toutiao.com": "toutiao",
    "toutiao.org": "toutiao",
    "ixigua.com": "xigua",
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


def detect_source_platform(url: str, platform: str) -> str:
    """区分检测链路平台和原始来源平台；西瓜当前不走头条链路，直接展示为不支持。"""
    try:
        hostname = (urlparse(url).hostname or "").lower()
    except Exception:
        return platform
    if platform in ("toutiao", "xigua") and (hostname == "ixigua.com" or hostname.endswith(".ixigua.com")):
        return "xigua"
    return platform


def _extract_content_id(
    platform: str, url: str, path: str, query: str
) -> Optional[str]:
    """
    按平台规则从 URL 路径中提取作品 ID。
    返回 None 表示需要调度器通过重定向解析或原始URL直接获取。
    """

    if platform == "dy":
        # /video/7628682927572997561 或 /note/7637386017688071330
        m = re.search(r"/(?:video|note)/(\d+)", path)
        if m:
            return m.group(1)
        # ?modal_id=xxx
        m = re.search(r"modal_id=(\d+)", query)
        if m:
            return m.group(1)
        # v.douyin.com/xxx/ 短链 → content_id=None，由调度器重定向解析
        return None

    if platform == "bili":
        # /video/BV1xx411c7mZ/
        m = re.search(r"/video/(BV[\w]+)", path)
        if m:
            return m.group(1)
        # /video/av12345 → 旧版AV号格式
        m = re.search(r"/video/(av\d+)", path, re.IGNORECASE)
        if m:
            return m.group(1)
        # b23.tv/xxx 短链 → content_id=None，由调度器重定向解析
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

    if platform in ("toutiao", "xigua"):
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
        # 标准格式：/{uid}/{mid} 如 /8212373973/QFjQ9EhMo
        m = re.search(r"/(\d+)/(\w+)", path)
        if m:
            return m.group(2)

        # m.weibo.cn/detail/{mid}  或 weibo.com/detail/{mid}
        m = re.search(r"/detail/(\w+)", path)
        if m:
            return m.group(1)

        # 长微博：/ttarticle/p/show?id=xxx → 从 query 提取文章 ID
        if "/ttarticle/" in path:
            params = parse_qs(query)
            ids = params.get("id", [])
            if ids:
                return f"__ttarticle:{ids[0]}"

        # 微博视频：/tv/show/1034:5298475685052520 → 提取 object_id
        m = re.search(r"/tv/show/(\d+):(\d+)", path)
        if m:
            return f"__tv:{m.group(1)}:{m.group(2)}"

        # 其他微博URL变体 → content_id=None，由调度器通过原始URL获取
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
        row["_source_platform"] = detect_source_platform(url, platform)
        row["_content_id"] = content_id
        groups.setdefault(platform, []).append(row)
    return groups


# 匹配文本中所有 http/https URL（含短链、分享链接中夹杂的文字场景）
_URL_PATTERN = re.compile(r'https?://[^\s<>"\')\]]+')


def extract_urls_from_text(text: str) -> list[str]:
    """
    从任意文本中提取所有 URL 并过滤出支持的平台链接。

    适用场景：用户粘贴分享文本（含标题、时间戳、短链等混合内容），
    自动提取其中所有可识别平台的链接。
    短链（如 v.douyin.com）会保留，由后续引擎导航时自动重定向。

    Returns: 去重后的 URL 列表（保持输入顺序）
    """
    if not text:
        return []

    raw_urls = _URL_PATTERN.findall(text)
    # 清理尾部可能误匹配的标点
    cleaned = []
    for u in raw_urls:
        u = u.rstrip(",.;:!?。，；：！？、）)】》")
        if u:
            cleaned.append(u)

    # 过滤出支持的平台链接，同时去重保序
    seen = set()
    result = []
    for url in cleaned:
        if url in seen:
            continue
        seen.add(url)
        platform, _ = detect_platform(url)
        if platform != "unknown":
            result.append(url)
    return result
