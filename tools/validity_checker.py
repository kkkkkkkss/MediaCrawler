# -*- coding: utf-8 -*-
# 作品失效检测模块
# 三层检测机制：
#   1. HTTP 预检 — httpx 跟随重定向，看最终 URL/状态码，秒判大部分失效链接
#   2. DOM 关键词 — 用浏览器打开页面，匹配各平台"已删除/不存在/违规"等关键词
#   3. 接口字段  — 从 API JSON 中判断作品状态字段（如 photo == null）
#
# 失效类型（is_valid=2 统一标记为无效，日志中区分具体原因）：
#   deleted      — 作品已被删除
#   private      — 作品被设为私密
#   violation    — 作品因违规被下架
#   not_found    — 404 / 链接不存在
#   region_block — 地区受限
#   login_required — 需登录才能查看（关弹窗后仍无主体内容）
#   unknown_dead — HTTP 层判定失效但无法细分原因

import asyncio
import re
from typing import Any, Dict, Optional, Tuple
from enum import Enum

import httpx

from tools import utils


class ValidityStatus(Enum):
    VALID = "valid"
    INVALID_DELETED = "deleted"
    INVALID_PRIVATE = "private"
    INVALID_VIOLATION = "violation"
    INVALID_NOT_FOUND = "not_found"
    INVALID_REGION_BLOCK = "region_block"
    INVALID_LOGIN_REQUIRED = "login_required"
    INVALID_UNKNOWN = "unknown_dead"
    NEED_BROWSER = "need_browser"   # HTTP 层无法确定，需要浏览器进一步检测


# ── 各平台 DOM 失效关键词 ──
# 命中任意一个即判定失效
_PLATFORM_DEAD_KEYWORDS: Dict[str, list] = {
    "dy": [
        "该视频暂时无法播放",
        "作品不存在",
        "内容不适宜",
        "该作品已被删除",
        "视频已删除",
        "该作品不存在",
        "作品审核中",
        "内容已下架",
    ],
    "bili": [
        "视频去哪了",
        "啊叻？视频不见了",
        "稿件不存在",
        "抱歉，您的权限不足",
        "该视频已下架",
    ],
    "ks": [
        "视频不存在或已删除",
        "作品不存在",
        "该作品已删除",
        "该内容已被删除",
        "无法播放",
    ],
    "toutiao": [
        "内容已删除",
        "文章不存在",
        "内容不存在",
        "该内容已下架",
        "页面不存在",
    ],
    "xhs": [
        "笔记不存在",
        "当前内容无法浏览",
        "该内容已被删除",
        "内容不存在",
    ],
    "wb": [
        "该微博因作者设置",
        "由于博主设置",
        "微博不存在",
        "抱歉，未找到该微博",
        "该微博已被删除",
        "该内容已被原作者删除",
        "内容违规",
    ],
}

# ── HTTP 预检：跳转到错误页/首页的 URL 特征 ──
_REDIRECT_DEAD_PATTERNS: Dict[str, list] = {
    "dy": [r"douyin\.com/?$", r"douyin\.com/discover", r"douyin\.com/404"],
    "bili": [r"/404$", r"bilibili\.com/?$"],
    "ks": [r"kuaishou\.com/?$", r"kuaishou\.com/404"],
    "toutiao": [r"toutiao\.com/?$", r"toutiao\.com/404"],
    "xhs": [r"xiaohongshu\.com/?$"],
    "wb": [r"weibo\.com/?$", r"weibo\.com/sorry", r"weibo\.cn/?$"],
}


async def http_pre_check(url: str, platform: str) -> Tuple[ValidityStatus, Optional[str]]:
    """
    第一层：HTTP 预检。
    用 httpx 发送 GET 请求并跟随重定向，通过最终 URL 和状态码快速判断。
    不需要浏览器，不需要登录，能在毫秒级完成。

    Returns:
        (status, final_url)
        status 为 VALID/NEED_BROWSER/INVALID_*
    """
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=15,
            verify=False,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            },
        ) as client:
            resp = await client.get(url)
            final_url = str(resp.url)
            status_code = resp.status_code

            # 明确的 404
            if status_code == 404:
                utils.logger.info(f"[http_pre_check] 404 → {url}")
                return ValidityStatus.INVALID_NOT_FOUND, final_url

            # 4xx/5xx
            if status_code >= 400:
                utils.logger.info(f"[http_pre_check] HTTP {status_code} → {url}")
                return ValidityStatus.INVALID_UNKNOWN, final_url

            # 检查最终 URL 是否跳转到了首页/错误页
            dead_patterns = _REDIRECT_DEAD_PATTERNS.get(platform, [])
            for pattern in dead_patterns:
                if re.search(pattern, final_url):
                    utils.logger.info(
                        f"[http_pre_check] 重定向到错误页: {url} → {final_url}"
                    )
                    return ValidityStatus.INVALID_NOT_FOUND, final_url

            # B 站特殊：页面 title 含 "视频去哪了"
            if platform == "bili" and resp.status_code == 200:
                text_head = resp.text[:3000]
                if "视频去哪了" in text_head or "啊叻？视频不见了" in text_head:
                    return ValidityStatus.INVALID_DELETED, final_url

            # HTTP 层无法确定，需要浏览器进一步检测
            return ValidityStatus.NEED_BROWSER, final_url

    except httpx.TimeoutException:
        utils.logger.warning(f"[http_pre_check] 超时: {url}")
        return ValidityStatus.NEED_BROWSER, None
    except Exception as e:
        utils.logger.warning(f"[http_pre_check] 异常: {url} → {e}")
        return ValidityStatus.NEED_BROWSER, None


async def dom_validity_check(
    page, url: str, platform: str
) -> ValidityStatus:
    """
    第二层：浏览器 DOM 关键词检测。
    导航到作品页面后，检查页面文本是否包含失效关键词。
    同时检测"需登录"场景：关闭弹窗后如果 3 秒内仍无视频/内容主体 DOM，判为 login_required。

    Args:
        page: Playwright Page 对象（已在浏览器会话中）
        url: 作品 URL
        platform: 平台代码
    """
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(2)

        # 尝试关闭登录弹窗（各平台通用的关闭按钮选择器）
        close_selectors = [
            'button[aria-label="关闭"]',
            '.login-close', '.close-btn', '.modal-close',
            '[class*="close"]', '[class*="Close"]',
            'svg[class*="close"]',
        ]
        for sel in close_selectors:
            try:
                elem = page.locator(sel).first
                if await elem.is_visible(timeout=500):
                    await elem.click()
                    await asyncio.sleep(0.5)
            except Exception:
                pass

        # 获取页面可见文本
        body_text = await page.evaluate("() => document.body?.innerText || ''")

        # 检查失效关键词
        dead_keywords = _PLATFORM_DEAD_KEYWORDS.get(platform, [])
        for keyword in dead_keywords:
            if keyword in body_text:
                reason = _classify_keyword(keyword)
                utils.logger.info(
                    f"[dom_validity_check] 命中关键词 [{keyword}] → {reason.value}: {url}"
                )
                return reason

        # 检测"需登录"场景：视频/内容主体是否存在
        content_selectors = _get_content_selectors(platform)
        if content_selectors:
            has_content = False
            for sel in content_selectors:
                try:
                    if await page.locator(sel).first.is_visible(timeout=3000):
                        has_content = True
                        break
                except Exception:
                    pass
            if not has_content:
                # 再等 3 秒确认
                await asyncio.sleep(3)
                for sel in content_selectors:
                    try:
                        if await page.locator(sel).first.is_visible(timeout=1000):
                            has_content = True
                            break
                    except Exception:
                        pass
                if not has_content:
                    utils.logger.info(
                        f"[dom_validity_check] 无主体内容 DOM → login_required: {url}"
                    )
                    return ValidityStatus.INVALID_LOGIN_REQUIRED

        return ValidityStatus.VALID

    except Exception as e:
        utils.logger.warning(f"[dom_validity_check] 异常: {url} → {e}")
        return ValidityStatus.VALID  # 不确定时不误判


def check_api_json_validity(platform: str, raw_json: Any) -> ValidityStatus:
    """
    第三层：从 API 返回的 JSON 中检查作品状态字段。
    各平台接口在作品失效时通常返回空数据或特定状态码。
    """
    if raw_json is None:
        return ValidityStatus.INVALID_DELETED

    if isinstance(raw_json, dict):
        # 抖音：aweme_detail 为空或 status.private_status != 0
        if platform == "dy":
            status = raw_json.get("status", {})
            if isinstance(status, dict):
                private_status = status.get("private_status", 0)
                if private_status != 0:
                    return ValidityStatus.INVALID_PRIVATE
                is_delete = status.get("is_delete", False)
                if is_delete:
                    return ValidityStatus.INVALID_DELETED

        # 快手：visionVideoDetail.photo == null
        elif platform == "ks":
            photo = raw_json.get("photo")
            if photo is None:
                return ValidityStatus.INVALID_DELETED

        # B站：通常抛异常（code != 0）在 client 层就会报错
        elif platform == "bili":
            if not raw_json or raw_json.get("View") is None:
                return ValidityStatus.INVALID_DELETED

        # 微博：返回空 dict 或 mblog 为空
        elif platform == "wb":
            if not raw_json or raw_json.get("mblog") is None:
                return ValidityStatus.INVALID_DELETED

        # 头条：SSR JSON 中包含错误标记或内容为空
        elif platform == "toutiao":
            # 如果 JSON 中包含明确的错误标识
            if raw_json.get("code") and raw_json.get("code") != 0:
                return ValidityStatus.INVALID_DELETED
            # SSR 数据中如果有 is_deleted / status 字段
            if raw_json.get("is_deleted") or raw_json.get("status") == "deleted":
                return ValidityStatus.INVALID_DELETED
            # 包含错误消息
            msg = str(raw_json.get("message", "") or raw_json.get("msg", ""))
            if any(kw in msg for kw in ("不存在", "已删除", "下架", "已过期")):
                return ValidityStatus.INVALID_DELETED

    return ValidityStatus.VALID


def _classify_keyword(keyword: str) -> ValidityStatus:
    """根据命中的关键词细分失效类型"""
    delete_words = ["删除", "不存在", "不见了", "去哪了"]
    private_words = ["作者设置", "博主设置", "私密"]
    violation_words = ["违规", "不适宜", "下架", "审核"]

    for w in delete_words:
        if w in keyword:
            return ValidityStatus.INVALID_DELETED
    for w in private_words:
        if w in keyword:
            return ValidityStatus.INVALID_PRIVATE
    for w in violation_words:
        if w in keyword:
            return ValidityStatus.INVALID_VIOLATION
    return ValidityStatus.INVALID_UNKNOWN


def _get_content_selectors(platform: str) -> list:
    """各平台的内容主体 DOM 选择器，用于检测"需登录"场景"""
    return {
        "dy": ["video", '[class*="video-player"]', '[class*="xg-video-container"]'],
        "bili": ["video", ".bilibili-player-video", "#bilibili-player"],
        "ks": ["video", '[class*="video-player"]', ".player-container"],
        "toutiao": ["article", ".article-content", "video"],
        "xhs": [".note-content", '[class*="note"]', ".content-container"],
        "wb": [".weibo-text", ".Feed_body", '[class*="wbpro-feed"]'],
    }.get(platform, [])


def is_invalid(status: ValidityStatus) -> bool:
    """判断状态是否为无效"""
    return status not in (ValidityStatus.VALID, ValidityStatus.NEED_BROWSER)
