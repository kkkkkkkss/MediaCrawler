# -*- coding: utf-8 -*-
# 抖音举报脚本
# 操作流程（基于实际 DOM 验证）：
#   1. 等页面加载 + 关闭"保存登录信息"弹窗
#   2. 用 dispatchEvent 触发 data-e2e="video-play-more" 的 hover+click
#   3. 在弹出菜单中用 JS 点击"举报"（排除页脚政府举报链接）
#   4. 在举报弹窗中点击自定义 radio 容器选择理由
#   5. 填写描述（如有）
#   6. 截图(pre) -> 提交 -> 截图(post)
#   7. 检验弹窗是否关闭作为成功判据

import asyncio

from playwright.async_api import Page

from media_platform.report.base_report import BaseReport, ReportContext
from tools import utils


class DouyinReport(BaseReport):
    platform = "dy"
    platform_name = "抖音"

    async def _do_report(self, page: Page, reason_text: str, description: str, ctx: ReportContext):
        # Step 0: 多次尝试关闭"保存登录信息"弹窗（有倒计时，可能多次出现）
        for _ in range(3):
            try:
                cancel = page.locator('button:has-text("取消")').first
                if await cancel.is_visible(timeout=2000):
                    await cancel.click()
                    utils.logger.info("[Report-dy] 关闭保存登录弹窗")
                    await asyncio.sleep(1)
            except Exception:
                break

        # Step 1: 等待页面加载，轮询 video-play-more 按钮
        more_found = False
        more_has_size = False
        for attempt in range(8):
            size_info = await page.evaluate('''() => {
                let btn = document.querySelector('[data-e2e="video-play-more"]');
                if (!btn) return null;
                let rect = btn.getBoundingClientRect();
                return {w: rect.width, h: rect.height};
            }''')
            if size_info:
                more_found = True
                # 图文页: 按钮有尺寸; 视频页: 按钮存在但 0x0
                more_has_size = size_info['w'] > 0 and size_info['h'] > 0
                break
            await asyncio.sleep(1.5)
            try:
                cancel = page.locator('button:has-text("取消")').first
                if await cancel.is_visible(timeout=500):
                    await cancel.click()
                    await asyncio.sleep(1)
            except Exception:
                pass

        if not more_found:
            raise Exception("页面未加载完成：未找到 video-play-more 按钮")

        if more_has_size:
            # ── 图文(note)页面流程：hover+click more 按钮 → 菜单中点击"举报" ──
            await page.evaluate('''() => {
                let btn = document.querySelector('[data-e2e="video-play-more"]');
                btn.dispatchEvent(new MouseEvent('mouseenter', {bubbles: true}));
                btn.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
            }''')
            await asyncio.sleep(0.5)
            await page.evaluate('''() => {
                let btn = document.querySelector('[data-e2e="video-play-more"]');
                btn.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
            }''')
            await asyncio.sleep(1.5)

            report_clicked = await page.evaluate('''() => {
                let els = document.querySelectorAll('*');
                for (let el of els) {
                    let ownText = '';
                    for (let n of el.childNodes) { if (n.nodeType === 3) ownText += n.textContent; }
                    ownText = ownText.trim();
                    let rect = el.getBoundingClientRect();
                    if (ownText === '举报' && rect.width > 0 && rect.y > 300 && rect.y < 750) {
                        let href = el.href || (el.closest('a') ? el.closest('a').href : '');
                        if (href && href.includes('12377')) continue;
                        el.click();
                        return true;
                    }
                }
                return false;
            }''')
            if not report_clicked:
                raise Exception("弹出菜单中未找到举报按钮")
        else:
            # ── 视频(video)页面流程：video-play-more 大小为 0，直接 JS 点击"举报"文字 ──
            report_clicked = await page.evaluate('''() => {
                let spans = document.querySelectorAll('span, a, div');
                for (let el of spans) {
                    let own = '';
                    for (let n of el.childNodes) { if (n.nodeType === 3) own += n.textContent; }
                    own = own.trim();
                    if (own === '举报') {
                        let rect = el.getBoundingClientRect();
                        if (rect.width > 0 && rect.y < 1000 && rect.y > 100) {
                            let href = el.href || (el.closest('a') ? el.closest('a').href : '');
                            if (href && href.includes('12377')) continue;
                            el.click();
                            return true;
                        }
                    }
                }
                return false;
            }''')
            if not report_clicked:
                raise Exception("视频页未找到举报按钮")

        utils.logger.info("[Report-dy] 点击举报按钮成功")
        await asyncio.sleep(2)

        # Step 4: 等待举报弹窗出现（data-e2e="report-container"）
        dialog_ready = False
        for _ in range(5):
            exists = await page.evaluate('''() => {
                return document.querySelector('[data-e2e="report-container"]') !== null;
            }''')
            if exists:
                dialog_ready = True
                break
            await asyncio.sleep(1)
        if not dialog_ready:
            raise Exception("举报弹窗未出现")

        # Step 5: 选择举报理由
        # React onClick 绑定在 circle div（span 的兄弟元素）上
        reason_clicked = await page.evaluate('''(reasonText) => {
            let container = document.querySelector('[data-e2e="report-container"]');
            if (!container) return false;
            let spans = container.querySelectorAll('span');
            for (let s of spans) {
                if (s.textContent.trim() === reasonText) {
                    let circle = s.parentElement ? s.parentElement.querySelector('div') : null;
                    if (circle) { circle.click(); return true; }
                }
            }
            return false;
        }''', reason_text)

        if reason_clicked:
            utils.logger.info(f"[Report-dy] 选择理由成功: {reason_text}")
        else:
            fallback = await page.evaluate('''() => {
                let container = document.querySelector('[data-e2e="report-container"]');
                if (!container) return false;
                let spans = container.querySelectorAll('span');
                for (let s of spans) {
                    if (s.textContent.trim() === '不实信息') {
                        let circle = s.parentElement ? s.parentElement.querySelector('div') : null;
                        if (circle) { circle.click(); return true; }
                    }
                }
                return false;
            }''')
            if fallback:
                utils.logger.warning("[Report-dy] 使用兜底理由: 不实信息")
            else:
                utils.logger.warning("[Report-dy] 未能选择任何理由")

        await asyncio.sleep(1)

        # Step 5.5: 如果出现子类型选择（如"不实信息"→"疑似虚假时事"），自动选第一个
        sub_clicked = await page.evaluate('''() => {
            let container = document.querySelector('[data-e2e="report-container"]');
            if (!container) return false;
            let text = container.textContent;
            if (!text.includes('请选择具体的类型') && !text.includes('请选择具体')) return false;
            let allDivs = Array.from(container.querySelectorAll('div'));
            let idx = allDivs.findIndex(d => d.textContent.trim().startsWith('请选择具体'));
            if (idx < 0) return false;
            for (let i = idx + 1; i < allDivs.length; i++) {
                let d = allDivs[i];
                if (d.textContent.trim().startsWith('举报描述')) break;
                let circle = d.querySelector('div');
                let span = d.querySelector('span');
                if (circle && span && span.textContent.trim().length > 1) {
                    circle.click();
                    return true;
                }
            }
            return false;
        }''')
        if sub_clicked:
            utils.logger.info("[Report-dy] 自动选择了子类型")

        await asyncio.sleep(0.5)

        # Step 6: 填写描述
        if description:
            try:
                textarea = page.locator('[data-e2e="report-container"] textarea').first
                if await textarea.is_visible(timeout=2000):
                    await textarea.fill(description)
            except Exception:
                pass

        await asyncio.sleep(0.5)

        # Step 7: 提交前截图
        await self._take_screenshot(page, ctx, "pre")
        utils.logger.info("[Report-dy] 提交前截图完成")

        # Step 8: 点击"提交"
        submitted = False
        try:
            btn = page.locator('[data-e2e="report-container"] button:has-text("提交")').first
            if await btn.is_visible(timeout=3000):
                await btn.click()
                submitted = True
        except Exception:
            pass
        if not submitted:
            raise Exception("未找到提交按钮")

        utils.logger.info("[Report-dy] 点击提交按钮")

        # Step 9: 提交后等待成功弹窗出现再截图（太快会截到提交前状态）
        await asyncio.sleep(1.5)
        await self._take_screenshot(page, ctx, "post")
        utils.logger.info("[Report-dy] 提交后截图完成")

        await asyncio.sleep(1)

        # Step 10: 验证——举报弹窗消失 = 提交成功
        dialog_gone = await page.evaluate('''() => {
            let container = document.querySelector('[data-e2e="report-container"]');
            if (!container) return true;
            let rect = container.getBoundingClientRect();
            return rect.width === 0 || rect.height === 0;
        }''')
        if not dialog_gone:
            await asyncio.sleep(2)
            dialog_gone = await page.evaluate('''() => {
                let container = document.querySelector('[data-e2e="report-container"]');
                return !container || container.getBoundingClientRect().width === 0;
            }''')
            if not dialog_gone:
                raise Exception("举报弹窗仍在，提交可能未成功")
