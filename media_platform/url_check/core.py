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
import random
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

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

        # 4. 多平台并行处理：每个平台独立浏览器，互不干扰，大幅提升吞吐量
        # 对每个平台仍然是逐条处理，不增加单平台风控压力
        parallel_enabled = getattr(config, "URLCHECK_PARALLEL_PLATFORMS", True)
        active_platforms = [p for p in _PLATFORM_ORDER if p in groups]

        if parallel_enabled and len(active_platforms) > 1:
            utils.logger.info(
                f"[UrlCheckCrawler] 启用多平台并行模式，"
                f"同时处理 {len(active_platforms)} 个平台: {active_platforms}"
            )
            await self._process_platforms_parallel(active_platforms, groups, mode)
        else:
            # 单平台或未启用并行时，走原有顺序逻辑
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

    async def _process_platforms_parallel(
        self, platforms: List[str], groups: Dict[str, List[Dict]], mode: str
    ):
        """
        多平台并行处理：为每个平台创建独立的 Crawler 实例和浏览器上下文，
        使用 asyncio.gather 同时处理。每个平台内部仍逐条顺序处理，
        保证每个账号同一时间只有一个请求，不增加风控风险。
        """
        async def _platform_worker(platform: str, url_rows: List[Dict]):
            """单个平台的工作协程，拥有独立的浏览器实例"""
            # 每个平台使用独立的 Crawler 实例，避免共享浏览器上下文
            worker = UrlCheckCrawler()
            utils.logger.info(
                f"[Parallel] 平台 [{platform}] 启动独立浏览器，"
                f"待处理 {len(url_rows)} 条"
            )
            try:
                await worker._process_platform(platform, url_rows, mode)
            except Exception as e:
                utils.logger.error(
                    f"[Parallel] 平台 [{platform}] 并行处理异常: {e}"
                )
            return worker

        # 并行启动所有平台
        tasks = []
        for platform in platforms:
            url_rows = groups[platform]
            tasks.append(_platform_worker(platform, url_rows))

        workers = await asyncio.gather(*tasks, return_exceptions=True)

        # 合并所有 worker 的结果到主实例
        for result in workers:
            if isinstance(result, Exception):
                utils.logger.error(f"[Parallel] 某平台 worker 异常: {result}")
                continue
            if isinstance(result, UrlCheckCrawler):
                self._all_results.extend(result._all_results)
                self._collected_comment_texts.extend(result._collected_comment_texts)

    async def _process_platform(
        self, platform: str, url_rows: List[Dict], mode: str,
        on_result=None,
    ):
        """
        处理单个平台的所有 URL。
        流程：HTTP 预检(免浏览器) → 判断并发数 → 单浏览器或多浏览器并发处理
        on_result: 可选回调，每条URL处理完后立即调用 on_result(row_dict)，用于实时更新前端日志
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
                # 实时回调：HTTP预检结果
                if on_result:
                    on_result(row)
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

        # ── 确定并发数，决定走单浏览器还是多浏览器 ──
        concurrency = self._resolve_concurrency(platform, len(need_browser_rows))

        if concurrency > 1:
            await self._process_platform_concurrent(
                platform, need_browser_rows, mode, concurrency, on_result
            )
        else:
            await self._process_platform_single(
                platform, need_browser_rows, mode, on_result
            )

    async def _process_platform_single(
        self, platform: str, need_browser_rows: List[Dict], mode: str,
        on_result=None,
    ):
        """单浏览器处理模式（复用 _browser_worker，确保与并发模式行为一致）"""
        cookie_free = self._is_urlcheck_cookie_free(platform)
        cookie_purpose = self._get_urlcheck_cookie_purpose(platform)

        # 获取Cookie
        cookie_info: Optional[tuple] = None
        if not cookie_free and getattr(config, "ENABLE_COOKIE_POOL", False):
            from proxy.cookie_pool import cookie_pool
            allocated = cookie_pool.allocate_cookies(platform, 1, purpose=cookie_purpose)
            if allocated:
                cookie_info = allocated[0]

        used_cookie_ids = {cookie_info[0]} if cookie_info else set()

        url_queue: asyncio.Queue = asyncio.Queue()
        for row in need_browser_rows:
            await url_queue.put(row)

        result = await self._browser_worker(
            platform, mode, url_queue, cookie_info, 1, used_cookie_ids,
            on_result=on_result,
        )
        if isinstance(result, tuple) and len(result) == 2:
            results_list, _exhausted_cookie_id = result
        else:
            results_list = result

        if isinstance(results_list, list):
            self._all_results.extend(results_list)

    async def _process_platform_concurrent(
        self, platform: str, need_browser_rows: List[Dict], mode: str, concurrency: int,
        on_result=None,
    ):
        """
        多浏览器并发处理模式。
        创建共享队列，为每个浏览器分配独立Cookie，通过 asyncio.gather 并行处理。
        """
        cookie_free = self._is_urlcheck_cookie_free(platform)
        cookie_purpose = self._get_urlcheck_cookie_purpose(platform)

        utils.logger.info(
            f"[UrlCheckCrawler] 平台 [{platform}] 启用多浏览器并发: "
            f"concurrency={concurrency}, cookie_free={cookie_free}"
        )

        # 分配Cookie（cookie_free 平台不需要）
        cookie_list: List[Optional[tuple]] = []
        used_cookie_ids: set = set()

        if cookie_free:
            cookie_list = [None] * concurrency
        else:
            from proxy.cookie_pool import cookie_pool
            allocated = cookie_pool.allocate_cookies(
                platform, concurrency, purpose=cookie_purpose
            )
            cookie_list = allocated
            used_cookie_ids = {c[0] for c in allocated}
            # 实际并发受限于分配到的Cookie数
            concurrency = len(cookie_list)

        if concurrency < 1:
            utils.logger.warning(
                f"[UrlCheckCrawler] 平台 [{platform}] 无可用Cookie，回退单浏览器模式"
            )
            await self._process_platform_single(platform, need_browser_rows, mode, on_result)
            return

        # 创建共享URL队列
        url_queue: asyncio.Queue = asyncio.Queue()
        for row in need_browser_rows:
            await url_queue.put(row)

        # 启动多个 Worker
        tasks = []
        for i, cookie_info in enumerate(cookie_list):
            tasks.append(
                self._browser_worker(
                    platform, mode, url_queue,
                    cookie_info, i + 1, used_cookie_ids,
                    on_result=on_result,
                )
            )

        all_worker_results = await asyncio.gather(*tasks, return_exceptions=True)

        # strict 模式收集已跑满的 Cookie，重分配时排除
        exhausted_cookie_ids: set = set()

        for result in all_worker_results:
            if isinstance(result, Exception):
                utils.logger.error(
                    f"[UrlCheckCrawler] 平台 [{platform}] Worker 异常: {result}"
                )
                continue
            # Worker 返回 (results_list, exhausted_cookie_id)
            if isinstance(result, tuple) and len(result) == 2:
                results_list, ex_id = result
                if isinstance(results_list, list):
                    self._all_results.extend(results_list)
                if ex_id:
                    exhausted_cookie_ids.add(ex_id)
            elif isinstance(result, list):
                self._all_results.extend(result)

        max_rounds = getattr(config, "MAX_REDISTRIBUTE_ROUNDS", 3)
        redistribute_round = 0

        while not url_queue.empty() and not cookie_free:
            redistribute_round += 1
            remaining_count = url_queue.qsize()

            if redistribute_round > max_rounds:
                # 超过最大轮次，标记剩余为无效
                remaining = []
                while not url_queue.empty():
                    try:
                        remaining.append(url_queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                utils.logger.warning(
                    f"[UrlCheckCrawler] 平台 [{platform}] 重试{max_rounds}轮后仍剩余"
                    f" {len(remaining)} 条URL，标记为无效"
                )
                for row in remaining:
                    row["_fetch_fail_reason"] = "max_rounds_exhausted"
                    # 业务约定: 0/NULL=未检测(会被重新捞)，1=有效，2=无效
                    await self._save_result(row, is_valid=2)
                break

            utils.logger.info(
                f"[UrlCheckCrawler] 平台 [{platform}] 第{redistribute_round}轮重分配："
                f"剩余 {remaining_count} 条URL，重新分配Cookie"
            )

            # 重新从Cookie池获取可用Cookie（排除已跑满的）
            from proxy.cookie_pool import cookie_pool
            new_allocated = cookie_pool.allocate_cookies(
                platform, concurrency, exclude_ids=exhausted_cookie_ids,
                purpose=cookie_purpose,
            )
            if not new_allocated:
                remaining = []
                while not url_queue.empty():
                    try:
                        remaining.append(url_queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                utils.logger.warning(
                    f"[UrlCheckCrawler] 平台 [{platform}] 无可用Cookie，"
                    f"剩余 {len(remaining)} 条标记为无效"
                )
                for row in remaining:
                    row["_fetch_fail_reason"] = "no_cookie_available"
                    await self._save_result(row, is_valid=2)
                break

            new_used = {c[0] for c in new_allocated}
            retry_tasks = []
            for i, ci in enumerate(new_allocated):
                retry_tasks.append(
                    self._browser_worker(
                        platform, mode, url_queue,
                        ci, i + 1, new_used,
                        on_result=on_result,
                    )
                )

            retry_results = await asyncio.gather(*retry_tasks, return_exceptions=True)
            for result in retry_results:
                if isinstance(result, Exception):
                    utils.logger.error(
                        f"[UrlCheckCrawler] 平台 [{platform}] 重分配Worker异常: {result}"
                    )
                    continue
                if isinstance(result, tuple) and len(result) == 2:
                    results_list, ex_id = result
                    if isinstance(results_list, list):
                        self._all_results.extend(results_list)
                    if ex_id:
                        exhausted_cookie_ids.add(ex_id)
                elif isinstance(result, list):
                    self._all_results.extend(result)

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

        cookie_free = self._is_urlcheck_cookie_free(platform)
        cookie_purpose = self._get_urlcheck_cookie_purpose(platform)
        # 最多尝试次数（首次 + 切换重试）
        max_attempts = 2 if getattr(config, "ENABLE_COOKIE_POOL", False) and not cookie_free else 1

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
                        # DOM提取不再重新导航（复用_fetch_detail已加载的页面）
                        metrics = await client.get_article_metrics_from_dom(
                            content_id, original_url=None
                        )
                        # 如果 DOM 提取到了标题，保存到 metrics 中供后续使用
                        if metrics and metrics.get("title"):
                            row["_title"] = metrics.pop("title")
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
                    if (
                        getattr(config, "ENABLE_COOKIE_POOL", False)
                        and not cookie_free
                        and attempt < max_attempts - 1
                    ):
                        from proxy.cookie_pool import cookie_pool
                        current_cookie_id = cookie_pool.get_current_id(platform)
                        new_cookie = cookie_pool.get_another_cookie(
                            platform, exclude_id=current_cookie_id,
                            purpose=cookie_purpose,
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
                if not getattr(config, "ENABLE_COOKIE_POOL", False) or cookie_free:
                    break

                from proxy.cookie_pool import cookie_pool, FailureLevel

                current_cookie_id = cookie_pool.get_current_id(platform)

                if attempt < max_attempts - 1:
                    has_more = cookie_pool.report_failure(
                        platform, FailureLevel.FATAL, purpose=cookie_purpose
                    )
                    if has_more:
                        new_cookie = cookie_pool.get_another_cookie(
                            platform, exclude_id=current_cookie_id,
                            purpose=cookie_purpose,
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
                    cookie_pool.report_failure(
                        platform, FailureLevel.FATAL, purpose=cookie_purpose
                    )
                    break

            except asyncio.TimeoutError:
                utils.logger.warning(
                    f"[UrlCheckCrawler] id={row_id} 请求超时(attempt={attempt+1})"
                )
                if (
                    getattr(config, "ENABLE_COOKIE_POOL", False)
                    and not cookie_free
                    and attempt < max_attempts - 1
                ):
                    from proxy.cookie_pool import cookie_pool
                    current_cookie_id = cookie_pool.get_current_id(platform)
                    new_cookie = cookie_pool.get_another_cookie(
                        platform, exclude_id=current_cookie_id,
                        purpose=cookie_purpose,
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
        # --- 短链/变体URL重定向解析 ---
        # 微博非标准URL（ttarticle长微博、tv/show视频）：通过原始URL直接获取
        if platform == "wb" and (not content_id or str(content_id).startswith("__")):
            utils.logger.info(
                f"[UrlCheckCrawler] 微博非标准URL，通过原始URL获取: {url}"
            )
            try:
                result = await client.get_note_info_by_url(url)
                if result:
                    mblog = result.get("mblog", {})
                    real_mid = mblog.get("mid") or mblog.get("id")
                    if real_mid and row is not None:
                        row["_content_id"] = str(real_mid)
                    return result
            except Exception as e:
                utils.logger.warning(
                    f"[UrlCheckCrawler] 微博原始URL获取失败: {url}, err: {e}"
                )
            if row is not None:
                row["_fetch_fail_reason"] = self._FETCH_FAIL_CONTENT
            return None

        if not content_id:
            # 抖音/B站等短链（v.douyin.com、b23.tv）需要重定向解析
            if platform in ("dy", "bili"):
                resolved_id = await self._resolve_short_link(url, platform)
                if resolved_id:
                    content_id = resolved_id
                    utils.logger.info(
                        f"[UrlCheckCrawler] 短链重定向解析: {url} → {content_id}"
                    )
            if not content_id:
                utils.logger.warning(
                    f"[UrlCheckCrawler] 无法从 URL 提取 content_id: {url}"
                )
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
                    # 第三方跳转链接保留原始URL导航
                    if any(d in url for d in ("zjurl.cn", "weitoutiao", "ixigua.com")):
                        nav_url = url
                    else:
                        # /i{id}/ 旧版短链在 headless 模式下会被反爬拦截重定向到首页，
                        # 必须转换为 /article/ 或 /video/ 正规路径
                        nav_url = None

                    if nav_url:
                        result = await client.get_article_info(
                            content_id, original_url=nav_url
                        )
                    else:
                        # 先尝试 /article/ 路径（默认）
                        result = await client.get_article_info(content_id)

                        # /article/ 路径失败（None或内容无效）时回退 /video/ 路径，
                        # 因为头条内容可能是视频，/article/ 路径无法访问视频内容
                        need_fallback = (result is None)
                        if result and not need_fallback:
                            from tools.validity_checker import check_api_json_validity, is_invalid
                            raw_json = result.get("raw_json") or {}
                            status_check = check_api_json_validity(platform, raw_json)
                            need_fallback = is_invalid(status_check)

                        if need_fallback:
                            utils.logger.info(
                                f"[UrlCheckCrawler] toutiao /article/ 路径无效，回退 /video/ 路径"
                            )
                            video_url = f"https://www.toutiao.com/video/{content_id}/"
                            result = await client.get_article_info(
                                content_id, original_url=video_url
                            )

                    # DOM 检测失效关键词 + 首页重定向检测
                    if hasattr(client, "playwright_page"):
                        dead_keywords = [
                            "内容已删除", "文章不存在", "该内容已下架",
                            "页面不存在", "内容不存在", "404 Not Found",
                            "抱歉，你访问的内容不存在", "内容正在审核中",
                            "此内容因违规无法查看", "该文章已被删除",
                        ]
                        # 头条首页特征：页面被重定向到首页，说明内容已失效
                        homepage_indicators = [
                            "下载头条APP关于头条反馈侵权投诉",
                            "关注\n推荐\n",
                        ]

                        page_text = await client.playwright_page.evaluate(
                            "() => document.body?.innerText?.substring(0, 1000) || ''"
                        )
                        utils.logger.info(
                            f"[UrlCheckCrawler] toutiao page_text({len(page_text)}字): "
                            f"{page_text[:80]}"
                        )

                        is_dead = False
                        stripped = page_text.strip().lower()

                        # 检查是否为空白页/error/404
                        if stripped in ("error", "", "404"):
                            is_dead = True
                        else:
                            # 检查失效关键词
                            for kw in dead_keywords:
                                if kw in page_text:
                                    is_dead = True
                                    break

                        # 检查是否被重定向到首页（内容被删除后头条跳转首页）
                        if not is_dead:
                            for indicator in homepage_indicators:
                                if indicator in page_text:
                                    # 进一步确认：首页不会包含当前content_id相关内容
                                    final_url = client.playwright_page.url
                                    if content_id not in final_url:
                                        utils.logger.info(
                                            f"[UrlCheckCrawler] toutiao 检测到首页重定向"
                                            f"（内容已失效）: {content_id}"
                                        )
                                        is_dead = True
                                    break

                        if is_dead:
                            # 二次确认：用原始URL重新导航，避免/video/路径导致的误判
                            utils.logger.info(
                                f"[UrlCheckCrawler] toutiao 首次检测疑似失效，"
                                f"等待后二次确认: {content_id}"
                            )
                            await asyncio.sleep(2)
                            try:
                                # 使用原始/i{id}/ URL 重新确认（不转换为/video/）
                                confirm_url = url
                                await client.playwright_page.goto(
                                    confirm_url, wait_until="domcontentloaded", timeout=15000
                                )
                                await asyncio.sleep(3)
                                page_text2 = await client.playwright_page.evaluate(
                                    "() => document.body?.innerText?.substring(0, 1000) || ''"
                                )
                                stripped2 = page_text2.strip().lower()
                                still_dead = stripped2 in ("error", "", "404")
                                if not still_dead:
                                    for kw in dead_keywords:
                                        if kw in page_text2:
                                            still_dead = True
                                            break
                                if not still_dead:
                                    # 检查是否又跳到首页
                                    for indicator in homepage_indicators:
                                        if indicator in page_text2:
                                            final_url2 = client.playwright_page.url
                                            if content_id not in final_url2:
                                                still_dead = True
                                            break
                                if not still_dead:
                                    utils.logger.info(
                                        f"[UrlCheckCrawler] toutiao 二次确认: 内容存活 {content_id}"
                                    )
                                    return result
                            except Exception as e:
                                utils.logger.warning(
                                    f"[UrlCheckCrawler] toutiao 二次确认异常: {e}"
                                )

                            utils.logger.info(
                                f"[UrlCheckCrawler] toutiao 二次确认失效: {content_id}"
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

    async def _resolve_short_link(self, url: str, platform: str) -> Optional[str]:
        """
        通用短链重定向解析（b23.tv、v.douyin.com 等）。
        跟踪302重定向到最终URL，再用 url_detector 提取 content_id。
        """
        from tools.url_detector import _extract_content_id
        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=10, verify=False,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            ) as client:
                resp = await client.get(url)
                final_url = str(resp.url)
                parsed = urlparse(final_url)
                content_id = _extract_content_id(
                    platform, final_url, parsed.path, parsed.query or ""
                )
                if content_id:
                    utils.logger.info(
                        f"[UrlCheckCrawler] 通用短链解析: {url[:60]} → {content_id}"
                    )
                    return content_id
        except Exception as e:
            utils.logger.warning(
                f"[UrlCheckCrawler] 通用短链解析失败: {url[:60]}, err: {e}"
            )
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

    # ────────────────── 同平台多浏览器并发 ──────────────────

    def _resolve_concurrency(self, platform: str, url_count: int) -> int:
        """
        计算指定平台的实际浏览器并发数。
        - 链接检测免 Cookie 平台：直接按 PLATFORM_CONCURRENCY 配置值（不受账号数量限制）
        - 其他平台：按账号/公开详情用途统计可用 Cookie，再限制并发
        """
        platform_concurrency = getattr(config, "PLATFORM_CONCURRENCY", {})
        configured = platform_concurrency.get(platform, 1)

        if self._is_urlcheck_cookie_free(platform):
            # 无Cookie限制，但不需要超过URL数量
            actual = min(configured, url_count)
        else:
            # 受Cookie数量限制
            if getattr(config, "ENABLE_COOKIE_POOL", False):
                from proxy.cookie_pool import cookie_pool
                purpose = self._get_urlcheck_cookie_purpose(platform)
                available = cookie_pool.get_available_count(platform, purpose)
            else:
                available = 1
            actual = min(configured, available, url_count)

        # 至少为1
        return max(actual, 1)

    def _is_urlcheck_cookie_free(self, platform: str) -> bool:
        """链接检测专用免 Cookie 判断；不影响投诉举报等账号态流程。"""
        detail_free = getattr(config, "URLCHECK_DETAIL_COOKIE_FREE_PLATFORMS", None)
        if detail_free is None:
            detail_free = getattr(config, "COOKIE_FREE_PLATFORMS", [])
        return platform in detail_free

    def _get_urlcheck_cookie_purpose(self, platform: str) -> str:
        """链接检测按平台选择 Cookie 能力，快手/微博可使用公开详情 session。"""
        policy = getattr(config, "URLCHECK_DETAIL_COOKIE_PURPOSE", {})
        if platform in self._is_urlcheck_cookie_free_platforms():
            return "none"
        return policy.get(platform, "account")

    def _is_urlcheck_cookie_free_platforms(self) -> set:
        detail_free = getattr(config, "URLCHECK_DETAIL_COOKIE_FREE_PLATFORMS", None)
        if detail_free is None:
            detail_free = getattr(config, "COOKIE_FREE_PLATFORMS", [])
        return set(detail_free)

    async def _browser_worker(
        self,
        platform: str,
        mode: str,
        queue: asyncio.Queue,
        cookie_info: Optional[tuple],
        worker_id: int,
        used_cookie_ids: set,
        on_result=None,
    ) -> List[Dict]:
        """
        单个浏览器工作协程。从共享队列取 URL 处理，直到队列空或达到软上限。
        Cookie 失效时尝试从池中获取新 Cookie 重启浏览器。

        Args:
            platform: 平台标识
            mode: 检测模式
            queue: 共享 URL 队列
            cookie_info: (cookie_id, cookie_str) 或 None（cookie_free平台）
            worker_id: Worker 编号（用于日志标识）
            used_cookie_ids: 所有 Worker 已占用的 Cookie ID 集合（共享引用，用于避免重复分配）
        """
        # 按平台读取单Cookie最大处理数（兼容旧的全局int配置）
        max_urls_cfg = getattr(config, "MAX_URLS_PER_COOKIE", 0)
        if isinstance(max_urls_cfg, dict):
            max_per_cookie = max_urls_cfg.get(platform, 0)
        else:
            max_per_cookie = max_urls_cfg
        base_sleep = getattr(config, "PLATFORM_SLEEP_SEC", {}).get(
            platform, config.CRAWLER_MAX_SLEEP_SEC
        )
        jitter_ratio = getattr(config, "SLEEP_JITTER_RATIO", 0.3)
        cookie_free = self._is_urlcheck_cookie_free(platform)
        cookie_purpose = self._get_urlcheck_cookie_purpose(platform)
        worker_results: List[Dict] = []
        # strict 模式下达到上限的 Cookie ID，供调度层排除
        exhausted_cookie_id: Optional[str] = None
        processed_count = 0
        tag = f"[W{worker_id}-{platform}]"

        current_cookie_id = cookie_info[0] if cookie_info else None
        current_cookie_str = cookie_info[1] if cookie_info else None

        # 拟人化：错峰启动，每个 Worker 随机延迟 0~BROWSER_STAGGER_MAX_SEC 秒
        stagger_max = getattr(config, "BROWSER_STAGGER_MAX_SEC", 3.0)
        if stagger_max > 0 and worker_id > 1:
            delay = random.uniform(0, stagger_max)
            utils.logger.info(f"{tag} 拟人化延迟 {delay:.1f}s 后启动浏览器")
            await asyncio.sleep(delay)

        async with async_playwright() as playwright:
            client = None
            crawler_instance = UrlCheckCrawler()

            try:
                # 创建浏览器和Client
                client = await self._create_worker_client(
                    crawler_instance, platform, playwright,
                    current_cookie_str, cookie_free, worker_id
                )
                if client is None:
                    utils.logger.error(f"{tag} 创建Client失败")
                    return worker_results, exhausted_cookie_id

                utils.logger.info(
                    f"{tag} 启动成功"
                    f"{'' if cookie_free else f', Cookie={current_cookie_id}'}"
                )

                # cookie_batch_count 跟踪当前 Cookie 本轮已处理的URL数
                # cookie_round 跟踪当前 Cookie 已复用的轮次
                cookie_batch_count = 0
                cookie_round = 1

                limit_policy = getattr(config, "COOKIE_LIMIT_POLICY", "cooldown")
                cooldown_sec = getattr(config, "COOKIE_COOLDOWN_SEC", 300)

                while not queue.empty():
                    # 达到单Cookie上限时：按策略处理
                    if max_per_cookie > 0 and cookie_batch_count >= max_per_cookie:
                        if cookie_free:
                            break
                        utils.logger.info(
                            f"{tag} Cookie {current_cookie_id} 第{cookie_round}轮达到上限"
                            f"({max_per_cookie}条)，策略={limit_policy}"
                        )
                        # 达到上限换号，不记FATAL（mark_failure=False）
                        new_client = await self._try_rebind_cookie(
                            crawler_instance, platform, playwright,
                            current_cookie_id, used_cookie_ids, tag,
                            mark_failure=False,
                            purpose=cookie_purpose,
                        )
                        if new_client:
                            client = new_client
                            current_cookie_id = getattr(
                                crawler_instance, "_rebound_cookie_id", current_cookie_id
                            )
                            cookie_batch_count = 0
                            cookie_round = 1
                            utils.logger.info(f"{tag} 已换到Cookie {current_cookie_id}，继续处理")
                        elif limit_policy == "cooldown":
                            # 冷却模式：休息后复用当前Cookie
                            cookie_batch_count = 0
                            cookie_round += 1
                            utils.logger.info(
                                f"{tag} 无备用Cookie，冷却 {cooldown_sec}s 后复用 "
                                f"{current_cookie_id} 开始第{cookie_round}轮"
                            )
                            await asyncio.sleep(cooldown_sec)
                        else:
                            # strict 模式：记录已跑满的 Cookie，供调度层排除
                            exhausted_cookie_id = current_cookie_id
                            utils.logger.info(
                                f"{tag} strict模式，无备用Cookie，Worker停止"
                            )
                            break

                    try:
                        row = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

                    row["_worker_id"] = worker_id
                    row["_cookie_id"] = current_cookie_id

                    url = row.get("url", "")
                    utils.logger.info(f"{tag} 处理 id={row['id']} url={url[:50]}...")

                    await crawler_instance._process_single_url(platform, client, row, mode)
                    processed_count += 1
                    cookie_batch_count += 1
                    new_results = crawler_instance._all_results[len(worker_results):]
                    worker_results.extend(new_results)

                    if on_result:
                        for r in new_results:
                            on_result(r)

                    # Cookie 失效检测
                    fail_reason = row.get("_fetch_fail_reason", "")
                    if fail_reason == UrlCheckCrawler._FETCH_FAIL_AUTH and not cookie_free:
                        new_client = await self._try_rebind_cookie(
                            crawler_instance, platform, playwright,
                            current_cookie_id, used_cookie_ids, tag,
                            purpose=cookie_purpose,
                        )
                        if new_client:
                            client = new_client
                            current_cookie_id = getattr(
                                crawler_instance, "_rebound_cookie_id", current_cookie_id
                            )
                            cookie_batch_count = 0
                        else:
                            utils.logger.warning(f"{tag} Cookie失效且无备用Cookie，Worker停止")
                            break

                    # Set-Cookie 回写
                    if not cookie_free and current_cookie_id and hasattr(client, "get_updated_cookie_str"):
                        new_str = client.get_updated_cookie_str()
                        if new_str:
                            from proxy.cookie_pool import cookie_pool
                            cookie_pool.update_cookie_str(platform, current_cookie_id, new_str)

                    actual_sleep = base_sleep * (1 + random.uniform(-jitter_ratio, jitter_ratio))
                    await asyncio.sleep(actual_sleep)

            except Exception as e:
                utils.logger.error(f"{tag} Worker异常: {e}")
            finally:
                await crawler_instance._cleanup_browser()

        utils.logger.info(f"{tag} 完成，处理了 {processed_count} 条URL")
        return worker_results, exhausted_cookie_id

    async def _create_worker_client(
        self,
        crawler_instance: "UrlCheckCrawler",
        platform: str,
        playwright: "Playwright",
        cookie_str: Optional[str],
        cookie_free: bool,
        worker_id: int = 0,
    ):
        """为 Worker 创建浏览器 Client（cookie_free 平台开空白浏览器）"""
        from importlib import import_module

        pw_proxy = await self._get_playwright_proxy()

        # 每个 Worker 使用独立 user_data_dir 避免并发冲突
        chromium = playwright.chromium
        user_data_dir = os.path.join(
            os.getcwd(), "browser_data", f"worker_{platform}_{worker_id}"
        )

        # 拟人化：视口尺寸随机微调，使每个浏览器指纹不同
        vp_offset = getattr(config, "VIEWPORT_RANDOM_OFFSET", 50)
        vp_w = 1920 + random.randint(-vp_offset, vp_offset)
        vp_h = 1080 + random.randint(-vp_offset, vp_offset)

        launch_kwargs = {
            "user_data_dir": user_data_dir,
            "accept_downloads": True,
            "headless": config.HEADLESS,
            "viewport": {"width": vp_w, "height": vp_h},
        }
        if pw_proxy:
            launch_kwargs["proxy"] = pw_proxy

        crawler_instance.browser_context = await chromium.launch_persistent_context(
            **launch_kwargs
        )
        await crawler_instance.browser_context.add_init_script(path="libs/stealth.min.js")
        crawler_instance.context_page = await crawler_instance.browser_context.new_page()

        if cookie_free:
            # 无需Cookie，直接导航到平台首页创建Client
            pcfg = crawler_instance._PLATFORM_CLIENT_MAP.get(platform)
            if not pcfg:
                return None
            home_url = pcfg["home_url"]
            await crawler_instance.context_page.goto(home_url)
            await asyncio.sleep(1)

            module_path, class_name = pcfg["client_path"].rsplit(".", 1)
            mod = import_module(module_path)
            client_cls = getattr(mod, class_name)

            headers = {
                "User-Agent": utils.get_user_agent(),
                "Cookie": "",
                **pcfg["headers_base"],
            }
            client = client_cls(
                headers=headers,
                playwright_page=crawler_instance.context_page,
                cookie_dict={},
            )
            return client
        else:
            # 有Cookie，注入后创建Client
            if not cookie_str:
                return None
            from proxy.cookie_pool import CookiePool
            cookie_dict = CookiePool.parse_cookie_string(cookie_str)

            pcfg = crawler_instance._PLATFORM_CLIENT_MAP.get(platform)
            if not pcfg:
                return None

            home_url = pcfg["home_url"]
            domain = home_url.replace("https://", "").replace("http://", "").rstrip("/")
            if domain.startswith("www."):
                domain = domain[4:]
            if domain.startswith("m."):
                domain = domain[2:]

            browser_cookies = []
            for name, value in cookie_dict.items():
                browser_cookies.append({
                    "name": name, "value": value,
                    "domain": f".{domain}", "path": "/",
                    "secure": True, "httpOnly": False,
                })
            if browser_cookies:
                await crawler_instance.browser_context.add_cookies(browser_cookies)

            await crawler_instance.context_page.goto(home_url)
            await asyncio.sleep(2)

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
                playwright_page=crawler_instance.context_page,
                cookie_dict=cookie_dict,
            )
            return client

    async def _try_rebind_cookie(
        self,
        crawler_instance: "UrlCheckCrawler",
        platform: str,
        playwright: "Playwright",
        current_cookie_id: Optional[str],
        used_cookie_ids: set,
        tag: str,
        mark_failure: bool = True,
        purpose: str = "account",
    ):
        """
        尝试从池中取新 Cookie 重建 Client。
        mark_failure=True 时记录致命失败（真实 auth 失效）；
        mark_failure=False 时仅换号，不记失败（达到使用上限等正常场景）。
        """
        if not getattr(config, "ENABLE_COOKIE_POOL", False):
            return None

        from proxy.cookie_pool import cookie_pool, FailureLevel

        # 只在真实失效时记录 FATAL，按 cookie_id 精确标记
        if mark_failure and current_cookie_id:
            cookie_pool.report_failure_by_id(
                platform, current_cookie_id, FailureLevel.FATAL, purpose=purpose
            )

        # 获取一个未被其他Worker占用的新Cookie
        new_cookie = cookie_pool.get_unused_cookie(platform, used_cookie_ids, purpose=purpose)
        if not new_cookie:
            return None

        new_id, new_str = new_cookie
        used_cookie_ids.add(new_id)
        if current_cookie_id:
            used_cookie_ids.discard(current_cookie_id)

        utils.logger.info(f"{tag} 重新绑定Cookie: {current_cookie_id} → {new_id}")

        # 关闭旧浏览器，创建新Client
        await crawler_instance._cleanup_browser()
        client = await self._create_worker_client(
            crawler_instance, platform, playwright, new_str, False
        )
        # 记录新的cookie_id供外部使用
        crawler_instance._rebound_cookie_id = new_id
        return client

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
