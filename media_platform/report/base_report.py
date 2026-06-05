# -*- coding: utf-8 -*-
# 举报操作基类
# 封装 Cookie 注入、反检测脚本、截图存档、超时控制、重试、错误兜底等通用逻辑
# 各平台继承此类，仅需实现 _do_report() 方法

import asyncio
import hashlib
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from playwright.async_api import BrowserContext, Page, async_playwright

import config
from tools import utils


@dataclass
class ReportContext:
    """传递给 _do_report 的上下文，子类用它调用截图等基类方法"""
    task_id: str
    url: str
    cookie_id: str


@dataclass
class ReportResult:
    """单次举报操作的结果"""
    url: str
    platform: str
    cookie_id: str
    reason: str
    success: bool = False
    error_msg: str = ""
    screenshot_pre_path: str = ""   # 提交前截图
    screenshot_post_path: str = ""  # 提交后截图
    elapsed_sec: float = 0


class BaseReport:
    """
    举报操作基类。
    子类只需实现 _do_report(page, reason_text, description, ctx) 方法，
    基类负责浏览器生命周期、Cookie 注入、截图、超时、重试、错误处理。
    """

    platform: str = ""
    platform_name: str = ""

    _stealth_js = os.path.join(os.getcwd(), "libs", "stealth.min.js")

    _user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    async def execute(
        self,
        url: str,
        cookie_str: str,
        cookie_id: str,
        reason_text: str,
        description: str,
        task_id: str,
    ) -> ReportResult:
        """
        完整举报流程：启动浏览器 -> 注入 Cookie -> 导航 -> 举报(含重试) -> 关闭。
        截图由子类在 _do_report 中按时机调用 _take_screenshot。
        """
        result = ReportResult(
            url=url, platform=self.platform,
            cookie_id=cookie_id, reason=reason_text,
        )
        ctx = ReportContext(task_id=task_id, url=url, cookie_id=cookie_id)
        pw = None
        context = None
        page = None
        start = time.time()

        try:
            pw = await async_playwright().start()
            headless = getattr(config, "REPORT_HEADLESS", True)
            timeout_sec = getattr(config, "REPORT_TIMEOUT_SEC", 30)
            retry_count = getattr(config, "REPORT_RETRY_COUNT", 1)

            context = await self._create_context(pw, headless, cookie_str)
            page = await context.new_page()
            page.set_default_timeout(timeout_sec * 1000)

            utils.logger.info(
                f"[Report-{self.platform}] 开始举报: cookie={cookie_id} url={url}"
            )

            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_sec * 1000)
            await asyncio.sleep(3)

            # 页面加载后立即检测是否为失效/已删除作品，避免浪费时间尝试举报
            # 传入原始URL用于跳转检测（快手无效链接会跳转到其他视频）
            invalid_reason = await self._check_page_invalid(page, url)
            if invalid_reason:
                result.error_msg = f"作品已失效/不存在（{invalid_reason}），无法投诉"
                utils.logger.warning(
                    f"[Report-{self.platform}] 链接失效: cookie={cookie_id} url={url} reason={invalid_reason}"
                )
                return result

            # 重试循环：首次执行 + retry_count 次重试
            last_error = None
            for attempt in range(retry_count + 1):
                try:
                    await self._do_report(page, reason_text, description, ctx)
                    result.success = True
                    last_error = None
                    break
                except Exception as e:
                    last_error = e
                    if attempt < retry_count:
                        utils.logger.warning(
                            f"[Report-{self.platform}] 第{attempt+1}次失败({e})，刷新页面重试..."
                        )
                        await page.reload(wait_until="domcontentloaded", timeout=timeout_sec * 1000)
                        await asyncio.sleep(3)
                    else:
                        raise

            # 子类在 _do_report 中已调用截图，把路径写回 result
            self._collect_screenshot_paths(result, ctx)

            utils.logger.info(
                f"[Report-{self.platform}] 举报成功: cookie={cookie_id} url={url}"
            )

        except asyncio.TimeoutError:
            result.error_msg = "操作超时"
            utils.logger.warning(
                f"[Report-{self.platform}] 举报超时: cookie={cookie_id} url={url}"
            )
        except Exception as e:
            result.error_msg = str(e)[:500]
            utils.logger.error(
                f"[Report-{self.platform}] 举报异常: cookie={cookie_id} url={url} err={e}"
            )
        finally:
            # 失败时补一张错误现场截图
            if not result.success and page:
                try:
                    fail_path = await self._take_screenshot(page, ctx, "fail")
                    if not result.screenshot_post_path:
                        result.screenshot_post_path = fail_path
                except Exception as e:
                    utils.logger.warning(f"[Report-{self.platform}] 失败截图异常: {e}")

            # 收集可能在 _do_report 中已拍的截图路径
            self._collect_screenshot_paths(result, ctx)

            try:
                if context:
                    await context.close()
                if pw:
                    await pw.stop()
            except Exception:
                pass

            result.elapsed_sec = round(time.time() - start, 2)

        return result

    async def _do_report(self, page: Page, reason_text: str, description: str, ctx: ReportContext):
        """
        子类必须实现：在已打开的页面上执行举报操作。
        子类应在提交前调用 self._take_screenshot(page, ctx, "pre")，
        提交后调用 self._take_screenshot(page, ctx, "post")。
        """
        raise NotImplementedError

    async def _check_page_invalid(self, page: Page, original_url: str = "") -> str:
        """
        检测页面是否为失效/已删除作品。
        返回空字符串表示页面正常，返回非空字符串表示失效原因。
        子类可覆写以适配各自平台的失效页面特征。
        """
        import re

        current_url = page.url

        # 检测1：URL跳转检测
        # 快手无效链接会302跳转到其他有效视频或首页
        if original_url:
            # 短链（如 /f/xxx）会302跳转到真实URL，不能用ID包含检查
            is_short_link = bool(re.search(r'/f/[a-zA-Z0-9_-]+', original_url))

            if not is_short_link:
                original_id = ""
                id_patterns = [
                    r'/short-video/([a-zA-Z0-9_-]+)',  # 快手
                    r'/video/(\d+)',  # 抖音
                    r'/detail/(\d+)',  # 微博
                    r'/video/(BV\w+)',  # B站
                    r'/i(\d+)',  # 头条
                ]
                for pat in id_patterns:
                    m = re.search(pat, original_url)
                    if m:
                        original_id = m.group(1)
                        break

                if original_id:
                    if original_id not in current_url:
                        return f"链接已跳转（原ID:{original_id}不在当前URL中）"

        # 检测2：常见的跳转目标页面（说明原链接无效被重定向了）
        redirect_indicators = [
            '/discover',  # 快手/抖音跳转到发现页
            'kuaishou.com/new-reco',  # 快手推荐页
            'kuaishou.com/?isHome',  # 快手首页
        ]
        for indicator in redirect_indicators:
            if indicator in current_url:
                return f"链接已跳转至{current_url}（原链接无效）"

        # 检测3：通用失效关键词检测（覆盖大多数平台的删除/下架提示）
        invalid_msg = await page.evaluate('''() => {
            let body = document.body ? document.body.innerText : '';

            // 快手特殊处理1：无效视频页面返回 JSON {"result":2} 而非正常HTML（headless模式）
            if (body.trim().startsWith('{') && body.includes('"result":2')) {
                return '快手接口返回result:2（作品不存在）';
            }
            try {
                let json = JSON.parse(body.trim());
                if (json && json.result && json.result !== 1 && json.result !== 0) {
                    return '快手接口返回错误码result:' + json.result;
                }
            } catch(e) {}

            // 快手特殊处理2：无效视频显示倒计时"Ns后，播放其他精彩视频"
            if (/\\d+s后.*播放其他/.test(body)) {
                return '快手视频已失效（显示倒计时跳转）';
            }

            let keywords = [
                '作品不存在', '视频不存在', '内容已删除', '页面不存在',
                '视频不见了', '该作品已被删除', '该内容已被删除',
                '该视频已删除', '作品已失效', '内容不存在',
                '抱歉，页面无法访问', '该页面不存在',
                '作品审核中', '内容违规', '已被下架',
                '该作品可能涉嫌违规', '播放量/评论等信息不可见',
                '该内容暂时无法查看', '内容已下线'
            ];
            for (let kw of keywords) {
                if (body.includes(kw)) return kw;
            }
            return '';
        }''')
        return invalid_msg or ""

    def _collect_screenshot_paths(self, result: ReportResult, ctx: ReportContext):
        """从截图目录中收集 pre/post 路径写回 result"""
        screenshot_dir = os.path.join("data", "report_screenshots", ctx.task_id)
        if not os.path.exists(screenshot_dir):
            return
        url_hash = self._url_hash(ctx.url)
        prefix = f"{self.platform}_{ctx.cookie_id}_{url_hash}"
        for f in os.listdir(screenshot_dir):
            if not f.startswith(prefix):
                continue
            path = os.path.join(screenshot_dir, f)
            if "_pre.png" in f and not result.screenshot_pre_path:
                result.screenshot_pre_path = path
            elif "_post.png" in f and not result.screenshot_post_path:
                result.screenshot_post_path = path
            elif "_fail.png" in f and not result.screenshot_post_path:
                result.screenshot_post_path = path

    async def _create_context(
        self, pw, headless: bool, cookie_str: str
    ) -> BrowserContext:
        """创建浏览器上下文并注入 Cookie + 反检测脚本"""
        context = await pw.chromium.launch_persistent_context(
            user_data_dir="",
            headless=headless,
            viewport={"width": 1280, "height": 800},
            user_agent=self._user_agent,
        )

        if os.path.exists(self._stealth_js):
            await context.add_init_script(path=self._stealth_js)

        await context.add_init_script("""
            Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
            Object.defineProperty(navigator, 'userAgentData', {
                get: () => ({ platform: 'Windows', brands: [
                    {brand: 'Chromium', version: '124'},
                    {brand: 'Google Chrome', version: '124'},
                ], mobile: false })
            });
        """)

        if cookie_str:
            await self._inject_cookies(context, cookie_str)

        return context

    async def _inject_cookies(self, context: BrowserContext, cookie_str: str):
        """将 'k1=v1; k2=v2' 格式的 Cookie 字符串注入到浏览器上下文"""
        cookies = []
        domain_map = {
            "dy": ".douyin.com",
            "wb": ".weibo.com",
            "ks": ".kuaishou.com",
            "toutiao": ".toutiao.com",
        }
        domain = domain_map.get(self.platform, f".{self.platform}.com")

        for pair in cookie_str.split(";"):
            pair = pair.strip()
            if "=" in pair:
                key, _, val = pair.partition("=")
                cookies.append({
                    "name": key.strip(),
                    "value": val.strip(),
                    "domain": domain,
                    "path": "/",
                })
        if cookies:
            await context.add_cookies(cookies)

    async def _take_screenshot(
        self, page: Page, ctx: ReportContext, suffix: str = "post"
    ) -> str:
        """
        截图并保存。suffix 可选 "pre"(提交前) / "post"(提交后) / "fail"(失败现场)。
        返回保存路径。
        """
        url_hash = self._url_hash(ctx.url)
        screenshot_dir = os.path.join("data", "report_screenshots", ctx.task_id)
        os.makedirs(screenshot_dir, exist_ok=True)

        filename = f"{self.platform}_{ctx.cookie_id}_{url_hash}_{suffix}.png"
        filepath = os.path.join(screenshot_dir, filename)

        await page.screenshot(path=filepath, full_page=False)
        return filepath

    @staticmethod
    def _url_hash(url: str) -> str:
        return hashlib.md5(url.encode()).hexdigest()[:8]
