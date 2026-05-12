# -*- coding: utf-8 -*-
# url_check 模式核心调度器
# 从外部业务库读取 URL → 自动识别平台并分组 → 逐平台启动浏览器+登录 → 调接口 → AI 解析 → 写回
#
# 调度流程：
# 1. ExternalDB.fetch_pending_urls() 拉取待处理行
# 2. url_detector.group_urls_by_platform() 按平台分组
# 3. 对每个平台：复用对应 Crawler 的浏览器启动+登录逻辑，创建 Client
# 4. 对每个 URL：Client 获取作品详情 JSON → AIFieldMapper 提取指标 → ExternalDB 写回
# 5. 可选：抓取评论并写入评论表

import asyncio
import os
from typing import Any, Dict, List, Optional

import httpx
from playwright.async_api import (
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

import config
from base.base_crawler import AbstractCrawler
from database.external_db import external_db
from tools import utils
from tools.ai_field_mapper import ai_mapper
from tools.cdp_browser import CDPBrowserManager
from tools.url_detector import group_urls_by_platform, detect_platform
from tools.validity_checker import (
    ValidityStatus,
    http_pre_check,
    dom_validity_check,
    check_api_json_validity,
    is_invalid,
)
from var import crawler_type_var

# ── 平台处理顺序（优先处理量大的平台）──
_PLATFORM_ORDER = ["dy", "bili", "ks", "toutiao", "xhs", "wb"]


class UrlCheckCrawler(AbstractCrawler):
    """
    url_check 模式主入口。
    不同于其它 Crawler 只处理单一平台，本 Crawler 会自动识别 URL 所属平台，
    按平台分组顺序执行，每个平台复用对应的 Client。
    """

    def __init__(self):
        self.cdp_manager: Optional[CDPBrowserManager] = None
        self.browser_context: Optional[BrowserContext] = None
        self.context_page: Optional[Page] = None
        self._collected_comment_texts: List[str] = []
        self._all_results: List[Dict] = []
        self._ip_pool = None  # IP代理池实例（按需初始化）

    @staticmethod
    def _load_urls_from_file() -> List[Dict]:
        """从本地 txt 文件读取 URL（每行一个），转为与 DB 兼容的行格式"""
        file_path = getattr(config, "URLCHECK_INPUT_FILE", "")
        if not file_path:
            utils.logger.error("[UrlCheckCrawler] 文件输入源未指定路径 (--urlcheck_file)")
            return []
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip()]
            rows = []
            for idx, url in enumerate(lines, start=1):
                if not url.startswith("http"):
                    continue
                rows.append({"id": idx, "url": url})
            utils.logger.info(f"[UrlCheckCrawler] 从文件读取 {len(rows)} 条 URL: {file_path}")
            return rows
        except FileNotFoundError:
            utils.logger.error(f"[UrlCheckCrawler] 文件不存在: {file_path}")
            return []

    async def start(self):
        crawler_type_var.set("url_check")
        mode = config.URLCHECK_MODE
        batch_size = config.URLCHECK_BATCH_SIZE
        input_source = getattr(config, "URLCHECK_INPUT_SOURCE", "db")

        utils.logger.info(
            f"[UrlCheckCrawler] 启动 url_check 模式 mode={mode} "
            f"source={input_source} batch_size={batch_size}"
        )

        # 1. 根据输入来源读取 URL
        if input_source == "file":
            rows = self._load_urls_from_file()
        else:
            rows = await external_db.fetch_pending_urls(
                batch_size=batch_size, mode=mode
            )
        if not rows:
            utils.logger.info("[UrlCheckCrawler] 没有待处理的 URL，退出")
            return

        # 2. 按平台分组
        groups = group_urls_by_platform(rows)
        utils.logger.info(
            f"[UrlCheckCrawler] URL 分组: "
            + ", ".join(f"{k}={len(v)}" for k, v in groups.items())
        )

        # 3. 如果启用了 Cookie 池，先加载
        if getattr(config, "ENABLE_COOKIE_POOL", False):
            from proxy.cookie_pool import cookie_pool
            await cookie_pool.load()
            stats_info = cookie_pool.get_stats()
            utils.logger.info(f"[UrlCheckCrawler] Cookie池状态: {stats_info}")

        # 4. 按顺序处理每个平台
        for platform in _PLATFORM_ORDER:
            if platform not in groups:
                continue
            url_rows = groups[platform]
            utils.logger.info(
                f"[UrlCheckCrawler] 开始处理平台 [{platform}] 共 {len(url_rows)} 条"
            )
            await self._process_platform(platform, url_rows, mode)

        # 处理未知平台的 URL（标记为无效）
        unknown_rows = groups.get("unknown", [])
        for row in unknown_rows:
            utils.logger.warning(
                f"[UrlCheckCrawler] 未知平台 URL id={row['id']} url={row['url']}，标记 is_valid=2"
            )
            await self._save_result(row, is_valid=2)

        # 输出 AI 使用统计
        stats = ai_mapper.get_stats()
        utils.logger.info(
            f"[UrlCheckCrawler] 完成！AI 统计: {stats['call_count']} 次调用, "
            f"{stats['total_tokens_used']} tokens"
        )

        # 生成词云（仅在开启评论抓取且有评论数据时）
        if config.URLCHECK_ENABLE_COMMENTS and self._collected_comment_texts:
            await self._generate_wordcloud()

        # 输出 Excel 报表
        if self._all_results:
            try:
                from store.url_check_excel_store import generate_url_check_excel
                excel_path = generate_url_check_excel(self._all_results)
                utils.logger.info(f"[UrlCheckCrawler] Excel 报表: {excel_path}")
            except Exception as e:
                utils.logger.error(f"[UrlCheckCrawler] Excel 输出失败: {e}")

    async def _process_platform(
        self, platform: str, url_rows: List[Dict], mode: str
    ):
        """
        处理单个平台的所有 URL。
        流程：HTTP 预检(免浏览器) → 仅对需要浏览器的 URL 启动浏览器 → 逐 URL 处理
        """
        # ── 第一层：HTTP 预检，不需要浏览器/登录，快速筛掉明确失效的 ──
        need_browser_rows = []
        for row in url_rows:
            url = row.get("url", "")
            status, final_url = await http_pre_check(url, platform)
            if is_invalid(status):
                utils.logger.info(
                    f"[UrlCheckCrawler] HTTP 预检失效 id={row['id']} "
                    f"reason={status.value} url={url}"
                )
                await self._save_result(row, is_valid=2)
            else:
                need_browser_rows.append(row)

        if not need_browser_rows:
            utils.logger.info(
                f"[UrlCheckCrawler] 平台 [{platform}] 全部 URL 已在 HTTP 预检中处理完毕"
            )
            return

        utils.logger.info(
            f"[UrlCheckCrawler] 平台 [{platform}] HTTP 预检后剩余 "
            f"{len(need_browser_rows)} 条需要浏览器处理"
        )

        # ── 第二/三层：需要浏览器的 URL ──
        async with async_playwright() as playwright:
            try:
                client, cleanup = await self._create_platform_client(
                    platform, playwright
                )
                if client is None:
                    utils.logger.error(
                        f"[UrlCheckCrawler] 平台 [{platform}] 创建 Client 失败，跳过"
                    )
                    return

                for row in need_browser_rows:
                    await self._process_single_url(
                        platform, client, row, mode
                    )
                    await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)

            except Exception as e:
                utils.logger.error(
                    f"[UrlCheckCrawler] 平台 [{platform}] 处理异常: {e}"
                )
            finally:
                await self._cleanup_browser()

    async def _save_result(self, row: Dict, is_valid: int, metrics: Optional[Dict] = None):
        """统一的结果保存：文件模式写内存，DB模式写外部库"""
        row["_is_valid"] = is_valid
        if metrics:
            row.setdefault("_metrics", {}).update(metrics)
        self._all_results.append(row)

        if getattr(config, "URLCHECK_INPUT_SOURCE", "db") != "file":
            await external_db.update_metrics(
                row["id"],
                is_valid=is_valid,
                praise_count=metrics.get("praise_count") if metrics else None,
                reply_count=metrics.get("reply_count") if metrics else None,
                visit_count=metrics.get("visit_count") if metrics else None,
                share_count=metrics.get("share_count") if metrics else None,
            )

    async def _process_single_url(
        self,
        platform: str,
        client: Any,
        row: Dict,
        mode: str,
    ):
        """
        处理单个 URL（已通过 HTTP 预检的）。

        Cookie 池失败重试策略：
        - API 返回 None（获取不到接口数据）→ 判定为 FATAL，切换 Cookie 重试一次
        - 网络超时异常 → 判定为 BUSINESS，切换 Cookie 但不计入失败
        - 接口返回了数据但链接无效 → 非 Cookie 问题，直接标记
        - 能拿到数据但提取不到指标 → PARSE 级别，Cookie 正常
        """
        row_id = row["id"]
        url = row["url"]
        content_id = row.get("_content_id")

        utils.logger.info(
            f"[UrlCheckCrawler] 处理 id={row_id} platform={platform} "
            f"content_id={content_id} url={url}"
        )

        # 最多尝试次数（首次 + 切换重试）
        max_attempts = 2 if getattr(config, "ENABLE_COOKIE_POOL", False) else 1

        for attempt in range(max_attempts):
            try:
                raw_json = await self._fetch_detail(platform, client, content_id, url, row=row)
                content_id = row.get("_content_id", content_id)

                if raw_json is not None:
                    # 拿到接口数据 → Cookie 有效
                    api_status = check_api_json_validity(platform, raw_json)
                    if is_invalid(api_status):
                        utils.logger.info(
                            f"[UrlCheckCrawler] id={row_id} 接口字段检测失效 "
                            f"reason={api_status.value}"
                        )
                        await self._save_result(row, is_valid=2)
                        return

                    if mode == "validity":
                        await self._save_result(row, is_valid=1)
                        return

                    # ── 指标提取 ──
                    if platform == "toutiao":
                        # 头条直接用 DOM 提取（API JSON 不含转赞评数据，跳过 AI 节省 token）
                        utils.logger.info(
                            f"[UrlCheckCrawler] id={row_id} 头条使用 DOM 提取指标"
                        )
                        # 决定 DOM 提取时的导航 URL
                        if "/i" in url and "/i" + content_id in url:
                            toutiao_nav_url = f"https://www.toutiao.com/video/{content_id}/"
                        elif any(d in url for d in ("zjurl.cn", "weitoutiao", "ixigua.com")):
                            toutiao_nav_url = url
                        else:
                            toutiao_nav_url = None
                        metrics = await client.get_article_metrics_from_dom(
                            content_id, original_url=toutiao_nav_url
                        )
                        row["_extract_method"] = "DOM"
                    else:
                        # 构造基准帖子检测回调（三层兜底第二层）
                        async def _health_check(plat):
                            from tools.platform_health_checker import check_benchmark
                            return await check_benchmark(plat, client, self._fetch_detail)

                        metrics = await ai_mapper.extract_metrics(
                            platform, raw_json, health_checker=_health_check
                        )
                        row["_extract_method"] = ai_mapper.last_method or "硬编码"

                    row["_raw_json"] = raw_json
                    await self._save_result(row, is_valid=1, metrics=metrics)

                    # 抓取评论
                    if config.URLCHECK_ENABLE_COMMENTS and content_id:
                        comments_count = await self._fetch_and_store_comments(
                            platform, client, content_id, url, row
                        )
                        if comments_count and comments_count > 0:
                            cur_reply = metrics.get("reply_count")
                            if cur_reply is None or cur_reply == 0:
                                metrics["reply_count"] = comments_count
                                row.setdefault("_metrics", {})["reply_count"] = comments_count
                                if getattr(config, "URLCHECK_INPUT_SOURCE", "db") != "file":
                                    await external_db.update_metrics(
                                        row_id, reply_count=comments_count
                                    )
                    return

                # ── raw_json is None → API 获取失败，根据原因决定是否归咎 Cookie ──
                fail_reason = row.get("_fetch_fail_reason", self._FETCH_FAIL_AUTH)

                # 内容不存在/已删除 → Cookie 正常，不切换不计数，直接标记链接无效
                if fail_reason == self._FETCH_FAIL_CONTENT:
                    utils.logger.info(
                        f"[UrlCheckCrawler] id={row_id} 内容不存在(非Cookie问题)，标记无效"
                    )
                    await self._save_result(row, is_valid=2)
                    return

                # 网络超时 → 不计入 Cookie 失败，但可以切换试试
                if fail_reason == self._FETCH_FAIL_NETWORK:
                    if getattr(config, "ENABLE_COOKIE_POOL", False) and attempt < max_attempts - 1:
                        from proxy.cookie_pool import cookie_pool
                        current_cookie_id = cookie_pool.get_current_id(platform)
                        new_cookie = cookie_pool.get_another_cookie(
                            platform, exclude_id=current_cookie_id
                        )
                        if new_cookie and hasattr(client, "headers"):
                            client.headers["Cookie"] = new_cookie
                            utils.logger.info(
                                f"[UrlCheckCrawler] id={row_id} 网络错误，切换Cookie重试(不计入失败)"
                            )
                        await asyncio.sleep(1)
                        continue
                    break

                # auth_failed → Cookie/IP 风控，计入 FATAL 失败 + 切换重试
                if not getattr(config, "ENABLE_COOKIE_POOL", False):
                    break

                from proxy.cookie_pool import cookie_pool, FailureLevel

                current_cookie_id = cookie_pool.get_current_id(platform)

                if attempt < max_attempts - 1:
                    has_more = cookie_pool.report_failure(platform, FailureLevel.FATAL)
                    if has_more:
                        new_cookie = cookie_pool.get_another_cookie(
                            platform, exclude_id=current_cookie_id
                        )
                        if new_cookie and hasattr(client, "headers"):
                            client.headers["Cookie"] = new_cookie
                            utils.logger.info(
                                f"[UrlCheckCrawler] id={row_id} Cookie鉴权失败(attempt={attempt+1})，"
                                f"切换Cookie重试: {cookie_pool.get_current_id(platform)}"
                            )
                            await asyncio.sleep(1)
                            # 清除上次的失败原因
                            row.pop("_fetch_fail_reason", None)
                            continue
                    utils.logger.warning(
                        f"[UrlCheckCrawler] id={row_id} 无更多可用Cookie"
                    )
                    break
                else:
                    cookie_pool.report_failure(platform, FailureLevel.FATAL)
                    break

            except asyncio.TimeoutError:
                utils.logger.warning(
                    f"[UrlCheckCrawler] id={row_id} 请求超时(attempt={attempt+1})"
                )
                if getattr(config, "ENABLE_COOKIE_POOL", False) and attempt < max_attempts - 1:
                    from proxy.cookie_pool import cookie_pool
                    current_cookie_id = cookie_pool.get_current_id(platform)
                    new_cookie = cookie_pool.get_another_cookie(
                        platform, exclude_id=current_cookie_id
                    )
                    if new_cookie and hasattr(client, "headers"):
                        client.headers["Cookie"] = new_cookie
                    await asyncio.sleep(1)
                    continue
                break

            except Exception as e:
                utils.logger.error(
                    f"[UrlCheckCrawler] id={row_id} 处理异常(attempt={attempt+1}): {e}"
                )
                if attempt < max_attempts - 1:
                    await asyncio.sleep(1)
                    continue
                break

        # ── 所有尝试均失败：DOM 检测 ──
        utils.logger.info(
            f"[UrlCheckCrawler] id={row_id} API 获取失败，启动 DOM 检测"
        )
        dom_status = await self._dom_check_on_new_page(url, platform)
        if is_invalid(dom_status):
            utils.logger.info(
                f"[UrlCheckCrawler] id={row_id} DOM检测失效 reason={dom_status.value}"
            )
            await self._save_result(row, is_valid=2)
        else:
            # API失败但DOM未检测到"内容不存在"标志，可能是验证码/风控拦截
            # 此时内容可能仍然有效，标记为有效
            fail_reason = row.get("_fetch_fail_reason", "")
            if fail_reason == self._FETCH_FAIL_CONTENT:
                utils.logger.warning(
                    f"[UrlCheckCrawler] id={row_id} API判定内容不存在但DOM未确认，标记无效"
                )
                await self._save_result(row, is_valid=2)
            else:
                utils.logger.warning(
                    f"[UrlCheckCrawler] id={row_id} API失败(风控/验证码)+DOM未检测到失效，"
                    f"保持有效(is_valid=1)"
                )
                await self._save_result(row, is_valid=1)

    async def _dom_check_on_new_page(
        self, url: str, platform: str
    ) -> ValidityStatus:
        """在独立的新页面上做 DOM 关键词检测，不影响 API Client 的页面上下文"""
        check_page = None
        try:
            if self.browser_context:
                check_page = await self.browser_context.new_page()
                result = await dom_validity_check(check_page, url, platform)
                return result
            return ValidityStatus.NEED_BROWSER
        except Exception as e:
            utils.logger.warning(
                f"[UrlCheckCrawler] DOM 检测异常: {url} → {e}"
            )
            return ValidityStatus.NEED_BROWSER
        finally:
            if check_page:
                try:
                    await check_page.close()
                except Exception:
                    pass

    # 用于区分 _fetch_detail 返回 None 的原因
    _FETCH_FAIL_AUTH = "auth_failed"       # Cookie/IP 风控，需要计入 Cookie 失败
    _FETCH_FAIL_CONTENT = "content_gone"   # 内容不存在/已删除，Cookie 正常
    _FETCH_FAIL_NETWORK = "network_error"  # 网络/超时类错误

    async def _fetch_detail(
        self,
        platform: str,
        client: Any,
        content_id: Optional[str],
        url: str,
        row: Optional[Dict] = None,
    ) -> Optional[Dict]:
        """
        根据平台类型调用对应 Client 获取作品详情原始 JSON。

        返回值：
          - dict: 成功获取到数据（Cookie 有效）
          - None: 获取失败，失败原因写入 row["_fetch_fail_reason"]：
            - "auth_failed": Cookie/IP 风控（account blocked等）→ 应计入Cookie失败
            - "content_gone": 内容不存在/已删除 → Cookie 正常，不计入失败
            - "network_error": 网络超时等 → 不计入Cookie失败
        """
        if not content_id:
            utils.logger.warning(f"[UrlCheckCrawler] 无法从 URL 提取 content_id: {url}")
            if row is not None:
                row["_fetch_fail_reason"] = self._FETCH_FAIL_CONTENT
            return None

        # 抖音短链（iesdouyin.com）的 content_id 可能需要通过重定向获取真实 ID
        if platform == "dy" and "iesdouyin.com" in url:
            resolved_id = await self._resolve_douyin_share_url(url)
            if resolved_id:
                content_id = resolved_id

        # 快手 /f/ 短链或 notice/detail 链接需要重定向解析获取真实 photo_id
        if platform == "ks" and ("/f/" in url or "notice/detail" in url):
            resolved_id = await self._resolve_kuaishou_share_url(url)
            if resolved_id:
                content_id = resolved_id

        # 将解析后的真实 ID 写回 row，供后续评论抓取使用
        if row is not None:
            row["_content_id"] = content_id

        try:
            if platform == "dy":
                res = await client.get_video_by_id(content_id, raw=True)
                # 正常拿到了 JSON 响应 → Cookie 有效
                if res is not None:
                    detail = res.get("aweme_detail")
                    if detail:
                        return detail
                    # aweme_detail 为空 → 视频不存在/已删除，但 Cookie 是正常的
                    utils.logger.info(
                        f"[UrlCheckCrawler] dy aweme_detail 为空，视频可能不存在: {content_id}"
                    )
                    if row is not None:
                        row["_fetch_fail_reason"] = self._FETCH_FAIL_CONTENT
                    return None
                # res 为 None 不应该发生（client 正常会返回 dict 或抛异常）
                if row is not None:
                    row["_fetch_fail_reason"] = self._FETCH_FAIL_AUTH
                return None

            elif platform == "bili":
                res = await client.get_video_info(bvid=content_id)
                if res and res.get("code") == 0:
                    return res
                # B站返回了 JSON 但 code != 0 → 视频不存在
                if res and res.get("code") in (-404, 62002, 62004):
                    if row is not None:
                        row["_fetch_fail_reason"] = self._FETCH_FAIL_CONTENT
                    return None
                return res

            elif platform == "ks":
                res = await client.get_video_info(content_id)
                detail = res.get("visionVideoDetail") if res else None
                if detail and detail.get("status") == 1:
                    return detail
                # 接口返回了 visionVideoDetail 但 status != 1，需要区分原因
                if detail and detail.get("status") is not None:
                    # status=0 或其他非1值通常表示内容不存在/已删除
                    if row is not None:
                        row["_fetch_fail_reason"] = self._FETCH_FAIL_CONTENT
                    return None
                # visionVideoDetail 为 None → Cookie 未登录/失效
                if res is not None and detail is None:
                    if row is not None:
                        row["_fetch_fail_reason"] = self._FETCH_FAIL_AUTH
                    return None
                if row is not None:
                    row["_fetch_fail_reason"] = self._FETCH_FAIL_AUTH
                return None

            elif platform == "toutiao":
                if hasattr(client, "get_article_info"):
                    # /i{id}/ 短链在 headless 下被拦截，改用 /video/ 路径
                    if "/i" in url and "/i" + content_id in url:
                        nav_url = f"https://www.toutiao.com/video/{content_id}/"
                    elif any(d in url for d in ("zjurl.cn", "weitoutiao", "ixigua.com")):
                        nav_url = url
                    else:
                        nav_url = None  # 使用默认 /article/ 路径

                    if nav_url:
                        result = await client.get_article_info(
                            content_id, original_url=nav_url
                        )
                    else:
                        result = await client.get_article_info(content_id)

                    # DOM 检测失效关键词
                    if hasattr(client, "playwright_page"):
                        page_text = await client.playwright_page.evaluate(
                            "() => document.body?.innerText?.substring(0, 1000) || ''"
                        )
                        utils.logger.info(
                            f"[UrlCheckCrawler] toutiao page_text({len(page_text)}字): "
                            f"{page_text[:80]}"
                        )
                        stripped = page_text.strip().lower()
                        # 头条404特征：页面仅显示"error"或完全空白
                        if stripped in ("error", "", "404"):
                            utils.logger.info(
                                f"[UrlCheckCrawler] toutiao 页面为空白/error，"
                                f"判定为失效: {content_id}"
                            )
                            if row is not None:
                                row["_fetch_fail_reason"] = self._FETCH_FAIL_CONTENT
                            return None
                        dead_keywords = ["内容已删除", "文章不存在", "该内容已下架",
                                         "页面不存在", "内容不存在", "404 Not Found",
                                         "抱歉，你访问的内容不存在", "内容正在审核中",
                                         "此内容因违规无法查看", "该文章已被删除"]
                        for kw in dead_keywords:
                            if kw in page_text:
                                utils.logger.info(
                                    f"[UrlCheckCrawler] toutiao DOM检测到失效关键词「{kw}」: {content_id}"
                                )
                                if row is not None:
                                    row["_fetch_fail_reason"] = self._FETCH_FAIL_CONTENT
                                return None
                    return result
                return None

            elif platform == "xhs":
                if hasattr(client, "get_note_by_id_from_html"):
                    return await client.get_note_by_id_from_html(
                        content_id, xsec_source="", xsec_token=""
                    )
                return None

            elif platform == "wb":
                return await client.get_note_info_by_id(content_id)

            else:
                return None

        except asyncio.TimeoutError:
            utils.logger.warning(
                f"[UrlCheckCrawler] 平台 [{platform}] 请求超时: {content_id}"
            )
            if row is not None:
                row["_fetch_fail_reason"] = self._FETCH_FAIL_NETWORK
            return None

        except Exception as e:
            err_msg = str(e).lower()
            utils.logger.error(
                f"[UrlCheckCrawler] 平台 [{platform}] 获取详情失败: {e}"
            )
            await self._recover_context_page(platform)

            # 根据错误信息判断是 Cookie 问题还是内容问题
            if any(kw in err_msg for kw in ["account blocked", "blocked", "login", "auth", "credential"]):
                if row is not None:
                    row["_fetch_fail_reason"] = self._FETCH_FAIL_AUTH
            elif any(kw in err_msg for kw in ["not found", "404", "deleted", "removed"]):
                if row is not None:
                    row["_fetch_fail_reason"] = self._FETCH_FAIL_CONTENT
            else:
                # 无法确定原因，默认为网络类错误，不归咎于 Cookie
                if row is not None:
                    row["_fetch_fail_reason"] = self._FETCH_FAIL_NETWORK
            return None

    async def _resolve_douyin_share_url(self, url: str) -> Optional[str]:
        """
        抖音短链（iesdouyin.com/share/video/xxx）跟随重定向获取真实 aweme_id。
        短链 302 → douyin.com/video/REAL_ID
        """
        import re
        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=10, verify=False,
                headers={"User-Agent": "Mozilla/5.0"},
            ) as client:
                resp = await client.get(url)
                final = str(resp.url)
                m = re.search(r"/video/(\d+)", final)
                if m:
                    resolved = m.group(1)
                    utils.logger.info(
                        f"[UrlCheckCrawler] 抖音短链解析: {url[:50]} → aweme_id={resolved}"
                    )
                    return resolved
        except Exception as e:
            utils.logger.warning(f"[UrlCheckCrawler] 抖音短链解析失败: {e}")
        return None

    async def _resolve_kuaishou_share_url(self, url: str) -> Optional[str]:
        """
        快手短链（/f/xxx 或 notice/detail?id=xxx）跟随重定向获取真实 photo_id。
        短链 302 → kuaishou.com/short-video/REAL_ID
        """
        import re
        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=10, verify=False,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            ) as client:
                resp = await client.get(url)
                final = str(resp.url)
                m = re.search(r"/short-video/([\w-]+)", final)
                if m:
                    resolved = m.group(1)
                    utils.logger.info(
                        f"[UrlCheckCrawler] 快手短链解析: {url[:60]} → photo_id={resolved}"
                    )
                    return resolved
                # 有些链接重定向后 URL 中直接包含 photoId 参数
                m = re.search(r"photoId=([\w-]+)", final)
                if m:
                    resolved = m.group(1)
                    utils.logger.info(
                        f"[UrlCheckCrawler] 快手短链解析(photoId): {url[:60]} → photo_id={resolved}"
                    )
                    return resolved
        except Exception as e:
            utils.logger.warning(f"[UrlCheckCrawler] 快手短链解析失败: {e}")
        return None

    async def _recover_context_page(self, platform: str):
        """尝试恢复 context_page 到平台首页"""
        platform_urls = {
            "dy": "https://www.douyin.com",
            "bili": "https://www.bilibili.com",
            "ks": "https://www.kuaishou.com",
            "toutiao": "https://www.toutiao.com",
            "xhs": "https://www.xiaohongshu.com",
            "wb": "https://m.weibo.cn",
        }
        try:
            if self.context_page and not self.context_page.is_closed():
                home_url = platform_urls.get(platform, "about:blank")
                await self.context_page.goto(home_url, wait_until="domcontentloaded")
                utils.logger.info(
                    f"[UrlCheckCrawler] context_page 已恢复到 {home_url}"
                )
            elif self.browser_context:
                self.context_page = await self.browser_context.new_page()
                home_url = platform_urls.get(platform, "about:blank")
                await self.context_page.goto(home_url, wait_until="domcontentloaded")
                utils.logger.info(
                    f"[UrlCheckCrawler] 创建新 context_page 并导航到 {home_url}"
                )
        except Exception as e:
            utils.logger.warning(f"[UrlCheckCrawler] 恢复 context_page 失败: {e}")

    async def _fetch_and_store_comments(
        self,
        platform: str,
        client: Any,
        content_id: str,
        content_url: str,
        row: Optional[Dict] = None,
    ) -> int:
        """抓取评论并写入评论表，同时收集评论文本用于词云。返回实际抓取到的评论条数。"""
        from store.url_check_comment_store import store_comments_to_external_db

        max_comments = config.URLCHECK_MAX_COMMENTS
        comments = []
        try:
            if platform == "dy":
                comments = await client.get_aweme_all_comments(
                    aweme_id=content_id,
                    crawl_interval=config.CRAWLER_MAX_SLEEP_SEC,
                    max_count=max_comments,
                )
            elif platform == "bili":
                comments = await client.get_video_all_comments(
                    video_id=content_id,
                    crawl_interval=config.CRAWLER_MAX_SLEEP_SEC,
                    max_count=max_comments,
                )
            elif platform == "ks":
                comments = await client.get_video_all_comments(
                    photo_id=content_id,
                    crawl_interval=config.CRAWLER_MAX_SLEEP_SEC,
                )
            elif platform == "toutiao":
                comments = await client.get_all_comments(
                    item_id=content_id,
                    crawl_interval=config.CRAWLER_MAX_SLEEP_SEC,
                    max_count=max_comments,
                )
            elif platform == "wb":
                comments = await client.get_note_all_comments(
                    note_id=content_id,
                    crawl_interval=config.CRAWLER_MAX_SLEEP_SEC,
                    max_count=max_comments,
                )

            if comments:
                await store_comments_to_external_db(
                    platform, content_id, content_url, comments
                )
                # 存储格式化后的评论到 row（供 TaskInfo.comments_data 使用）
                if row is not None:
                    from store.url_check_comment_store import _PLATFORM_CONVERTERS, _default_converter
                    converter = _PLATFORM_CONVERTERS.get(platform, _default_converter)
                    formatted = []
                    for raw_c in comments:
                        try:
                            fc = converter(raw_c, platform, content_id, content_url)
                            if fc:
                                # 序列化 datetime 和移除 raw_json（太大）
                                clean = {k: (str(v) if hasattr(v, 'isoformat') else v)
                                         for k, v in fc.items() if k != "raw_json"}
                                formatted.append(clean)
                        except Exception:
                            pass
                    row["_comments"] = formatted

                for c in comments:
                    text = c.get("text") or ""
                    if not text:
                        content_val = c.get("content")
                        if isinstance(content_val, dict):
                            text = content_val.get("message", "")
                        elif isinstance(content_val, str):
                            text = content_val
                    if text and isinstance(text, str):
                        self._collected_comment_texts.append(text)

            return len(comments)

        except Exception as e:
            utils.logger.error(
                f"[UrlCheckCrawler] 评论抓取失败 platform={platform} "
                f"content_id={content_id}: {e}"
            )
            return 0

    async def _generate_wordcloud(self):
        """从本次收集到的评论文本生成词云"""
        import pathlib
        try:
            if not config.ENABLE_GET_WORDCLOUD:
                return
            from tools.words import AsyncWordCloudGenerator
            generator = AsyncWordCloudGenerator()
            comment_data = [{"content": t} for t in self._collected_comment_texts if t.strip()]
            if not comment_data:
                return
            base_path = "data/url_check/words"
            pathlib.Path(base_path).mkdir(parents=True, exist_ok=True)
            prefix = f"{base_path}/url_check_comments_{utils.get_current_date()}"
            utils.logger.info(
                f"[UrlCheckCrawler] 生成词云，评论数量: {len(comment_data)}"
            )
            await generator.generate_word_frequency_and_cloud(comment_data, prefix)
            utils.logger.info(f"[UrlCheckCrawler] 词云已保存: {prefix}")
        except Exception as e:
            utils.logger.error(f"[UrlCheckCrawler] 词云生成失败: {e}")

    # ────────────────── 浏览器管理 ──────────────────

    async def _get_playwright_proxy(self) -> Optional[Dict]:
        """当 ENABLE_IP_PROXY=True 时，从代理池获取一个代理并转成 Playwright 格式"""
        if not config.ENABLE_IP_PROXY:
            return None
        try:
            from proxy.proxy_ip_pool import create_ip_pool
            if not hasattr(self, "_ip_pool") or self._ip_pool is None:
                self._ip_pool = await create_ip_pool(
                    ip_pool_count=config.IP_PROXY_POOL_COUNT,
                    enable_validate_ip=True,
                )
            ip_info = await self._ip_pool.get_or_refresh_proxy()
            proxy_url = f"http://{ip_info.ip}:{ip_info.port}"
            pw_proxy: Dict = {"server": proxy_url}
            if ip_info.user and ip_info.password:
                pw_proxy["username"] = ip_info.user
                pw_proxy["password"] = ip_info.password
            utils.logger.info(f"[UrlCheckCrawler] 使用IP代理: {ip_info.ip}:{ip_info.port}")
            return pw_proxy
        except Exception as e:
            utils.logger.warning(f"[UrlCheckCrawler] 获取IP代理失败，直连: {e}")
            return None

    async def _create_platform_client(
        self, platform: str, playwright: Playwright
    ):
        """
        为指定平台创建 Client，复用 MediaCrawler 已有的浏览器启动和登录逻辑。
        返回 (client_instance, cleanup_func)
        """
        # 获取代理配置（如果启用）
        pw_proxy = await self._get_playwright_proxy()

        if config.ENABLE_CDP_MODE:
            original_platform = config.PLATFORM
            config.PLATFORM = platform
            try:
                self.cdp_manager = CDPBrowserManager()
                self.browser_context = await self.cdp_manager.launch_and_connect(
                    playwright=playwright,
                    playwright_proxy=pw_proxy,
                    user_agent=None,
                    headless=config.CDP_HEADLESS,
                )
                await self.cdp_manager.add_stealth_script()
            finally:
                config.PLATFORM = original_platform
        else:
            chromium = playwright.chromium
            user_data_dir = os.path.join(
                os.getcwd(), "browser_data",
                config.USER_DATA_DIR % platform
            )
            launch_kwargs = {
                "user_data_dir": user_data_dir,
                "accept_downloads": True,
                "headless": config.HEADLESS,
                "viewport": {"width": 1920, "height": 1080},
            }
            if pw_proxy:
                launch_kwargs["proxy"] = pw_proxy
            self.browser_context = await chromium.launch_persistent_context(
                **launch_kwargs
            )
            await self.browser_context.add_init_script(path="libs/stealth.min.js")

        # 创建页面并导航到平台首页
        self.context_page = await self.browser_context.new_page()

        try:
            client = await self._init_platform_client(platform)
            return client, None
        except Exception as e:
            utils.logger.error(
                f"[UrlCheckCrawler] 平台 [{platform}] Client 初始化失败: {e}"
            )
            return None, None

    async def _init_platform_client(self, platform: str):
        """根据平台创建对应的 API Client 并完成登录（或通过 Cookie 池直接注入）"""

        # ── Cookie 池快速路径：跳过浏览器登录，直接注入 Cookie ──
        if getattr(config, "ENABLE_COOKIE_POOL", False):
            return await self._init_client_from_cookie_pool(platform)

        if platform == "dy":
            await self.context_page.goto("https://www.douyin.com")
            from media_platform.douyin.client import DouYinClient
            from media_platform.douyin.login import DouYinLogin

            cookie_urls = [
                "https://douyin.com", "https://www.douyin.com",
                "https://creator.douyin.com",
            ]
            cookie_str, cookie_dict = await utils.convert_browser_context_cookies(
                self.browser_context, urls=cookie_urls
            )
            client = DouYinClient(
                headers={
                    "User-Agent": await self.context_page.evaluate("() => navigator.userAgent"),
                    "Cookie": cookie_str,
                    "Host": "www.douyin.com",
                    "Origin": "https://www.douyin.com/",
                    "Referer": "https://www.douyin.com/",
                    "Content-Type": "application/json;charset=UTF-8",
                },
                playwright_page=self.context_page,
                cookie_dict=cookie_dict,
            )
            # 检查登录状态
            if not await client.pong(browser_context=self.browser_context):
                login_obj = DouYinLogin(
                    login_type=config.LOGIN_TYPE,
                    login_phone="",
                    browser_context=self.browser_context,
                    context_page=self.context_page,
                    cookie_str=config.COOKIES,
                )
                await login_obj.begin()
                await client.update_cookies(
                    browser_context=self.browser_context,
                    urls=cookie_urls,
                )
            return client

        elif platform == "bili":
            await self.context_page.goto("https://www.bilibili.com")
            from media_platform.bilibili.client import BilibiliClient
            from media_platform.bilibili.login import BilibiliLogin

            cookie_urls = ["https://www.bilibili.com"]
            cookie_str, cookie_dict = await utils.convert_browser_context_cookies(
                self.browser_context, urls=cookie_urls
            )
            client = BilibiliClient(
                headers={
                    "User-Agent": utils.get_user_agent(),
                    "Cookie": cookie_str,
                    "Origin": "https://www.bilibili.com",
                    "Referer": "https://www.bilibili.com",
                    "Content-Type": "application/json;charset=UTF-8",
                },
                playwright_page=self.context_page,
                cookie_dict=cookie_dict,
            )
            if not await client.pong():
                login_obj = BilibiliLogin(
                    login_type=config.LOGIN_TYPE,
                    login_phone="",
                    browser_context=self.browser_context,
                    context_page=self.context_page,
                    cookie_str=config.COOKIES,
                )
                await login_obj.begin()
                await client.update_cookies(
                    browser_context=self.browser_context,
                    urls=cookie_urls,
                )
            return client

        elif platform == "ks":
            await self.context_page.goto("https://www.kuaishou.com")
            from media_platform.kuaishou.client import KuaiShouClient
            from media_platform.kuaishou.login import KuaishouLogin

            cookie_urls = ["https://www.kuaishou.com"]
            cookie_str, cookie_dict = await utils.convert_browser_context_cookies(
                self.browser_context, urls=cookie_urls
            )
            client = KuaiShouClient(
                headers={
                    "User-Agent": utils.get_user_agent(),
                    "Cookie": cookie_str,
                    "Origin": "https://www.kuaishou.com",
                    "Referer": "https://www.kuaishou.com/search",
                    "Content-Type": "application/json;charset=UTF-8",
                },
                playwright_page=self.context_page,
                cookie_dict=cookie_dict,
            )
            if not await client.pong():
                login_obj = KuaishouLogin(
                    login_type=config.LOGIN_TYPE,
                    login_phone="",
                    browser_context=self.browser_context,
                    context_page=self.context_page,
                    cookie_str=config.COOKIES,
                )
                await login_obj.begin()
                await client.update_cookies(
                    browser_context=self.browser_context,
                    urls=cookie_urls,
                )
            return client

        elif platform == "toutiao":
            await self.context_page.goto("https://www.toutiao.com")
            from media_platform.toutiao.client import ToutiaoClient
            from media_platform.toutiao.login import ToutiaoLogin

            cookie_urls = ["https://www.toutiao.com"]
            cookie_str, cookie_dict = await utils.convert_browser_context_cookies(
                self.browser_context, urls=cookie_urls
            )
            client = ToutiaoClient(
                headers={
                    "User-Agent": await self.context_page.evaluate("() => navigator.userAgent"),
                    "Cookie": cookie_str,
                    "Host": "www.toutiao.com",
                    "Referer": "https://www.toutiao.com/",
                },
                playwright_page=self.context_page,
                cookie_dict=cookie_dict,
            )
            if not await client.pong():
                login_obj = ToutiaoLogin(
                    login_type=config.LOGIN_TYPE,
                    login_phone="",
                    browser_context=self.browser_context,
                    context_page=self.context_page,
                    cookie_str=config.COOKIES,
                )
                await login_obj.begin()
                await client.update_cookies(
                    browser_context=self.browser_context,
                    urls=cookie_urls,
                )
            return client

        elif platform == "wb":
            await self.context_page.goto("https://m.weibo.cn")
            from media_platform.weibo.client import WeiboClient
            from media_platform.weibo.login import WeiboLogin

            cookie_urls = ["https://m.weibo.cn", "https://weibo.com"]
            cookie_str, cookie_dict = await utils.convert_browser_context_cookies(
                self.browser_context, urls=cookie_urls
            )
            client = WeiboClient(
                headers={
                    "User-Agent": utils.get_user_agent(),
                    "Cookie": cookie_str,
                    "accept": "application/json, text/plain, */*",
                    "Referer": "https://m.weibo.cn",
                },
                playwright_page=self.context_page,
                cookie_dict=cookie_dict,
            )
            if not await client.pong():
                login_obj = WeiboLogin(
                    login_type=config.LOGIN_TYPE,
                    login_phone="",
                    browser_context=self.browser_context,
                    context_page=self.context_page,
                    cookie_str=config.COOKIES,
                )
                await login_obj.begin()
                await client.update_cookies(
                    browser_context=self.browser_context,
                    urls=cookie_urls,
                )
            return client

        elif platform == "xhs":
            await self.context_page.goto("https://www.xiaohongshu.com")
            from media_platform.xhs.client import XiaoHongShuClient
            from media_platform.xhs.login import XiaoHongShuLogin

            cookie_urls = [
                "https://www.xiaohongshu.com",
                "https://creator.xiaohongshu.com",
            ]
            cookie_str, cookie_dict = await utils.convert_browser_context_cookies(
                self.browser_context, urls=cookie_urls
            )
            client = XiaoHongShuClient(
                headers={
                    "User-Agent": utils.get_user_agent(),
                    "Cookie": cookie_str,
                    "Origin": "https://www.xiaohongshu.com",
                    "Referer": "https://www.xiaohongshu.com",
                    "Content-Type": "application/json;charset=UTF-8",
                },
                playwright_page=self.context_page,
                cookie_dict=cookie_dict,
            )
            if not await client.pong():
                login_obj = XiaoHongShuLogin(
                    login_type=config.LOGIN_TYPE,
                    login_phone="",
                    browser_context=self.browser_context,
                    context_page=self.context_page,
                    cookie_str=config.COOKIES,
                )
                await login_obj.begin()
                await client.update_cookies(
                    browser_context=self.browser_context,
                    urls=cookie_urls,
                )
            return client

        else:
            utils.logger.warning(
                f"[UrlCheckCrawler] 平台 [{platform}] 暂不支持 url_check 模式"
            )
            return None

    # ── 平台 → 客户端创建配置映射（用于 Cookie 池模式）──
    _PLATFORM_CLIENT_MAP = {
        "dy": {
            "home_url": "https://www.douyin.com",
            "client_path": "media_platform.douyin.client.DouYinClient",
            "headers_base": {
                "Host": "www.douyin.com",
                "Origin": "https://www.douyin.com/",
                "Referer": "https://www.douyin.com/",
                "Content-Type": "application/json;charset=UTF-8",
            },
        },
        "bili": {
            "home_url": "https://www.bilibili.com",
            "client_path": "media_platform.bilibili.client.BilibiliClient",
            "headers_base": {
                "Origin": "https://www.bilibili.com",
                "Referer": "https://www.bilibili.com",
                "Content-Type": "application/json;charset=UTF-8",
            },
        },
        "ks": {
            "home_url": "https://www.kuaishou.com",
            "client_path": "media_platform.kuaishou.client.KuaiShouClient",
            "headers_base": {
                "Origin": "https://www.kuaishou.com",
                "Referer": "https://www.kuaishou.com/search",
                "Content-Type": "application/json;charset=UTF-8",
            },
        },
        "toutiao": {
            "home_url": "https://www.toutiao.com",
            "client_path": "media_platform.toutiao.client.ToutiaoClient",
            "headers_base": {
                "Host": "www.toutiao.com",
                "Referer": "https://www.toutiao.com/",
            },
        },
        "wb": {
            "home_url": "https://m.weibo.cn",
            "client_path": "media_platform.weibo.client.WeiboClient",
            "headers_base": {
                "accept": "application/json, text/plain, */*",
                "Referer": "https://m.weibo.cn",
            },
        },
        "xhs": {
            "home_url": "https://www.xiaohongshu.com",
            "client_path": "media_platform.xhs.client.XiaoHongShuClient",
            "headers_base": {
                "Origin": "https://www.xiaohongshu.com",
                "Referer": "https://www.xiaohongshu.com",
                "Content-Type": "application/json;charset=UTF-8",
            },
        },
    }

    async def _init_client_from_cookie_pool(self, platform: str):
        """Cookie 池模式：跳过浏览器登录，直接用预置 Cookie 构建 Client"""
        from importlib import import_module
        from proxy.cookie_pool import cookie_pool

        pcfg = self._PLATFORM_CLIENT_MAP.get(platform)
        if not pcfg:
            utils.logger.warning(
                f"[CookiePool] 平台 [{platform}] 不在 Cookie 池映射中"
            )
            return None

        cookie_str = cookie_pool.get_cookie(platform)
        if not cookie_str:
            utils.logger.error(
                f"[CookiePool] 平台 [{platform}] 无可用 Cookie，跳过"
            )
            return None

        # 将 Cookie 注入到 browser_context，使页面具有登录态
        cookie_dict = cookie_pool.parse_cookie_string(cookie_str)
        home_url = pcfg["home_url"]
        # 提取根域名（去掉 www. 和协议），确保 cookie 对所有子域生效
        domain = home_url.replace("https://", "").replace("http://", "").rstrip("/")
        if domain.startswith("www."):
            domain = domain[4:]
        if domain.startswith("m."):
            domain = domain[2:]
        browser_cookies = []
        for name, value in cookie_dict.items():
            browser_cookies.append({
                "name": name,
                "value": value,
                "domain": f".{domain}",
                "path": "/",
                "secure": True,
                "httpOnly": False,
            })
        if browser_cookies:
            await self.browser_context.add_cookies(browser_cookies)
            utils.logger.info(
                f"[CookiePool] 平台 [{platform}] 已注入 {len(browser_cookies)} 条Cookie到浏览器"
            )

        # 导航到平台首页（为 DOM 提取等操作提供页面上下文）
        await self.context_page.goto(home_url)
        await asyncio.sleep(2)

        # 动态导入 Client 类
        module_path, class_name = pcfg["client_path"].rsplit(".", 1)
        mod = import_module(module_path)
        client_cls = getattr(mod, class_name)

        headers = {
            "User-Agent": utils.get_user_agent(),
            "Cookie": cookie_str,
            **pcfg["headers_base"],
        }

        client = client_cls(
            headers=headers,
            playwright_page=self.context_page,
            cookie_dict=cookie_dict,
        )

        utils.logger.info(
            f"[CookiePool] 平台 [{platform}] 使用 Cookie: "
            f"{cookie_pool.get_current_id(platform)}"
        )
        return client

    async def _cleanup_browser(self):
        """关闭当前浏览器会话"""
        try:
            if self.cdp_manager:
                await self.cdp_manager.cleanup()
                self.cdp_manager = None
            elif self.browser_context:
                await self.browser_context.close()
                self.browser_context = None
        except Exception as e:
            utils.logger.warning(f"[UrlCheckCrawler] 清理浏览器时异常: {e}")

    # ── AbstractCrawler 接口实现（url_check 模式不使用，但需实现接口）──

    async def search(self):
        pass

    async def launch_browser(self, chromium, playwright_proxy, user_agent, headless=True):
        pass
