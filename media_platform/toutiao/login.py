# -*- coding: utf-8 -*-
# 头条登录模块
# 头条大部分公开内容不需要登录即可访问，
# 这里提供基础的登录框架以保持与其他平台一致的接口

from typing import Optional

from playwright.async_api import BrowserContext, Page

from base.base_crawler import AbstractLogin
from tools import utils


class ToutiaoLogin(AbstractLogin):
    """
    头条登录。
    头条的公开文章/视频详情不强制登录即可访问，
    因此 pong() 直接返回 True。
    如果需要评论等功能，可在这里扩展扫码登录。
    """

    def __init__(
        self,
        login_type: str,
        login_phone: str,
        browser_context: BrowserContext,
        context_page: Page,
        cookie_str: str = "",
    ):
        self.login_type = login_type
        self.login_phone = login_phone
        self.browser_context = browser_context
        self.context_page = context_page
        self.cookie_str = cookie_str

    async def begin(self):
        utils.logger.info("[ToutiaoLogin] 头条大部分内容不需要登录")
        if self.login_type == "cookie":
            await self.login_by_cookies()
        elif self.login_type == "qrcode":
            await self.login_by_qrcode()
        else:
            utils.logger.info("[ToutiaoLogin] 跳过登录（头条不强制登录）")

    async def login_by_qrcode(self):
        utils.logger.info("[ToutiaoLogin] 头条扫码登录暂未实现，跳过")

    async def login_by_mobile(self):
        pass

    async def login_by_cookies(self):
        if self.cookie_str:
            for cookie_pair in self.cookie_str.split(";"):
                cookie_pair = cookie_pair.strip()
                if "=" in cookie_pair:
                    name, value = cookie_pair.split("=", 1)
                    await self.browser_context.add_cookies([{
                        "name": name.strip(),
                        "value": value.strip(),
                        "domain": ".toutiao.com",
                        "path": "/",
                    }])
            utils.logger.info("[ToutiaoLogin] Cookie 登录完成")
