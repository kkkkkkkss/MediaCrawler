# -*- coding: utf-8 -*-
# 快手举报脚本
# 操作流程（基于实际 DOM 验证）：
#   1. 等页面加载，找到右侧面板的"举报"按钮（span.item-text）
#   2. 点击"举报"
#   3. 检测登录弹窗（Cookie 过期场景），如是则标记失败
#   4. 弹窗容器: report-container > modal > modal-dialog > modal-body
#      注意 report-container 自身 height=0，需检测子元素 modal-dialog
#   5. 理由使用真正的 radio input（parentClass=pl-radio），通过相邻 label 文字匹配
#   6. 提交前截图(pre) -> 提交 -> 提交后截图(post) -> 验证弹窗关闭

import asyncio

from playwright.async_api import Page

from media_platform.report.base_report import BaseReport, ReportContext
from tools import utils


class KuaishouReport(BaseReport):
    platform = "ks"
    platform_name = "快手"

    async def _do_report(self, page: Page, reason_text: str, description: str, ctx: ReportContext):
        # Step 1: 轮询等待举报按钮出现
        report_found = False
        for _ in range(8):
            exists = await page.evaluate('''() => {
                let spans = document.querySelectorAll('span.item-text');
                for (let s of spans) {
                    if (s.textContent.trim() === '举报') return true;
                }
                return false;
            }''')
            if exists:
                report_found = True
                break
            await asyncio.sleep(1.5)

        if not report_found:
            raise Exception("页面未加载完成：未找到举报按钮")

        # Step 2: 点击举报
        report_btn = page.locator('span.item-text:has-text("举报")').first
        await report_btn.click()
        utils.logger.info("[Report-ks] 点击举报按钮")
        await asyncio.sleep(2)

        # Step 3: 检测登录弹窗（Cookie 过期时快手会弹登录框而非举报框）
        login_modal = await page.evaluate('''() => {
            let modal = document.querySelector('.modal');
            if (modal) {
                let text = modal.textContent || '';
                if (text.includes('登录后举报') || text.includes('登录即可')) return true;
            }
            return false;
        }''')
        if login_modal:
            try:
                close_btn = page.locator('.modal-close').first
                if await close_btn.is_visible(timeout=2000):
                    await close_btn.click()
            except Exception:
                pass
            raise Exception("Cookie已过期/无效，需要重新扫码登录快手")

        # Step 4: 等待举报弹窗（report-container 自身 height=0，检测子元素 modal-dialog）
        dialog_ready = False
        for _ in range(5):
            ready = await page.evaluate('''() => {
                let dialog = document.querySelector('.modal-dialog');
                if (dialog) {
                    let rect = dialog.getBoundingClientRect();
                    let text = dialog.textContent || '';
                    return rect.width > 100 && rect.height > 100 && text.includes('举报');
                }
                return false;
            }''')
            if ready:
                dialog_ready = True
                break
            await asyncio.sleep(1)

        if not dialog_ready:
            raise Exception("举报弹窗未出现")

        utils.logger.info("[Report-ks] 举报弹窗已打开")

        # 等待弹窗内容异步渲染完成（快手 Vue 组件渲染 radio 文字有延迟）
        await asyncio.sleep(2)

        # Step 5: 选择理由 —— 用 innerText（比 textContent 更准确）+ 多重匹配策略
        all_options = await page.evaluate('''() => {
            let dialog = document.querySelector('.modal-dialog');
            if (!dialog) return [];
            // 策略1: .pl-radio 的 innerText
            let radios = dialog.querySelectorAll('.pl-radio');
            let texts = Array.from(radios).map(r => r.innerText.trim()).filter(t => t.length > 0);
            if (texts.length > 0) return texts;
            // 策略2: .report-item-list 内所有含文字的 label/span
            let list = dialog.querySelector('.report-item-list');
            if (list) {
                let items = list.querySelectorAll('label, span, .pl-radio__label');
                texts = Array.from(items).map(i => i.innerText.trim()).filter(t => t.length > 0);
            }
            return texts;
        }''')
        utils.logger.info(f"[Report-ks] 可选理由列表: {all_options}")

        # 多策略理由选择：innerText + textContent + 子元素匹配
        reason_clicked = await page.evaluate('''(reasonText) => {
            let dialog = document.querySelector('.modal-dialog');
            if (!dialog) return '';
            // 策略1: .pl-radio innerText 匹配
            let radios = dialog.querySelectorAll('.pl-radio');
            for (let lbl of radios) {
                let text = lbl.innerText.trim();
                if (text && (text === reasonText || text.includes(reasonText))) {
                    let radio = lbl.querySelector('input[type="radio"]');
                    if (radio) { radio.click(); return text; }
                    lbl.click();
                    return text;
                }
            }
            // 策略2: 所有 label/span 文字匹配后点击最近的 radio
            let allItems = dialog.querySelectorAll('label, span, div');
            for (let item of allItems) {
                let text = item.innerText.trim();
                if (text === reasonText) {
                    let parent = item.closest('.pl-radio') || item.parentElement;
                    if (parent) {
                        let radio = parent.querySelector('input[type="radio"]');
                        if (radio) { radio.click(); return text; }
                        parent.click();
                        return text;
                    }
                    item.click();
                    return text;
                }
            }
            return '';
        }''', reason_text)

        if reason_clicked:
            utils.logger.info(f"[Report-ks] 选择理由成功: {reason_clicked}")
        else:
            # 兜底1: 显式匹配"不实信息"
            reason_clicked = await page.evaluate('''() => {
                let dialog = document.querySelector('.modal-dialog');
                if (!dialog) return '';
                let items = dialog.querySelectorAll('.pl-radio, label, span');
                for (let item of items) {
                    let text = item.innerText.trim();
                    if (text.includes('\u4e0d\u5b9e\u4fe1\u606f')) {
                        let radio = item.querySelector('input[type="radio"]') ||
                                    (item.closest('.pl-radio') || item.parentElement || {}).querySelector &&
                                    (item.closest('.pl-radio') || item.parentElement).querySelector('input[type="radio"]');
                        if (radio) { radio.click(); return text; }
                        item.click();
                        return text;
                    }
                }
                return '';
            }''')
            if reason_clicked:
                utils.logger.warning(f"[Report-ks] 兜底匹配'不实信息': {reason_clicked}")
            else:
                # 兜底2: 选第一个可用 radio
                reason_clicked = await page.evaluate('''() => {
                    let dialog = document.querySelector('.modal-dialog');
                    if (!dialog) return '';
                    let radios = dialog.querySelectorAll('.pl-radio');
                    if (radios.length > 0) {
                        let radio = radios[0].querySelector('input[type="radio"]');
                        if (radio) { radio.click(); return radios[0].innerText.trim() || '(first)'; }
                        radios[0].click();
                        return radios[0].innerText.trim() || '(first)';
                    }
                    return '';
                }''')
                if reason_clicked:
                    utils.logger.warning(f"[Report-ks] 最终兜底选了第一个: {reason_clicked}")
                else:
                    utils.logger.warning("[Report-ks] 未能选择任何理由")

        await asyncio.sleep(0.5)

        # Step 6: 填写描述
        if description:
            try:
                textarea = page.locator('.report-input textarea, .modal-body textarea').first
                if await textarea.is_visible(timeout=2000):
                    await textarea.fill(description)
            except Exception:
                pass

        await asyncio.sleep(0.5)

        # Step 7: 提交前截图
        await self._take_screenshot(page, ctx, "pre")
        utils.logger.info("[Report-ks] 提交前截图完成")

        # Step 8: 提交
        submitted = False
        try:
            btn = page.locator('.modal-body button:has-text("提交")').first
            if await btn.is_visible(timeout=3000):
                await btn.click()
                submitted = True
        except Exception:
            pass
        if not submitted:
            submitted = await page.evaluate('''() => {
                let btns = document.querySelectorAll('.modal-body button, .modal-dialog button');
                for (let b of btns) {
                    if (b.textContent.includes('提交') && !b.disabled) {
                        b.click();
                        return true;
                    }
                }
                return false;
            }''')
        if not submitted:
            raise Exception("未找到提交按钮")

        utils.logger.info("[Report-ks] 点击提交按钮")

        # Step 9: 提交后立即截图（不等 sleep，防弹窗消失）
        await asyncio.sleep(0.5)
        await self._take_screenshot(page, ctx, "post")
        utils.logger.info("[Report-ks] 提交后截图完成")

        await asyncio.sleep(1.5)

        # Step 10: 验证——弹窗消失或出现成功提示
        success = await page.evaluate('''() => {
            let dialog = document.querySelector('.modal-dialog');
            if (!dialog) return true;
            let rect = dialog.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) return true;
            let text = dialog.textContent || '';
            if (text.includes('提交成功') || text.includes('举报成功') || text.includes('感谢')) return true;
            return false;
        }''')
        if not success:
            await asyncio.sleep(2)
            success = await page.evaluate('''() => {
                let dialog = document.querySelector('.modal-dialog');
                if (!dialog) return true;
                let rect = dialog.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) return true;
                let text = dialog.textContent || '';
                return text.includes('成功') || text.includes('感谢');
            }''')
            if not success:
                raise Exception("举报弹窗仍在，提交可能未成功")
