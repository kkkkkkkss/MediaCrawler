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

        # Step 5: 选择理由 —— 使用 Playwright 原生 click 点击 radio
        # 快手举报弹窗的 DOM 结构：
        #   .report-item-list-item
        #     label.pl-radio (包含空 span + hidden input)
        #     div.report-item-text "理由文字"
        # 文本在 .report-item-text 中，radio 在同级的 label.pl-radio 中
        # 点击目标：整个 .report-item-list-item 或其中的 label.pl-radio
        all_options = await page.evaluate('''() => {
            let dialog = document.querySelector('.modal-dialog');
            if (!dialog) return [];
            let items = dialog.querySelectorAll('.report-item-list-item');
            let results = [];
            for (let item of items) {
                let textEl = item.querySelector('.report-item-text');
                let text = textEl ? textEl.textContent.trim() : item.textContent.trim();
                if (text) results.push(text);
            }
            return results;
        }''')
        utils.logger.info(f"[Report-ks] 可选理由列表: {all_options}")

        # 找到匹配理由在 .report-item-list-item 中的索引
        match_index = await page.evaluate('''(reasonText) => {
            function normalize(s) {
                return s.replace(/[\\s\\u3000\\u3001\\u3002\\uff0c\\uff0e\\u00b7\\-_\\/\\\\、，。]/g, '');
            }
            let dialog = document.querySelector('.modal-dialog');
            if (!dialog) return -1;
            let items = dialog.querySelectorAll('.report-item-list-item');
            let normReason = normalize(reasonText);

            for (let i = 0; i < items.length; i++) {
                let textEl = items[i].querySelector('.report-item-text');
                let text = textEl ? textEl.textContent.trim() : items[i].textContent.trim();
                if (!text) continue;

                let normText = normalize(text);
                let matched = (text === reasonText)
                    || text.includes(reasonText)
                    || reasonText.includes(text)
                    || (normText === normReason)
                    || normText.includes(normReason)
                    || normReason.includes(normText);

                if (matched) return i;
            }
            return -1;
        }''', reason_text)

        reason_clicked = ""
        if match_index >= 0:
            # 点击匹配项内的 label.pl-radio（label 包裹了 input，点击 label 触发 radio）
            label_locator = page.locator('.modal-dialog .report-item-list-item').nth(match_index).locator('label.pl-radio')
            await label_locator.click()
            reason_clicked = all_options[match_index] if match_index < len(all_options) else f"index={match_index}"
            utils.logger.info(f"[Report-ks] Playwright原生click选择理由成功: {reason_clicked}")
        else:
            # 兜底：匹配"不实信息"
            fallback_index = await page.evaluate('''() => {
                function normalize(s) {
                    return s.replace(/[\\s\\u3000\\u3001\\u3002\\uff0c\\uff0e\\u00b7\\-_\\/\\\\、，。]/g, '');
                }
                let dialog = document.querySelector('.modal-dialog');
                if (!dialog) return -1;
                let items = dialog.querySelectorAll('.report-item-list-item');
                for (let i = 0; i < items.length; i++) {
                    let textEl = items[i].querySelector('.report-item-text');
                    let text = textEl ? textEl.textContent.trim() : items[i].textContent.trim();
                    let norm = normalize(text);
                    if (norm.includes('不实信息')) return i;
                }
                return -1;
            }''')
            if fallback_index >= 0:
                label_locator = page.locator('.modal-dialog .report-item-list-item').nth(fallback_index).locator('label.pl-radio')
                await label_locator.click()
                reason_clicked = all_options[fallback_index] if fallback_index < len(all_options) else "不实信息"
                utils.logger.warning(f"[Report-ks] 兜底Playwright click匹配'不实信息': {reason_clicked}")
            else:
                utils.logger.warning(f"[Report-ks] 所有策略均未匹配到理由'{reason_text}'，可选列表: {all_options}")

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
        # 快手提交按钮可能是 button/a/div，class 可能含 report-submit/btn 等
        submitted = False
        try:
            # 策略1：Playwright locator 文本匹配（最可靠）
            btn = page.locator('.modal-dialog :text("提交")').first
            if await btn.is_visible(timeout=3000):
                await btn.click()
                submitted = True
        except Exception:
            pass
        if not submitted:
            try:
                # 策略2：查找 modal-dialog 内所有含"提交"文字的可点击元素
                btn = page.locator('.modal-dialog button, .modal-dialog a, .modal-dialog [class*="submit"], .modal-dialog [class*="btn"]').filter(has_text="提交").first
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    submitted = True
            except Exception:
                pass
        if not submitted:
            # 策略3：JS evaluate 兜底
            submitted = await page.evaluate('''() => {
                let dialog = document.querySelector('.modal-dialog');
                if (!dialog) return false;
                let allEls = dialog.querySelectorAll('button, a, div, span');
                for (let el of allEls) {
                    let text = (el.textContent || '').trim();
                    if (text === '提交' && el.offsetHeight > 0) {
                        el.click();
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
