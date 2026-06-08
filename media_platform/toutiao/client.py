# -*- coding: utf-8 -*-
# 头条/西瓜 API 客户端
# 头条 Web 端使用服务端渲染，作品详情可通过页面接口或 SSR 数据获取
# 签名方式相对简单，不需要像抖音那样复杂的 a_bogus

import asyncio
import copy
import html
import json
import re
from typing import Any, Callable, Dict, List, Optional, Union

import httpx
from playwright.async_api import BrowserContext, Page

import config
from base.base_crawler import AbstractApiClient
from tools import utils
from tools.httpx_util import make_async_client

from .exception import DataFetchError


class ToutiaoClient(AbstractApiClient):
    """
    头条/西瓜视频 API Client。
    头条的 API 签名相对简单：主要依赖 cookie 中的 _signature 和 tt_webid。
    目前采用"通过 Playwright 页面加载获取数据"的方式，兼容性最好。
    """

    def __init__(
        self,
        timeout=30,
        proxy=None,
        *,
        headers: Dict,
        playwright_page: Page,
        cookie_dict: Dict,
    ):
        self.proxy = proxy
        self.timeout = timeout
        self.headers = headers
        self._toutiao_host = "https://www.toutiao.com"
        self._ixigua_host = "https://www.ixigua.com"
        self.playwright_page = playwright_page
        self.cookie_dict = cookie_dict

    async def request(self, method, url, **kwargs) -> Any:
        async with make_async_client(proxy=self.proxy) as client:
            response = await client.request(
                method, url, timeout=self.timeout, **kwargs
            )
        try:
            data = response.json()
            return data
        except Exception as e:
            raise DataFetchError(f"JSON 解析失败: {e}, text={response.text[:200]}")

    async def get(self, uri: str, params: Optional[Dict] = None) -> Any:
        headers = copy.copy(self.headers)
        url = f"{self._toutiao_host}{uri}"
        return await self.request("GET", url, params=params, headers=headers)

    async def pong(self) -> bool:
        """检查头条登录状态（头条不强制登录也能看大部分内容）"""
        return True

    async def update_cookies(self, browser_context: BrowserContext, urls=None):
        cookie_str, cookie_dict = await utils.convert_browser_context_cookies(
            browser_context,
            urls=urls or ["https://www.toutiao.com"],
        )
        self.headers["Cookie"] = cookie_str
        self.cookie_dict = cookie_dict

    async def get_article_info(
        self, item_id: str, original_url: Optional[str] = None
    ) -> Optional[Dict]:
        """
        获取头条文章/视频详情。
        头条的详情数据嵌在页面 SSR 的 JSON 中，通过 Playwright 提取。
        original_url: 非标准链接（zjurl.cn等）直接用原始URL导航
        """
        if original_url:
            detail_url = original_url
        else:
            detail_url = f"https://www.toutiao.com/article/{item_id}/"
        utils.logger.info(f"[ToutiaoClient] 导航到: {detail_url}")
        try:
            # 导航到文章页面（增加等待时间，确保页面完全渲染避免并发时加载不完整）
            # 旧逻辑固定 15s + 3s 等待，代理批量下坏出口会显著拖慢吞吐；改为可配置。
            await self.playwright_page.goto(
                detail_url,
                wait_until="domcontentloaded",
                timeout=getattr(config, "URLCHECK_TOUTIAO_NAV_TIMEOUT_MS", 10000),
            )
            await asyncio.sleep(getattr(config, "URLCHECK_TOUTIAO_AFTER_NAV_SLEEP_SEC", 2))

            # 尝试从页面 SSR 数据中提取 JSON
            # 头条的 SSR 数据通常在 window.__INITIAL_STATE__ 或 INITIAL_PROPS 中
            raw_data = await self.playwright_page.evaluate("""
                () => {
                    // 尝试多种方式提取页面数据
                    if (window.__INITIAL_STATE__) return JSON.stringify(window.__INITIAL_STATE__);
                    if (window.INITIAL_PROPS) return JSON.stringify(window.INITIAL_PROPS);
                    if (window.__NEXT_DATA__) return JSON.stringify(window.__NEXT_DATA__);

                    // 从 script 标签中查找
                    const scripts = document.querySelectorAll('script');
                    for (const script of scripts) {
                        const text = script.textContent || '';
                        if (text.includes('articleInfo') || text.includes('videoInfo')
                            || text.includes('itemInfo')) {
                            // 尝试提取 JSON 对象
                            const match = text.match(/({[\\s\\S]*"itemId"[\\s\\S]*})/);
                            if (match) return match[1];
                        }
                    }
                    return null;
                }
            """)

            if raw_data:
                if isinstance(raw_data, str):
                    return json.loads(raw_data)
                return raw_data

            # 回退方案：尝试直接调 API
            return await self._get_article_info_via_api(item_id)

        except Exception as e:
            utils.logger.error(
                f"[ToutiaoClient] 获取文章详情失败 item_id={item_id}: {e}"
            )
            # 最后尝试 API 方式
            try:
                return await self._get_article_info_via_api(item_id)
            except Exception:
                return None

    async def _get_article_info_via_api(self, item_id: str) -> Optional[Dict]:
        """通过 toutiao API 获取文章信息（备用方案）"""
        try:
            uri = "/api/pc/feed/"
            params = {"category": "article", "utm_source": "toutiao", "max_behot_time": 0}
            # 头条内容详情 API
            detail_uri = f"/article/v4/tab_comments/"
            detail_params = {"aid": 24, "app_name": "toutiao_web", "group_id": item_id, "item_id": item_id, "count": 0}
            res = await self.get(detail_uri, detail_params)
            return res
        except Exception:
            return None

    async def get_mobile_article_state(self, item_id: str) -> Dict[str, Any]:
        """
        通过头条移动端公开页确认内容状态。

        旧逻辑完全依赖桌面端 Playwright 页面，批量代理下容易拿到验证码/空白壳页；
        移动端 info/page 更轻，作为兜底只负责确认 alive/dead，不用于提取互动量。
        """
        timeout = getattr(config, "URLCHECK_TOUTIAO_MOBILE_TIMEOUT_SEC", 12)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                "Mobile/15E148 Safari/604.1"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "application/json;q=0.8,*/*;q=0.7"
            ),
            "Referer": "https://m.toutiao.com/",
        }
        info_url = f"https://m.toutiao.com/i{item_id}/info/"
        page_url = f"https://m.toutiao.com/i{item_id}/"

        try:
            async with make_async_client(
                proxy=self.proxy,
                timeout=timeout,
                follow_redirects=True,
                headers=headers,
            ) as client:
                info_resp = await client.get(info_url)
                try:
                    info_json = info_resp.json()
                except Exception:
                    info_json = {}

                data = info_json.get("data") if isinstance(info_json, dict) else None
                has_content_data = self._has_mobile_content_data(data)
                if isinstance(data, dict) and has_content_data:
                    title = (
                        data.get("title")
                        or data.get("abstract")
                        or data.get("content")
                        or ""
                    )
                    return {
                        "state": "alive",
                        "reason": "移动端 info 接口返回内容数据",
                        "title": self._clean_mobile_title(str(title)),
                        "metrics": self._extract_mobile_metrics(data),
                        "raw_json": info_json,
                    }

                page_resp = await client.get(page_url)
                page_text = page_resp.text or ""
                title = self._extract_mobile_title(page_text)
                generic_title = title in ("", "今日头条")
                final_url = str(page_resp.url)

                if page_resp.status_code == 404 and generic_title:
                    return {
                        "state": "dead",
                        "reason": "移动端页面返回 404 且无内容标题",
                        "title": title,
                        "raw_json": info_json,
                    }

                if isinstance(data, dict) and data and not has_content_data and generic_title:
                    return {
                        "state": "dead",
                        "reason": "移动端 info 为空壳且页面无内容标题",
                        "title": title,
                        "raw_json": info_json,
                    }

                if page_resp.status_code == 200 and not generic_title:
                    return {
                        "state": "alive",
                        "reason": "移动端页面返回内容标题",
                        "title": title,
                        "metrics": self._extract_mobile_metrics(data if isinstance(data, dict) else {}),
                        "final_url": final_url,
                        "raw_json": info_json,
                    }

                # info 为空但页面也不是明确 404 时，保守交还桌面端/异常路径处理。
                return {
                    "state": "unknown",
                    "reason": (
                        f"移动端无法确认 status={page_resp.status_code}, "
                        f"title={title or '-'}"
                    ),
                    "title": title,
                    "final_url": final_url,
                    "raw_json": info_json,
                }
        except Exception as e:
            utils.logger.warning(
                f"[ToutiaoClient] 移动端状态确认失败 item_id={item_id}: {e}"
            )
            return {"state": "unknown", "reason": f"移动端确认异常: {e}"}

    @staticmethod
    def _extract_mobile_title(page_text: str) -> str:
        match = re.search(r"<title[^>]*>(.*?)</title>", page_text or "", re.I | re.S)
        if not match:
            return ""
        return ToutiaoClient._clean_mobile_title(match.group(1))

    @staticmethod
    def _clean_mobile_title(title: str) -> str:
        cleaned = html.unescape(re.sub(r"<[^>]+>", "", title or ""))
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned.replace(" - 今日头条", "").strip()

    @staticmethod
    def _has_mobile_content_data(data: Any) -> bool:
        """
        判断移动端 info 是否真的包含公开内容。

        旧逻辑只要 data 非空就判有效；头条会对部分不可见/下架内容返回 group_source
        等空壳字段，导致“无效但返回有效”。这里要求有标题、正文、URL 或微头条正文证据。
        """
        if not isinstance(data, dict) or not data:
            return False
        if any(data.get(field) for field in ("title", "content", "abstract", "url")):
            return True
        thread_base = ((data.get("thread") or {}).get("thread_base") or {})
        if isinstance(thread_base, dict) and any(
            thread_base.get(field) for field in ("title", "content", "share_url")
        ):
            return True
        return False

    @classmethod
    def _extract_mobile_metrics(cls, data: Dict[str, Any]) -> Dict[str, Optional[int]]:
        """
        从移动端 info 数据提取转赞评读数。

        旧逻辑只从桌面 DOM 读互动量，代理出口一旦返回登录页就全空；
        移动端公开接口已返回同类字段时优先复用，降低 `both` 模式对桌面页的依赖。
        """
        action = (
            ((data.get("thread") or {}).get("thread_base") or {}).get("action")
            if isinstance(data.get("thread"), dict)
            else {}
        ) or {}

        praise = cls._to_int(data.get("digg_count"))
        reply = cls._to_int(data.get("comment_count"))
        visit = cls._to_int(data.get("impression_count"))
        share = cls._to_int(data.get("repost_count"))

        if praise is None:
            praise = cls._to_int(action.get("digg_count"))
        if reply is None:
            reply = cls._to_int(action.get("comment_count"))
        if visit is None:
            visit = cls._to_int(action.get("read_count")) or cls._to_int(action.get("play_count"))
        if share is None:
            share = cls._to_int(action.get("forward_count"))

        return {
            "praise_count": praise,
            "reply_count": reply,
            "visit_count": visit,
            "share_count": share,
        }

    @staticmethod
    def _to_int(value: Any) -> Optional[int]:
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return int(value)
        text = str(value).strip().replace(",", "")
        multiplier = 1
        if text.endswith("万"):
            multiplier = 10000
            text = text[:-1]
        elif text.endswith("亿"):
            multiplier = 100000000
            text = text[:-1]
        try:
            return int(float(text) * multiplier)
        except (TypeError, ValueError):
            return None

    async def get_article_metrics_from_dom(
        self, item_id: str, page: Optional[Page] = None,
        original_url: Optional[str] = None,
    ) -> Dict[str, Optional[int]]:
        """
        从头条文章/视频页面 DOM 中提取转赞评指标。
        头条的 SSR 数据不一定包含指标字段，通过解析页面可见文本提取。
        """
        target_page = page or self.playwright_page
        detail_url = original_url or f"https://www.toutiao.com/article/{item_id}/"
        result: Dict[str, Optional[int]] = {
            "praise_count": None,
            "reply_count": None,
            "visit_count": None,
            "share_count": None,
        }
        try:
            current_url = target_page.url
            if item_id not in current_url:
                await target_page.goto(detail_url, wait_until="domcontentloaded")
                await asyncio.sleep(4)
            else:
                # 已在目标页，等待动态渲染完成
                await asyncio.sleep(2)

            metrics = await target_page.evaluate("""
                () => {
                    const result = {praise_count: null, reply_count: null,
                                    visit_count: null, share_count: null};
                    const parseNum = (text) => {
                        if (!text) return null;
                        text = text.trim().replace(/,/g, '');
                        let m = text.match(/([\d.]+)\\s*万/);
                        if (m) return Math.round(parseFloat(m[1]) * 10000);
                        m = text.match(/([\d.]+)\\s*亿/);
                        if (m) return Math.round(parseFloat(m[1]) * 100000000);
                        m = text.match(/(\d+)/);
                        if (m) return parseInt(m[1]);
                        return null;
                    };

                    /* ────────────────────────────────────
                     * 策略1：精确类名选择器（视频页 & 图文页共用）
                     * 直接取 .like-count / .comment-count / .favour-count / .views-count
                     * 这些是头条互动栏的固定结构，不会误匹配其他区域
                     * ──────────────────────────────────── */
                    const exactMap = [
                        {sel: '.like-count',    field: 'praise_count'},
                        {sel: '.comment-count', field: 'reply_count'},
                        {sel: '.favour-count',  field: 'share_count'},
                        {sel: '.views-count',   field: 'visit_count'},
                    ];
                    for (const {sel, field} of exactMap) {
                        const el = document.querySelector(sel);
                        if (el) {
                            const num = parseNum(el.textContent);
                            // 文本是纯标签（"赞"/"评论"/"收藏"）且无数字时，视为0
                            result[field] = (num !== null) ? num : 0;
                        }
                    }

                    /* ────────────────────────────────────
                     * 策略2：actions-list 结构（视频页 ul.actions-list）
                     * 按钮顺序固定: 点赞→评论→收藏
                     * ──────────────────────────────────── */
                    if (result.praise_count === null) {
                        const actionsList = document.querySelector('ul.actions-list');
                        if (actionsList) {
                            const buttons = actionsList.querySelectorAll('.action-item');
                            const fields = ['praise_count', 'reply_count', 'share_count'];
                            buttons.forEach((btn, i) => {
                                if (i < fields.length && result[fields[i]] === null) {
                                    const num = parseNum(btn.textContent);
                                    result[fields[i]] = (num !== null) ? num : 0;
                                }
                            });
                        }
                    }

                    /* ────────────────────────────────────
                     * 策略3：图文页 article-interaction 区域
                     * 类名含 digg/comment/share 但必须在互动栏容器内
                     * 排除作者信息区（含"粉丝"字样的父元素）
                     * ──────────────────────────────────── */
                    if (result.praise_count === null) {
                        const interactBars = document.querySelectorAll(
                            '[class*="interact"], [class*="article-bar"], [class*="bottom-bar"]'
                        );
                        for (const bar of interactBars) {
                            if ((bar.textContent || '').includes('粉丝')) continue;
                            const items = bar.querySelectorAll('button, a, span, div');
                            for (const item of items) {
                                const rawCls = item.className;
                                const cls = (typeof rawCls === 'string'
                                    ? rawCls : (rawCls.baseVal || '')).toLowerCase();
                                const num = parseNum(item.textContent);
                                const val = (num !== null) ? num : 0;
                                if ((cls.includes('digg') || cls.includes('like'))
                                    && result.praise_count === null)
                                    result.praise_count = val;
                                else if (cls.includes('comment')
                                    && result.reply_count === null)
                                    result.reply_count = val;
                            }
                        }
                    }

                    /* ────────────────────────────────────
                     * 策略4："播放 NNN" 文本提取（视频页播放量）
                     * ──────────────────────────────────── */
                    if (result.visit_count === null) {
                        const bodyText = document.body?.innerText || '';
                        const m = bodyText.match(/播放\\s*([\d.]+[万亿]?)/);
                        if (m) {
                            const num = parseNum(m[1]);
                            if (num !== null) result.visit_count = num;
                        }
                    }

                    return result;
                }
            """)
            if metrics:
                for k, v in metrics.items():
                    if v is not None:
                        result[k] = v

            # 提取标题：优先从 h1 或 document.title 获取
            title = await target_page.evaluate("""
                () => {
                    // h1 标签通常是文章/视频标题
                    const h1 = document.querySelector('h1');
                    if (h1 && h1.textContent.trim().length > 2) return h1.textContent.trim();
                    // 页面 title 去掉尾部的 " - 今日头条"
                    const pageTitle = document.title || '';
                    const cleaned = pageTitle.replace(/ - 今日头条$/, '').replace(/ - 头条搜索$/, '').trim();
                    if (cleaned.length > 2 && cleaned !== '今日头条') return cleaned;
                    return null;
                }
            """)
            if title:
                result["title"] = title

            utils.logger.info(f"[ToutiaoClient] DOM 指标提取: item_id={item_id} → {result}")
        except Exception as e:
            utils.logger.warning(f"[ToutiaoClient] DOM 指标提取失败 item_id={item_id}: {e}")
        return result

    async def get_article_comments(
        self, item_id: str, offset: int = 0, count: int = 20
    ) -> Dict:
        """获取头条文章/视频评论"""
        uri = "/article/v4/tab_comments/"
        params = {
            "aid": 24,
            "app_name": "toutiao_web",
            "group_id": item_id,
            "item_id": item_id,
            "offset": offset,
            "count": count,
        }
        return await self.get(uri, params)

    async def get_all_comments(
        self,
        item_id: str,
        crawl_interval: float = 1.0,
        callback: Optional[Callable] = None,
        max_count: int = 100,
    ) -> List[Dict]:
        """获取文章/视频的所有评论"""
        result = []
        offset = 0
        while len(result) < max_count:
            try:
                res = await self.get_article_comments(item_id, offset=offset)
                if not isinstance(res, dict):
                    utils.logger.warning(f"[ToutiaoClient] 评论接口返回非dict: {type(res)}")
                    break
                data = res.get("data") or {}
                if not isinstance(data, dict):
                    break
                comments = data.get("comments", [])
                if not comments:
                    break
                if len(result) + len(comments) > max_count:
                    comments = comments[:max_count - len(result)]
                result.extend(comments)
                if callback:
                    await callback(item_id, comments)
                offset += len(comments)
                has_more = data.get("has_more", False)
                if not has_more:
                    break
                await asyncio.sleep(crawl_interval)
            except Exception as e:
                utils.logger.error(f"[ToutiaoClient] 评论获取失败: {e}")
                break
        return result
