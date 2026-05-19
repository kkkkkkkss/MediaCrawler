# -*- coding: utf-8 -*-
# 今日头条举报脚本（支持游客+Cookie两种模式）
# 操作流程：打开文章/视频页 -> hover更多 -> 点举报 -> 选理由 -> 截图(pre) -> 提交 -> 截图(post)

import asyncio

from playwright.async_api import Page

from media_platform.report.base_report import BaseReport, ReportContext
from tools import utils


class ToutiaoReport(BaseReport):
    platform = "toutiao"
    platform_name = "今日头条"

    async def execute(self, url, cookie_str, cookie_id, reason_text, description, task_id):
        """头条：无 Cookie 时以游客身份举报"""
        if not cookie_str:
            cookie_id = "guest"
        return await super().execute(
            url=url, cookie_str=cookie_str, cookie_id=cookie_id,
            reason_text=reason_text, description=description, task_id=task_id,
        )

    async def _do_report(self, page: Page, reason_text: str, description: str, ctx: ReportContext):
        await asyncio.sleep(2)

        # 滚动使底部操作栏可见
        await page.mouse.wheel(0, 600)
        await asyncio.sleep(1)

        # hover more-wrapper 触发弹出菜单（头条的 ... 按钮是 hover 触发）
        report_clicked = False
        try:
            more_wrapper = page.locator('.more-wrapper').first
            if await more_wrapper.is_visible(timeout=3000):
                await more_wrapper.hover()
                await asyncio.sleep(0.8)

                report_btn = page.locator('button.report-button').first
                if await report_btn.is_visible(timeout=2000):
                    await report_btn.click()
                    report_clicked = True
                    utils.logger.info("[Report-toutiao] 点击举报按钮成功: button.report-button")
        except Exception as e:
            utils.logger.warning(f"[Report-toutiao] more-wrapper 方式失败: {e}")

        # 兜底：直接尝试点击含"举报"文字的按钮
        if not report_clicked:
            fallback_selectors = [
                'button:has-text("举报")',
                'text=举报',
                'a:has-text("举报")',
            ]
            for sel in fallback_selectors:
                try:
                    loc = page.locator(sel).first
                    if await loc.is_visible(timeout=2000):
                        await loc.click()
                        report_clicked = True
                        utils.logger.info(f"[Report-toutiao] 点击举报按钮成功(兜底): {sel}")
                        break
                except Exception:
                    continue

        if not report_clicked:
            raise Exception("未找到举报按钮")

        await asyncio.sleep(1.5)

        # 选择举报理由
        reason_clicked = False
        try:
            reason_loc = page.get_by_text(reason_text, exact=False).first
            if await reason_loc.is_visible(timeout=3000):
                await reason_loc.click()
                reason_clicked = True
                utils.logger.info(f"[Report-toutiao] 选择理由成功: {reason_text}")
        except Exception:
            pass

        if not reason_clicked:
            utils.logger.warning(f"[Report-toutiao] 未找到理由'{reason_text}'，尝试选'与事实不符'")
            try:
                alt = page.get_by_text("与事实不符", exact=False).first
                if await alt.is_visible(timeout=2000):
                    await alt.click()
                    reason_clicked = True
            except Exception:
                pass

        await asyncio.sleep(0.5)

        # 填写补充说明
        if description:
            try:
                textarea = page.locator('textarea').first
                if await textarea.is_visible(timeout=2000):
                    await textarea.fill(description)
            except Exception:
                pass

        await asyncio.sleep(0.3)

        # 提交前截图
        await self._take_screenshot(page, ctx, "pre")
        utils.logger.info("[Report-toutiao] 提交前截图完成")

        # 点击"确认"提交
        submit_selectors = [
            'button:has-text("确认")',
            'text=确认',
            'button:has-text("提交")',
        ]
        for sel in submit_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=2000):
                    await loc.click()
                    utils.logger.info("[Report-toutiao] 提交举报成功")
                    break
            except Exception:
                continue

        # 提交后立即截图（不等长 sleep，防止成功弹窗消失来不及截图）
        await asyncio.sleep(0.5)
        await self._take_screenshot(page, ctx, "post")
        utils.logger.info("[Report-toutiao] 提交后截图完成")
