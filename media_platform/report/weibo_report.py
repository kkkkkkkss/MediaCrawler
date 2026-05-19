# -*- coding: utf-8 -*-
# 微博投诉脚本
# 操作流程（基于用户指定路线）：
#   1. 等页面加载 → 点击微博正文区"更多"按钮
#   2. 在下拉菜单中点击"投诉"
#   3. 投诉页可能在新标签页打开 → 切换
#   4. 选择投诉类型："时政有害信息"
#   5. 选择具体原因："其他有害信息"
#   6. 如出现"继续投诉"按钮则点击
#   7. 勾选"我已阅读《微博投诉操作细则》，确认此内容属于时政有害信息。"
#   8. 截图(pre)
#   9. 点击提交
#  10. 截图(post)

import asyncio

from playwright.async_api import Page

from media_platform.report.base_report import BaseReport, ReportContext
from tools import utils


class WeiboReport(BaseReport):
    platform = "wb"
    platform_name = "微博"

    async def _do_report(self, page: Page, reason_text: str, description: str, ctx: ReportContext):
        await asyncio.sleep(2)

        # Step 1: 等微博正文加载
        try:
            await page.wait_for_selector('header, article, [class*="Feed"]', timeout=8000)
        except Exception:
            utils.logger.warning("[Report-wb] 等待微博内容超时，继续")

        # Step 2: 找并点击"更多"按钮
        more_clicked = False
        try:
            more_loc = page.locator('.woo-pop-wrap[class*="_feed_"]').first
            if await more_loc.is_visible(timeout=5000):
                await more_loc.click()
                more_clicked = True
        except Exception:
            pass

        if not more_clicked:
            try:
                more_loc = page.locator('div[class*="_more_"]').first
                if await more_loc.is_visible(timeout=3000):
                    await more_loc.click()
                    more_clicked = True
            except Exception:
                pass

        if not more_clicked:
            raise Exception("未找到微博更多按钮")

        utils.logger.info("[Report-wb] 点击更多按钮成功")
        await asyncio.sleep(1.5)

        # Step 3: 点击下拉菜单中的"投诉"
        complaint_clicked = False
        try:
            loc = page.locator('.woo-pop-item-main:has-text("投诉")').first
            if await loc.is_visible(timeout=3000):
                await loc.click()
                complaint_clicked = True
        except Exception:
            pass

        if not complaint_clicked:
            complaint_clicked = await page.evaluate('''() => {
                let items = document.querySelectorAll('.woo-pop-item-main, [class*="pop"] [class*="item"]');
                for (let item of items) {
                    if (item.textContent.trim() === '投诉' || item.textContent.trim() === '举报') {
                        item.click();
                        return true;
                    }
                }
                return false;
            }''')

        if not complaint_clicked:
            raise Exception("下拉菜单中未找到投诉/举报选项")

        utils.logger.info("[Report-wb] 点击投诉按钮")
        await asyncio.sleep(3)

        # Step 4: 投诉页可能在新标签页打开
        if len(page.context.pages) > 1:
            page = page.context.pages[-1]
            await asyncio.sleep(3)
            utils.logger.info(f"[Report-wb] 切换到投诉页面: {page.url[:80]}")

        # Step 5: 选择投诉类型 "时政有害信息"
        type_clicked = False
        type_text = "时政有害信息"
        for _ in range(3):
            try:
                loc = page.get_by_text(type_text, exact=False).first
                if await loc.is_visible(timeout=5000):
                    await loc.click()
                    type_clicked = True
                    utils.logger.info(f"[Report-wb] 选择投诉类型: {type_text}")
                    break
            except Exception:
                await asyncio.sleep(1)

        if not type_clicked:
            type_clicked = await page.evaluate('''(targetText) => {
                let items = document.querySelectorAll('li, label, span, div, a');
                for (let item of items) {
                    let text = item.textContent.trim();
                    if (text === targetText || text.includes(targetText)) {
                        item.click();
                        return true;
                    }
                }
                return false;
            }''', type_text)
            if type_clicked:
                utils.logger.info(f"[Report-wb] JS兜底选择投诉类型: {type_text}")

        if not type_clicked:
            utils.logger.warning("[Report-wb] 未能选择投诉类型")

        await asyncio.sleep(1.5)

        # Step 6: 选择具体原因 "其他有害信息"
        reason_text_wb = "其他有害信息"
        reason_clicked = False
        try:
            loc = page.get_by_text(reason_text_wb, exact=False).first
            if await loc.is_visible(timeout=5000):
                await loc.click()
                reason_clicked = True
                utils.logger.info(f"[Report-wb] 选择具体原因: {reason_text_wb}")
        except Exception:
            pass

        if not reason_clicked:
            reason_clicked = await page.evaluate('''(targetText) => {
                let items = document.querySelectorAll('li, label, span, div, a');
                for (let item of items) {
                    let text = item.textContent.trim();
                    if (text === targetText || text.includes(targetText)) {
                        item.click();
                        return true;
                    }
                }
                return false;
            }''', reason_text_wb)
            if reason_clicked:
                utils.logger.info(f"[Report-wb] JS兜底选择原因: {reason_text_wb}")

        if not reason_clicked:
            utils.logger.warning("[Report-wb] 未能选择具体原因")

        await asyncio.sleep(1.5)

        # Step 7: 如果出现"继续投诉"按钮则点击（部分投诉类型需要此步骤）
        for kw in ['继续投诉', '下一步', '继续']:
            try:
                loc = page.locator(f'button:has-text("{kw}"), a:has-text("{kw}"), span:has-text("{kw}")').first
                if await loc.is_visible(timeout=2000):
                    await loc.click()
                    utils.logger.info(f"[Report-wb] 点击: {kw}")
                    await asyncio.sleep(2)
                    break
            except Exception:
                continue

        # Step 8: 精准勾选"操作细则"协议 checkbox（不是"拉黑"的 checkbox）
        # 微博使用自定义 checkbox，需要：设置 checked + 触发 change 事件 + 点击可见包裹元素
        checkbox_checked = False

        # 策略1: Playwright 原生 check()，通过 label 文本定位 checkbox
        try:
            # 找到包含"操作细则"的 label，点击整个 label 区域来触发 checkbox
            label_loc = page.locator('label:has-text("操作细则"), label:has-text("时政有害")').last
            if await label_loc.is_visible(timeout=3000):
                await label_loc.click()
                checkbox_checked = True
                utils.logger.info("[Report-wb] 点击 label 勾选操作细则")
        except Exception:
            pass

        if not checkbox_checked:
            # 策略2: JS 强制设置 checked + 触发原生事件，确保框架感知变化
            checkbox_checked = await page.evaluate('''() => {
                let checkboxes = document.querySelectorAll('input[type="checkbox"]');
                let targetCb = null;
                
                // 找到"操作细则"相关的 checkbox（通过遍历附近文本）
                for (let cb of checkboxes) {
                    let container = cb.closest('label') || cb.parentElement;
                    if (!container) continue;
                    let text = container.textContent || '';
                    if (text.includes('操作细则') || text.includes('时政有害')) {
                        targetCb = cb;
                        break;
                    }
                }
                
                // 兜底：取最后一个 checkbox（通常是协议 checkbox）
                if (!targetCb && checkboxes.length >= 2) {
                    targetCb = checkboxes[checkboxes.length - 1];
                }
                if (!targetCb && checkboxes.length === 1) {
                    targetCb = checkboxes[0];
                }
                
                if (!targetCb) return '';
                
                // 强制勾选并触发完整事件链
                targetCb.checked = true;
                targetCb.dispatchEvent(new Event('click', {bubbles: true}));
                targetCb.dispatchEvent(new Event('change', {bubbles: true}));
                targetCb.dispatchEvent(new Event('input', {bubbles: true}));
                
                // 同时点击可见的包裹元素
                let wrapper = targetCb.closest('label') || targetCb.parentElement;
                if (wrapper && wrapper !== document.body) {
                    wrapper.click();
                }
                
                return targetCb.checked ? 'forced_check' : 'failed';
            }''')
            if checkbox_checked and checkbox_checked != 'failed':
                utils.logger.info(f"[Report-wb] JS强制勾选操作细则 (方式: {checkbox_checked})")

        if not checkbox_checked:
            utils.logger.warning("[Report-wb] 未找到协议复选框")

        await asyncio.sleep(1)

        # Step 9: 提交前截图
        await self._take_screenshot(page, ctx, "pre")
        utils.logger.info("[Report-wb] 提交前截图完成")

        # Step 10: 点击提交
        submitted = False
        for kw in ['提交', '确定', '提交投诉']:
            try:
                btn = page.locator(f'button:has-text("{kw}"), a:has-text("{kw}"), input[value="{kw}"]').first
                if await btn.is_visible(timeout=3000):
                    await btn.click()
                    submitted = True
                    utils.logger.info(f"[Report-wb] 提交投诉: {kw}")
                    break
            except Exception:
                continue

        if not submitted:
            submitted = await page.evaluate('''() => {
                let btns = document.querySelectorAll('button, a, input[type="submit"]');
                for (let b of btns) {
                    let text = (b.textContent || b.value || '').trim();
                    if (text.includes('提交') && !b.disabled) {
                        b.click();
                        return true;
                    }
                }
                return false;
            }''')

        if not submitted:
            utils.logger.warning("[Report-wb] 未找到提交按钮")

        # Step 11: 提交后等待结果页面加载再截图
        await asyncio.sleep(2)
        await self._take_screenshot(page, ctx, "post")
        utils.logger.info("[Report-wb] 提交后截图完成")

        await asyncio.sleep(1)
