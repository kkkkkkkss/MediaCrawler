# -*- coding: utf-8 -*-
# 头条/西瓜 API 客户端
# 头条 Web 端使用服务端渲染，作品详情可通过页面接口或 SSR 数据获取
# 签名方式相对简单，不需要像抖音那样复杂的 a_bogus

import asyncio
import copy
import json
from typing import Any, Callable, Dict, List, Optional, Union

import httpx
from playwright.async_api import BrowserContext, Page

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
            await self.playwright_page.goto(detail_url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(3)

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
                        // 处理 "1.2万" 格式
                        let m = text.match(/([\d.]+)\\s*万/);
                        if (m) return Math.round(parseFloat(m[1]) * 10000);
                        m = text.match(/([\d.]+)\\s*亿/);
                        if (m) return Math.round(parseFloat(m[1]) * 100000000);
                        m = text.match(/^(\d+)$/);
                        if (m) return parseInt(m[1]);
                        return null;
                    };

                    // 策略1：查找带有特定属性或类名的按钮/元素
                    const allElements = document.querySelectorAll(
                        '[class*="like"], [class*="comment"], [class*="share"], ' +
                        '[class*="digg"], [class*="collect"], [class*="play"], ' +
                        '[class*="read"], [class*="forward"], [class*="favour"], ' +
                        '[class*="count"], [data-log-click]'
                    );
                    for (const el of allElements) {
                        const text = (el.textContent || '').trim();
                        const rawCls = el.className;
                        const cls = (typeof rawCls === 'string' ? rawCls : (rawCls.baseVal || '')).toLowerCase();
                        const num = parseNum(text.replace(/[^\\d.万亿]/g, ''));

                        if (num !== null) {
                            if ((cls.includes('like') || cls.includes('digg'))
                                && result.praise_count === null)
                                result.praise_count = num;
                            else if (cls.includes('comment') && result.reply_count === null)
                                result.reply_count = num;
                            // else if ((cls.includes('share') || cls.includes('forward'))
                            //          && result.share_count === null)
                            //     result.share_count = num;
                            // else if ((cls.includes('play') || cls.includes('read'))
                            //          && result.visit_count === null)
                            //     result.visit_count = num;
                        }
                    }

                    // 策略2：扫描全文匹配"XX评论"、"XX点赞"模式
                    const bodyText = document.body?.innerText || '';
                    const patterns = [
                        {field: 'reply_count', regex: /([\d.]+[万亿]?)\\s*(?:条)?\\s*评论/},
                        {field: 'reply_count', regex: /评论\\s*([\d.]+[万亿]?)/},
                        {field: 'praise_count', regex: /([\d.]+[万亿]?)\\s*(?:个)?\\s*(?:点赞|赞)/},
                        {field: 'praise_count', regex: /(?:点赞|赞)\\s*([\d.]+[万亿]?)/},
                        // {field: 'visit_count', regex: /([\d.]+[万亿]?)\\s*(?:次)?\\s*(?:播放|阅读|浏览)/},
                        // {field: 'visit_count', regex: /(?:播放|阅读|浏览)\\s*([\d.]+[万亿]?)/},
                        // {field: 'share_count', regex: /([\d.]+[万亿]?)\\s*(?:次)?\\s*(?:转发|分享)/},
                        // {field: 'share_count', regex: /(?:转发|分享)\\s*([\d.]+[万亿]?)/},
                    ];
                    for (const {field, regex} of patterns) {
                        if (result[field] !== null) continue;
                        const m = bodyText.match(regex);
                        if (m) {
                            const num = parseNum(m[1]);
                            if (num !== null) result[field] = num;
                        }
                    }

                    // 策略3：UGC/图文分享页底部操作栏（赞 评论 转发 依次排列）
                    const actionBars = document.querySelectorAll(
                        '[class*="action"], [class*="toolbar"], [class*="interact"], ' +
                        '[class*="bottom-bar"], [class*="footer"]'
                    );
                    for (const bar of actionBars) {
                        const spans = bar.querySelectorAll('span, div, a, button');
                        const nums = [];
                        for (const s of spans) {
                            const t = (s.textContent || '').trim();
                            const n = parseNum(t);
                            if (n !== null) nums.push(n);
                        }
                        if (nums.length >= 2) {
                            if (result.praise_count === null) result.praise_count = nums[0];
                            if (result.reply_count === null && nums.length > 1) result.reply_count = nums[1];
                            // if (result.share_count === null && nums.length > 2) result.share_count = nums[2];
                            break;
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
