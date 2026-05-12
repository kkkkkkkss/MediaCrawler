# -*- coding: utf-8 -*-
# 头条/西瓜视频 Crawler
# 支持 detail 模式获取指定文章/视频信息及评论

import asyncio
import os
from typing import Dict, List, Optional

from playwright.async_api import (
    BrowserContext,
    BrowserType,
    Page,
    Playwright,
    async_playwright,
)

import config
from base.base_crawler import AbstractCrawler
from tools import utils
from tools.cdp_browser import CDPBrowserManager
from var import crawler_type_var

from .client import ToutiaoClient
from .help import parse_article_info_from_url
from .login import ToutiaoLogin


class ToutiaoCrawler(AbstractCrawler):
    context_page: Page
    tt_client: ToutiaoClient
    browser_context: BrowserContext
    cdp_manager: Optional[CDPBrowserManager]

    def __init__(self):
        self.index_url = "https://www.toutiao.com"
        self.cookie_urls = [self.index_url]
        self.cdp_manager = None

    async def start(self):
        async with async_playwright() as playwright:
            if config.ENABLE_CDP_MODE:
                utils.logger.info("[ToutiaoCrawler] 使用 CDP 模式启动浏览器")
                self.browser_context = await self.launch_browser_with_cdp(
                    playwright, None, None, headless=config.CDP_HEADLESS
                )
            else:
                utils.logger.info("[ToutiaoCrawler] 使用标准模式启动浏览器")
                chromium = playwright.chromium
                self.browser_context = await self.launch_browser(
                    chromium, None, None, headless=config.HEADLESS
                )
                await self.browser_context.add_init_script(path="libs/stealth.min.js")

            self.context_page = await self.browser_context.new_page()
            await self.context_page.goto(self.index_url)

            self.tt_client = await self._create_client()

            login_obj = ToutiaoLogin(
                login_type=config.LOGIN_TYPE,
                login_phone="",
                browser_context=self.browser_context,
                context_page=self.context_page,
                cookie_str=config.COOKIES,
            )
            await login_obj.begin()

            crawler_type_var.set(config.CRAWLER_TYPE)
            if config.CRAWLER_TYPE == "detail":
                await self.get_specified_articles()

            utils.logger.info("[ToutiaoCrawler] 完成")

    async def get_specified_articles(self):
        """获取指定文章/视频的详情"""
        for article_url in config.TOUTIAO_SPECIFIED_ID_LIST:
            try:
                info = parse_article_info_from_url(article_url)
                item_id = info.item_id
                utils.logger.info(f"[ToutiaoCrawler] 获取 item_id={item_id}")
                detail = await self.tt_client.get_article_info(item_id)
                if detail:
                    utils.logger.info(f"[ToutiaoCrawler] 成功获取 item_id={item_id} 详情")
                else:
                    utils.logger.warning(f"[ToutiaoCrawler] item_id={item_id} 详情为空")
                await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)
            except Exception as e:
                utils.logger.error(f"[ToutiaoCrawler] 处理失败: {e}")

    async def _create_client(self) -> ToutiaoClient:
        cookie_str, cookie_dict = await utils.convert_browser_context_cookies(
            self.browser_context, urls=self.cookie_urls
        )
        return ToutiaoClient(
            headers={
                "User-Agent": await self.context_page.evaluate("() => navigator.userAgent"),
                "Cookie": cookie_str,
                "Host": "www.toutiao.com",
                "Referer": "https://www.toutiao.com/",
            },
            playwright_page=self.context_page,
            cookie_dict=cookie_dict,
        )

    async def search(self):
        pass

    async def launch_browser(
        self, chromium: BrowserType, playwright_proxy, user_agent, headless=True
    ) -> BrowserContext:
        if config.SAVE_LOGIN_STATE:
            user_data_dir = os.path.join(
                os.getcwd(), "browser_data",
                config.USER_DATA_DIR % "toutiao"
            )
            return await chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                accept_downloads=True,
                headless=headless,
                viewport={"width": 1920, "height": 1080},
                user_agent=user_agent,
            )
        else:
            browser = await chromium.launch(headless=headless)
            return await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=user_agent,
            )

    async def launch_browser_with_cdp(
        self, playwright: Playwright, playwright_proxy, user_agent, headless=True
    ) -> BrowserContext:
        try:
            self.cdp_manager = CDPBrowserManager()
            browser_context = await self.cdp_manager.launch_and_connect(
                playwright=playwright,
                playwright_proxy=playwright_proxy,
                user_agent=user_agent,
                headless=headless,
            )
            await self.cdp_manager.add_stealth_script()
            return browser_context
        except Exception as e:
            utils.logger.error(f"[ToutiaoCrawler] CDP 失败，回退标准模式: {e}")
            chromium = playwright.chromium
            return await self.launch_browser(chromium, playwright_proxy, user_agent, headless)
