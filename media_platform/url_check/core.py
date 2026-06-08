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
import shutil
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
from tools.url_check_status import (
    STATUS_EXCEPTION,
    STATUS_INVALID,
    STATUS_UNSUPPORTED,
    STATUS_VALID,
    URL_CHECK_PLATFORM_ORDER,
    default_status_reason,
    empty_metrics,
    is_supported_url_check_platform,
    metrics_for_status,
    should_clear_metrics,
    validity_label,
)
from tools.validity_checker import (
    ValidityStatus,
    http_pre_check,
    dom_validity_check,
    check_api_json_validity,
    is_invalid,
)
from var import crawler_type_var

# ── 平台处理顺序（仅包含 url_check 已接入的检测链路）──
_PLATFORM_ORDER = URL_CHECK_PLATFORM_ORDER


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
        self._ip_pool_lock = asyncio.Lock()

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

        # 旧逻辑把未知/未接入平台统一写成“无效”，审核时会误以为链接被删。
        # 新逻辑直接标记“不支持”并跳过浏览器，既省时间也保留真实原因。
        for platform, unsupported_rows in groups.items():
            if is_supported_url_check_platform(platform):
                continue
            for row in unsupported_rows:
                reason = f"平台 {platform or 'unknown'} 暂不支持 url_check，已跳过检测"
                utils.logger.warning(
                    f"[UrlCheckCrawler] 不支持平台 URL id={row['id']} "
                    f"platform={platform} url={row['url']}"
                )
                await self._save_result(
                    row, is_valid=STATUS_UNSUPPORTED, metrics=empty_metrics(), reason=reason
                )

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
        if not is_supported_url_check_platform(platform):
            for row in url_rows:
                reason = f"平台 {platform or 'unknown'} 暂不支持 url_check，已跳过检测"
                await self._save_result(
                    row, is_valid=STATUS_UNSUPPORTED, metrics=empty_metrics(), reason=reason
                )
                if on_result:
                    on_result(row)
            return

        # ── 第一层：HTTP 预检，不需要浏览器/登录，快速筛掉明确失效的 ──
        need_browser_rows = []
        if self._should_use_worker_proxy(platform):
            # 旧逻辑为了避免直连污染，改成 worker 内 HTTP 预检；但头条代理批量下这会多打一轮请求，
            # 且 403/5xx 不能证明内容失效。默认关闭，只在排查 HTTP 层时通过配置打开。
            proxy_precheck = bool(getattr(config, "URLCHECK_PROXY_WORKER_PRECHECK", False))
            for row in url_rows:
                if proxy_precheck:
                    row["_proxy_precheck_in_worker"] = True
                else:
                    row.pop("_proxy_precheck_in_worker", None)
                need_browser_rows.append(row)
        else:
            for row in url_rows:
                url = row.get("url", "")
                status, final_url = await http_pre_check(url, platform)
                if is_invalid(status):
                    utils.logger.info(
                        f"[UrlCheckCrawler] HTTP 预检失效 id={row['id']} "
                        f"reason={status.value} url={url}"
                    )
                    await self._save_result(
                        row, is_valid=STATUS_INVALID, reason=f"HTTP 预检失效: {status.value}"
                    )
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

        await self._mark_remaining_queue_exception(
            url_queue, on_result, "浏览器客户端创建失败或 Worker 提前退出"
        )

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

        if cookie_free:
            await self._mark_remaining_queue_exception(
                url_queue, on_result, "浏览器客户端创建失败或 Worker 提前退出"
            )
            return

        max_rounds = getattr(config, "MAX_REDISTRIBUTE_ROUNDS", 3)
        redistribute_round = 0

        while not url_queue.empty() and not cookie_free:
            redistribute_round += 1
            remaining_count = url_queue.qsize()

            if redistribute_round > max_rounds:
                # 超过最大轮次只能说明当前检测环境无法继续，不能证明内容失效。
                remaining = []
                while not url_queue.empty():
                    try:
                        remaining.append(url_queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                utils.logger.warning(
                    f"[UrlCheckCrawler] 平台 [{platform}] 重试{max_rounds}轮后仍剩余"
                    f" {len(remaining)} 条URL，标记为检测异常"
                )
                for row in remaining:
                    row["_fetch_fail_reason"] = "max_rounds_exhausted"
                    await self._save_result(
                        row,
                        is_valid=STATUS_EXCEPTION,
                        metrics=empty_metrics(),
                        reason="重试轮次耗尽，当前检测环境无法确认链接状态",
                    )
                    if on_result:
                        on_result(row)
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
                    f"剩余 {len(remaining)} 条标记为检测异常"
                )
                for row in remaining:
                    row["_fetch_fail_reason"] = "no_cookie_available"
                    await self._save_result(
                        row,
                        is_valid=STATUS_EXCEPTION,
                        metrics=empty_metrics(),
                        reason="无可用 Cookie/公开会话，当前检测环境无法确认链接状态",
                    )
                    if on_result:
                        on_result(row)
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

    async def _mark_remaining_queue_exception(
        self, url_queue: asyncio.Queue, on_result=None, reason: str = "检测流程提前结束"
    ):
        """为未被 worker 消费的剩余 URL 补齐异常结果，避免任务结果数量少于输入数量。"""
        remaining = []
        while not url_queue.empty():
            try:
                remaining.append(url_queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        for row in remaining:
            row["_fetch_fail_reason"] = self._FETCH_FAIL_RISK
            await self._save_result(
                row, is_valid=STATUS_EXCEPTION, metrics=empty_metrics(), reason=reason
            )
            if on_result:
                on_result(row)

    async def _save_result(
        self,
        row: Dict,
        is_valid: int,
        metrics: Optional[Dict] = None,
        reason: str = "",
    ):
        """统一保存检测结果；特殊状态必须清空指标，避免保留上次检测的旧互动量。"""
        normalized_metrics = metrics_for_status(is_valid, metrics)
        row["_is_valid"] = is_valid
        row["_validity_label"] = validity_label(is_valid)
        row["_status_reason"] = reason or row.get("_status_reason") or default_status_reason(is_valid)
        if normalized_metrics:
            row.setdefault("_metrics", {}).update(normalized_metrics)
        self._all_results.append(row)

        if getattr(config, "URLCHECK_INPUT_SOURCE", "db") != "file":
            await external_db.update_metrics(
                row["id"],
                is_valid=is_valid,
                praise_count=normalized_metrics.get("praise_count"),
                reply_count=normalized_metrics.get("reply_count"),
                visit_count=normalized_metrics.get("visit_count"),
                share_count=normalized_metrics.get("share_count"),
                clear_metrics=should_clear_metrics(is_valid),
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

        if (
            platform == "toutiao"
            and mode in ("validity", "both")
            and getattr(config, "URLCHECK_TOUTIAO_MOBILE_FAST_VALIDITY", True)
            and not getattr(config, "URLCHECK_ENABLE_COMMENTS", False)
        ):
            # 旧逻辑即使只做有效性检测也先打开桌面端页面，批量时容易触发验证码。
            # 移动端能明确 alive/dead 时直接落结果；both 模式只有拿到指标才短路，避免丢数据。
            mobile_state, mobile_result = await self._try_toutiao_mobile_fallback(
                client, content_id, row, "validity 快速确认"
            )
            if mobile_result is not None:
                mobile_metrics = self._get_toutiao_mobile_metrics(mobile_result)
                if mode == "validity" or self._has_any_metric(mobile_metrics):
                    row["_extract_method"] = "移动端公开接口" if mode == "both" else row.get("_extract_method", "")
                    await self._save_result(
                        row,
                        is_valid=STATUS_VALID,
                        metrics=mobile_metrics if mode == "both" else None,
                    )
                    return
            if mobile_state == "dead":
                await self._save_result(
                    row,
                    is_valid=STATUS_INVALID,
                    reason=row.get("_status_reason") or "移动端确认内容不存在或已删除",
                )
                return

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
                        await self._save_result(
                            row, is_valid=STATUS_INVALID, reason=f"接口字段检测失效: {api_status.value}"
                        )
                        return

                    if mode == "validity":
                        await self._save_result(row, is_valid=STATUS_VALID)
                        return

                    # ── 指标提取 ──
                    if platform == "toutiao":
                        mobile_metrics = self._get_toutiao_mobile_metrics(raw_json)
                        if self._has_any_metric(mobile_metrics):
                            # 旧逻辑即使移动端已确认有效，也会再打开桌面 DOM 抓指标；
                            # 代理批量下桌面页可能变登录页，优先用移动端公开接口返回的稳定指标。
                            metrics = mobile_metrics
                            row["_extract_method"] = "移动端公开接口"
                            utils.logger.info(
                                f"[UrlCheckCrawler] id={row_id} 头条使用移动端公开接口提取指标"
                            )
                        else:
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
                    await self._save_result(row, is_valid=STATUS_VALID, metrics=metrics)

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
                    await self._save_result(row, is_valid=STATUS_INVALID, reason="内容不存在或已删除")
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

                # 空白页/App壳页/验证码页更像平台风控或加载失败，不能把链接写成无效。
                if fail_reason == self._FETCH_FAIL_RISK:
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
        fail_reason = row.get("_fetch_fail_reason", "")
        if (
            self._should_use_worker_proxy(platform)
            and fail_reason in (self._FETCH_FAIL_RISK, self._FETCH_FAIL_NETWORK)
        ):
            # 旧逻辑在代理已明显空白/超时后，还会用同一个坏浏览器上下文再开 DOM 页确认，
            # 这会把每次换 IP 重试额外拖慢数秒。代理批量下更合理的是快速返回异常，
            # 由 worker 撤销本次结果并切到新出口重试；最终仍无法确认才保留“检测异常”。
            await self._save_result(
                row,
                is_valid=STATUS_EXCEPTION,
                metrics=empty_metrics(),
                reason=(
                    row.get("_status_reason")
                    or "头条页面为空或疑似风控，当前代理出口无法确认链接状态"
                ),
            )
            return

        dom_status = await self._dom_check_on_new_page(url, platform)
        if is_invalid(dom_status):
            if (
                dom_status == ValidityStatus.INVALID_LOGIN_REQUIRED
                and fail_reason == self._FETCH_FAIL_AUTH
                and not cookie_free
            ):
                # 旧逻辑把账号/Cookie 被挡后的登录页 DOM 当成内容失效；
                # 实际只能说明当前账号态不可用，批量检测时应换号重试或标记待复核。
                utils.logger.warning(
                    f"[UrlCheckCrawler] id={row_id} Cookie/API鉴权失败后 DOM={dom_status.value}，"
                    f"标记检测异常待复核"
                )
                await self._save_result(
                    row,
                    is_valid=STATUS_EXCEPTION,
                    metrics=empty_metrics(),
                    reason="账号/Cookie 被风控或失效，DOM 仅返回登录页，无法确认链接状态",
                )
                return
            if (
                platform == "toutiao"
                and fail_reason in (self._FETCH_FAIL_RISK, self._FETCH_FAIL_NETWORK)
                and dom_status == ValidityStatus.INVALID_LOGIN_REQUIRED
            ):
                # 旧逻辑会把头条空白页后的“无主体 DOM/login_required”写成无效；
                # 实际这是连续批量后的风控/加载失败信号，不能证明内容删除。
                utils.logger.warning(
                    f"[UrlCheckCrawler] id={row_id} 头条疑似风控后 DOM={dom_status.value}，"
                    f"标记检测异常待复核"
                )
                await self._save_result(
                    row,
                    is_valid=STATUS_EXCEPTION,
                    metrics=empty_metrics(),
                    reason=(
                        "头条页面为空或疑似风控，DOM 仅返回登录/无主体内容，"
                        "无法确认链接状态"
                    ),
                )
                return
            utils.logger.info(
                f"[UrlCheckCrawler] id={row_id} DOM检测失效 reason={dom_status.value}"
            )
            await self._save_result(
                row, is_valid=STATUS_INVALID, reason=f"DOM 检测失效: {dom_status.value}"
            )
        else:
            # API失败但DOM未检测到"内容不存在"标志，可能是验证码/风控拦截
            # 此时不能证明内容失效；旧逻辑写有效会掩盖风控问题，新逻辑写待复核。
            fail_reason = row.get("_fetch_fail_reason", "")
            if fail_reason == self._FETCH_FAIL_CONTENT:
                utils.logger.warning(
                    f"[UrlCheckCrawler] id={row_id} API判定内容不存在但DOM未确认，标记无效"
                )
                await self._save_result(row, is_valid=STATUS_INVALID, reason="内容不存在或已删除")
            else:
                utils.logger.warning(
                    f"[UrlCheckCrawler] id={row_id} API失败(风控/验证码)+DOM未检测到失效，"
                    f"标记检测异常待复核"
                )
                await self._save_result(
                    row,
                    is_valid=STATUS_EXCEPTION,
                    metrics=empty_metrics(),
                    reason=f"API/DOM 均无法确认，疑似风控或网络异常: {fail_reason or 'unknown'}",
                )

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

    @staticmethod
    def _classify_toutiao_page_state(
        page_text: str, final_url: str, content_id: Optional[str]
    ) -> tuple[str, str]:
        """
        头条页面状态分类。
        旧逻辑把空白页/首页壳页直接等同“内容不存在”，大批量风控时会把有效链接误杀。
        这里仅在命中明确删除/不存在语义时返回 dead，其余加载异常统一待复核。
        """
        text = page_text or ""
        stripped = text.strip().lower()

        if stripped == "404" or "404 Not Found" in text:
            return "dead", "页面返回 404"

        dead_keywords = (
            "内容已删除", "文章不存在", "该内容已下架", "页面不存在", "内容不存在",
            "抱歉，你访问的内容不存在", "内容正在审核中", "此内容因违规无法查看",
            "该文章已被删除",
        )
        for keyword in dead_keywords:
            if keyword in text:
                return "dead", f"页面明确提示: {keyword}"

        if stripped in ("", "error"):
            return "abnormal", "页面正文为空或返回 error，疑似风控/加载失败"

        risk_keywords = (
            "验证码", "安全验证", "访问频繁", "操作频繁", "请稍后再试",
            "verify", "captcha",
        )
        for keyword in risk_keywords:
            if keyword in text:
                return "abnormal", f"页面疑似风控验证: {keyword}"

        app_shell_indicators = (
            "打开App看完整内容", "打开 APP 看完整内容", "海量影视免费看",
            "打开抖音扫码下载", "打开App", "去首页看看",
        )
        for indicator in app_shell_indicators:
            if indicator in text:
                return "abnormal", f"页面仅返回 App/跳转壳: {indicator}"

        homepage_indicators = ("下载头条APP关于头条反馈侵权投诉", "关注\n推荐\n")
        if content_id and content_id not in (final_url or ""):
            for indicator in homepage_indicators:
                if indicator in text:
                    return "abnormal", "页面疑似跳转首页，无法确认内容是否失效"

        return "alive", ""

    async def _try_toutiao_mobile_fallback(
        self,
        client: Any,
        content_id: Optional[str],
        row: Optional[Dict],
        trigger_reason: str,
    ) -> tuple[str, Optional[Dict]]:
        """
        头条移动端状态兜底。

        旧逻辑只信桌面端 Playwright，遇到桌面端验证码/空白页会误判或留下异常；
        新逻辑在写无效/异常前用同代理访问移动端公开页，能确认 alive 时直接救回。
        """
        if (
            not content_id
            or not getattr(config, "URLCHECK_TOUTIAO_MOBILE_FALLBACK", True)
            or not hasattr(client, "get_mobile_article_state")
        ):
            return "unknown", None

        mobile_state = await client.get_mobile_article_state(content_id)
        state = mobile_state.get("state", "unknown")
        reason = mobile_state.get("reason") or "移动端未返回原因"
        title = mobile_state.get("title") or ""
        metrics = mobile_state.get("metrics") or {}

        if state == "alive":
            if row is not None:
                row.pop("_fetch_fail_reason", None)
                row.pop("_status_reason", None)
                if title:
                    row["_title"] = title
            utils.logger.info(
                f"[UrlCheckCrawler] toutiao 移动端兜底确认有效: "
                f"{content_id}, trigger={trigger_reason}, reason={reason}"
            )
            return "alive", {
                "group_id": content_id,
                "title": title,
                "raw_json": {
                    "group_id": content_id,
                    "title": title,
                    "mobile_confirmed": True,
                    "mobile_reason": reason,
                    "mobile_metrics": metrics,
                },
            }

        if state == "dead":
            if row is not None:
                row["_fetch_fail_reason"] = self._FETCH_FAIL_CONTENT
                row["_status_reason"] = reason
            utils.logger.info(
                f"[UrlCheckCrawler] toutiao 移动端兜底确认失效: "
                f"{content_id}, trigger={trigger_reason}, reason={reason}"
            )
            return "dead", None

        utils.logger.info(
            f"[UrlCheckCrawler] toutiao 移动端兜底无法确认: "
            f"{content_id}, trigger={trigger_reason}, reason={reason}"
        )
        return "unknown", None

    @staticmethod
    def _get_toutiao_mobile_metrics(result: Optional[Dict]) -> Dict:
        raw_json = (result or {}).get("raw_json") or {}
        metrics = raw_json.get("mobile_metrics") or {}
        return {
            "praise_count": metrics.get("praise_count"),
            "reply_count": metrics.get("reply_count"),
            "visit_count": metrics.get("visit_count"),
            "share_count": metrics.get("share_count"),
        }

    @staticmethod
    def _has_any_metric(metrics: Optional[Dict]) -> bool:
        """0 也是有效互动量；只有全 None 才认为没有指标证据。"""
        return any(value is not None for value in (metrics or {}).values())

    # 用于区分 _fetch_detail 返回 None 的原因
    _FETCH_FAIL_AUTH = "auth_failed"       # Cookie/IP 风控，需要计入 Cookie 失败
    _FETCH_FAIL_CONTENT = "content_gone"   # 内容不存在/已删除，Cookie 正常
    _FETCH_FAIL_NETWORK = "network_error"  # 网络/超时类错误
    _FETCH_FAIL_RISK = "risk_or_blocked"   # 空白页/壳页/验证页等疑似平台风控，待复核

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
            - "risk_or_blocked": 平台风控/空白页/壳页，不能直接判定链接无效
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
                    if "ixigua.com" in url and content_id:
                        # 旧逻辑会先打开 ixigua 原始页，批量无头下经常返回 App 壳页/超时。
                        # 已提取到内容 ID 时直接走头条规范页，再按需要回退 video，速度和稳定性更好。
                        nav_url = None
                    elif any(d in url for d in ("zjurl.cn", "weitoutiao")):
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
                            if hasattr(client, "playwright_page"):
                                try:
                                    article_text = await client.playwright_page.evaluate(
                                        "() => document.body?.innerText?.substring(0, 1000) || ''"
                                    )
                                    article_state, article_reason = self._classify_toutiao_page_state(
                                        article_text, client.playwright_page.url, content_id
                                    )
                                    if article_state == "alive":
                                        # 有些头条/西瓜页页面正文已加载，但 SSR JSON 不完整。
                                        # validity/DOM 指标可直接用当前页面，避免再回退 video 路径卡 10s 超时。
                                        utils.logger.info(
                                            f"[UrlCheckCrawler] toutiao 页面已存活，跳过 /video/ 回退: {content_id}"
                                        )
                                        result = {"group_id": content_id, "title": "", "raw_json": {"group_id": content_id}}
                                        need_fallback = False
                                    elif article_state == "dead":
                                        mobile_state, mobile_result = await self._try_toutiao_mobile_fallback(
                                            client, content_id, row, article_reason
                                        )
                                        if mobile_result is not None:
                                            result = mobile_result
                                            need_fallback = False
                                        elif mobile_state == "dead":
                                            utils.logger.info(
                                                f"[UrlCheckCrawler] toutiao /article/ 与移动端均确认失效: "
                                                f"{content_id}, reason={row.get('_status_reason') or article_reason}"
                                            )
                                            return None
                                        else:
                                            if row is not None:
                                                row["_fetch_fail_reason"] = self._FETCH_FAIL_CONTENT
                                            utils.logger.info(
                                                f"[UrlCheckCrawler] toutiao /article/ 已明确失效: "
                                                f"{content_id}, reason={article_reason}"
                                            )
                                            return None
                                except Exception as e:
                                    utils.logger.warning(
                                        f"[UrlCheckCrawler] toutiao /article/ 页面状态预判失败: {e}"
                                    )

                        if need_fallback:
                            utils.logger.info(
                                f"[UrlCheckCrawler] toutiao /article/ 路径无效，回退 /video/ 路径"
                            )
                            video_url = f"https://www.toutiao.com/video/{content_id}/"
                            result = await client.get_article_info(
                                content_id, original_url=video_url
                            )

                    # DOM 检测：明确删除才判无效；空白页、App壳页、首页跳转优先视为风控/加载异常。
                    if hasattr(client, "playwright_page"):
                        page_text = await client.playwright_page.evaluate(
                            "() => document.body?.innerText?.substring(0, 1000) || ''"
                        )
                        utils.logger.info(
                            f"[UrlCheckCrawler] toutiao page_text({len(page_text)}字): "
                            f"{page_text[:80]}"
                        )

                        page_state, page_reason = self._classify_toutiao_page_state(
                            page_text, client.playwright_page.url, content_id
                        )
                        if page_state != "alive":
                            mobile_state, mobile_result = await self._try_toutiao_mobile_fallback(
                                client, content_id, row, page_reason
                            )
                            if mobile_result is not None:
                                return mobile_result
                            if mobile_state == "dead":
                                if row is not None:
                                    row["_fetch_fail_reason"] = self._FETCH_FAIL_CONTENT
                                utils.logger.info(
                                    f"[UrlCheckCrawler] toutiao 移动端确认失效: "
                                    f"{content_id}, reason={row.get('_status_reason') or page_reason}"
                                )
                                return None

                            current_page_url = client.playwright_page.url or ""
                            normalized_detail_url = bool(
                                content_id and (
                                    f"/article/{content_id}" in current_page_url
                                    or f"/video/{content_id}" in current_page_url
                                )
                            )
                            if page_state == "dead" and normalized_detail_url:
                                # 已在规范详情页明确提示不存在，不再等待和重复 goto 二次确认。
                                if row is not None:
                                    row["_fetch_fail_reason"] = self._FETCH_FAIL_CONTENT
                                utils.logger.info(
                                    f"[UrlCheckCrawler] toutiao 规范详情页明确失效: "
                                    f"{content_id}, reason={page_reason}"
                                )
                                return None

                            if (
                                page_state == "abnormal"
                                and not getattr(config, "URLCHECK_TOUTIAO_CONFIRM_ABNORMAL", False)
                            ):
                                # 旧逻辑会在同一个疑似坏出口上等待并再次导航，100条批量时会成倍拖慢。
                                # 代理模式下更稳的做法是快速标记风险，让 worker 换 IP 后按行重试。
                                if row is not None:
                                    row["_fetch_fail_reason"] = self._FETCH_FAIL_RISK
                                    row["_status_reason"] = page_reason
                                utils.logger.warning(
                                    f"[UrlCheckCrawler] toutiao 首次异常即换IP重试: "
                                    f"{content_id}, reason={page_reason}"
                                )
                                return None

                            # 二次确认走规范详情页，不再用 /i{id}/ 原始短链，避免旧链触发反爬误判。
                            utils.logger.info(
                                f"[UrlCheckCrawler] toutiao 首次检测={page_state} "
                                f"reason={page_reason}，等待后二次确认: {content_id}"
                            )
                            # 旧逻辑这里直接复用 60s 风控冷却，App 壳页/空白页会把吞吐拖死。
                            # 二次确认只需要短暂等待页面稳定，连续异常的冷却在 worker 重建处单独处理。
                            await asyncio.sleep(getattr(
                                config,
                                "URLCHECK_TOUTIAO_CONFIRM_DELAY_SEC",
                                getattr(config, "URLCHECK_TOUTIAO_RISK_COOLDOWN_SEC", 5),
                            ))
                            try:
                                confirm_url = f"https://www.toutiao.com/article/{content_id}/"
                                await client.playwright_page.goto(
                                    confirm_url,
                                    wait_until="domcontentloaded",
                                    timeout=getattr(config, "URLCHECK_TOUTIAO_NAV_TIMEOUT_MS", 10000),
                                )
                                await asyncio.sleep(getattr(config, "URLCHECK_TOUTIAO_AFTER_NAV_SLEEP_SEC", 2))
                                page_text2 = await client.playwright_page.evaluate(
                                    "() => document.body?.innerText?.substring(0, 1000) || ''"
                                )
                                page_state2, page_reason2 = self._classify_toutiao_page_state(
                                    page_text2, client.playwright_page.url, content_id
                                )
                                if page_state2 == "alive":
                                    utils.logger.info(
                                        f"[UrlCheckCrawler] toutiao 二次确认: 内容存活 {content_id}"
                                    )
                                    return result
                                page_state, page_reason = page_state2, page_reason2
                            except Exception as e:
                                utils.logger.warning(
                                    f"[UrlCheckCrawler] toutiao 二次确认异常: {e}"
                                )
                                page_state = "abnormal"
                                page_reason = f"二次确认异常: {e}"

                            if row is not None:
                                if page_state == "dead":
                                    row["_fetch_fail_reason"] = self._FETCH_FAIL_CONTENT
                                else:
                                    row["_fetch_fail_reason"] = self._FETCH_FAIL_RISK
                                    row["_status_reason"] = page_reason
                            if page_state == "dead":
                                utils.logger.info(
                                    f"[UrlCheckCrawler] toutiao 二次确认失效: {content_id}, "
                                    f"reason={page_reason}"
                                )
                            else:
                                utils.logger.warning(
                                    f"[UrlCheckCrawler] toutiao 二次确认仍异常: {content_id}, "
                                    f"reason={page_reason}"
                                )
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
        if self._should_use_worker_proxy(platform):
            # 代理模式下并发受可用出口数约束；默认恢复较高吞吐，但每个 worker 独占 IP。
            configured = getattr(config, "URLCHECK_TOUTIAO_PROXY_CONCURRENCY", configured)

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

    def _proxy_fail_closed_platforms(self) -> set:
        return set(getattr(config, "URLCHECK_PROXY_FAIL_CLOSED_PLATFORMS", []))

    def _should_use_worker_proxy(self, platform: str) -> bool:
        """头条大批量代理必须绑定到 worker；避免平台预检直连、浏览器走代理的割裂。"""
        return bool(getattr(config, "ENABLE_IP_PROXY", False)) and platform in self._proxy_fail_closed_platforms()

    async def _get_or_create_ip_pool(self):
        from proxy.proxy_ip_pool import create_ip_pool

        if not hasattr(self, "_ip_pool_lock"):
            self._ip_pool_lock = asyncio.Lock()
        async with self._ip_pool_lock:
            if not hasattr(self, "_ip_pool") or self._ip_pool is None:
                # 多 worker 同时启动时只能创建一次代理池；豌豆 API 会拒绝并发提取请求。
                self._ip_pool = await create_ip_pool(
                    ip_pool_count=getattr(config, "IP_PROXY_POOL_COUNT", 5),
                    enable_validate_ip=True,
                )
        return self._ip_pool

    @staticmethod
    def _format_proxy_for_clients(ip_info) -> tuple[Dict, str]:
        protocol_raw = (getattr(ip_info, "protocol", "http") or "http").strip()
        # 豌豆返回/缓存的 protocol 可能已经带 ://；统一规范化，避免生成 http:://。
        protocol = protocol_raw if protocol_raw.endswith("://") else protocol_raw.rstrip(":/") + "://"
        server = f"{protocol}{ip_info.ip}:{ip_info.port}"
        pw_proxy: Dict = {"server": server}
        if getattr(ip_info, "user", "") and getattr(ip_info, "password", ""):
            pw_proxy["username"] = ip_info.user
            pw_proxy["password"] = ip_info.password
            httpx_proxy = f"{protocol}{ip_info.user}:{ip_info.password}@{ip_info.ip}:{ip_info.port}"
        else:
            httpx_proxy = server
        return pw_proxy, httpx_proxy

    @staticmethod
    def _proxy_ttl_seconds(ip_info) -> Optional[int]:
        expire_ts = getattr(ip_info, "expired_time_ts", None)
        if expire_ts is None:
            return None
        return int(expire_ts) - utils.get_unix_timestamp()

    async def _checkout_worker_proxy(self, platform: str, worker_id: int, tag: str):
        if not self._should_use_worker_proxy(platform):
            return None, None, None
        pool = await self._get_or_create_ip_pool()
        ip_info = await pool.checkout_proxy(
            min_ttl_sec=getattr(config, "URLCHECK_PROXY_MIN_TTL_SEC", 90),
            retry_count=getattr(config, "URLCHECK_PROXY_ACQUIRE_MAX_RETRIES", 3),
            retry_interval_sec=getattr(config, "URLCHECK_PROXY_ACQUIRE_RETRY_INTERVAL_SEC", 60),
        )
        pw_proxy, httpx_proxy = self._format_proxy_for_clients(ip_info)
        ttl = self._proxy_ttl_seconds(ip_info)
        utils.logger.info(
            f"{tag} 绑定代理 {ip_info.ip}:{ip_info.port}"
            f"{f' ttl={ttl}s' if ttl is not None else ''}"
        )
        return ip_info, pw_proxy, httpx_proxy

    async def _mark_queue_proxy_exception(
        self,
        result_crawler: "UrlCheckCrawler",
        queue: asyncio.Queue,
        worker_results: List[Dict],
        on_result=None,
        reason: str = "代理不可用，头条检测已熔断，避免回退直连污染服务器出口",
    ) -> None:
        """
        代理 fail-closed：头条批量启用代理后，如果拿不到代理，不能回退服务器直连。
        这里把剩余队列显式标记为检测异常，让任务结果数量完整且原因可审计。
        """
        while not queue.empty():
            try:
                row = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            row["_fetch_fail_reason"] = self._FETCH_FAIL_NETWORK
            await result_crawler._save_result(
                row,
                is_valid=STATUS_EXCEPTION,
                metrics=empty_metrics(),
                reason=reason,
            )
            worker_results.append(row)
            if on_result:
                on_result(row)

    def _release_worker_proxy(self, ip_info) -> None:
        if self._ip_pool and ip_info:
            self._ip_pool.release_proxy(ip_info)

    def _drop_worker_proxy(self, ip_info, reason: str) -> None:
        if self._ip_pool and ip_info:
            self._ip_pool.drop_proxy(ip_info, reason)

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
        proxy_ip_info = None
        worker_pw_proxy = None
        worker_httpx_proxy = None

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
                if self._should_use_worker_proxy(platform):
                    try:
                        proxy_ip_info, worker_pw_proxy, worker_httpx_proxy = await self._checkout_worker_proxy(
                            platform, worker_id, tag
                        )
                    except Exception as e:
                        utils.logger.error(f"{tag} 获取代理失败，头条检测熔断: {e}")
                        await self._mark_queue_proxy_exception(
                            crawler_instance, queue, worker_results, on_result
                        )
                        return worker_results, exhausted_cookie_id

                # 创建浏览器和Client
                client = await self._create_worker_client(
                    crawler_instance, platform, playwright,
                    current_cookie_str, cookie_free, worker_id,
                    playwright_proxy=worker_pw_proxy,
                    httpx_proxy=worker_httpx_proxy,
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
                risk_streak = 0

                limit_policy = getattr(config, "COOKIE_LIMIT_POLICY", "cooldown")
                cooldown_sec = getattr(config, "COOKIE_COOLDOWN_SEC", 300)

                while not queue.empty():
                    if proxy_ip_info and proxy_ip_info.is_expired(
                        getattr(config, "URLCHECK_PROXY_MIN_TTL_SEC", 90)
                    ):
                        ttl = self._proxy_ttl_seconds(proxy_ip_info)
                        utils.logger.info(
                            f"{tag} 代理 {proxy_ip_info.ip}:{proxy_ip_info.port} "
                            f"剩余TTL={ttl}s，停止取新URL并换IP"
                        )
                        await crawler_instance._cleanup_browser()
                        self._drop_worker_proxy(proxy_ip_info, "TTL不足")
                        try:
                            proxy_ip_info, worker_pw_proxy, worker_httpx_proxy = await self._checkout_worker_proxy(
                                platform, worker_id, tag
                            )
                        except Exception as e:
                            utils.logger.error(f"{tag} TTL换IP失败，头条检测熔断: {e}")
                            await self._mark_queue_proxy_exception(
                                crawler_instance, queue, worker_results, on_result
                            )
                            break
                        client = await self._create_worker_client(
                            crawler_instance, platform, playwright,
                            current_cookie_str, cookie_free, worker_id,
                            playwright_proxy=worker_pw_proxy,
                            httpx_proxy=worker_httpx_proxy,
                        )
                        if client is None:
                            utils.logger.error(f"{tag} 换IP后重建Client失败，Worker停止")
                            break

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
                            playwright_proxy=worker_pw_proxy,
                            httpx_proxy=worker_httpx_proxy,
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

                    if row.get("_proxy_precheck_in_worker"):
                        status, _final_url = await http_pre_check(
                            url, platform, proxy=worker_httpx_proxy
                        )
                        if is_invalid(status):
                            await crawler_instance._save_result(
                                row,
                                is_valid=STATUS_INVALID,
                                reason=f"HTTP 预检失效: {status.value}",
                            )
                            worker_results.append(row)
                            processed_count += 1
                            cookie_batch_count += 1
                            if on_result:
                                on_result(row)
                            actual_sleep = base_sleep * (
                                1 + random.uniform(-jitter_ratio, jitter_ratio)
                            )
                            await asyncio.sleep(actual_sleep)
                            continue

                    before_count = len(crawler_instance._all_results)
                    await crawler_instance._process_single_url(platform, client, row, mode)
                    new_results = crawler_instance._all_results[before_count:]
                    fail_reason = row.get("_fetch_fail_reason", "")

                    retry_status = new_results[-1].get("_is_valid") if new_results else None
                    cookie_retry_count = row.get("_cookie_retry_count", 0)
                    should_cookie_retry = (
                        not cookie_free
                        and retry_status == STATUS_EXCEPTION
                        and fail_reason == UrlCheckCrawler._FETCH_FAIL_AUTH
                        and cookie_retry_count < getattr(config, "URLCHECK_COOKIE_ROW_RETRY", 1)
                    )
                    retry_count = row.get("_proxy_retry_count", 0)
                    should_proxy_retry = (
                        self._should_use_worker_proxy(platform)
                        and retry_status == STATUS_EXCEPTION
                        and fail_reason in (
                            UrlCheckCrawler._FETCH_FAIL_RISK,
                            UrlCheckCrawler._FETCH_FAIL_NETWORK,
                        )
                        and retry_count < getattr(config, "URLCHECK_PROXY_ROW_RETRY", 1)
                    )
                    if should_cookie_retry:
                        new_client = await self._try_rebind_cookie(
                            crawler_instance, platform, playwright,
                            current_cookie_id, used_cookie_ids, tag,
                            purpose=cookie_purpose,
                            playwright_proxy=worker_pw_proxy,
                            httpx_proxy=worker_httpx_proxy,
                        )
                        if new_client:
                            # 旧逻辑换 Cookie 发生在保存结果之后，同一条 URL 已经被误写成无效/异常；
                            # 这里撤销本次结果并重排当前行，让新 Cookie 立即复核同一条。
                            del crawler_instance._all_results[before_count:]
                            row["_cookie_retry_count"] = cookie_retry_count + 1
                            for k in ("_is_valid", "_validity_label", "_status_reason", "_metrics", "_fetch_fail_reason"):
                                row.pop(k, None)
                            await queue.put(row)
                            client = new_client
                            current_cookie_id = getattr(
                                crawler_instance, "_rebound_cookie_id", current_cookie_id
                            )
                            cookie_batch_count = 0
                            utils.logger.warning(
                                f"{tag} id={row['id']} 当前Cookie仅返回登录页，换Cookie后重试"
                            )
                            actual_sleep = base_sleep * (
                                1 + random.uniform(-jitter_ratio, jitter_ratio)
                            )
                            await asyncio.sleep(actual_sleep)
                            continue
                    if should_proxy_retry:
                        # 当前代理已明显被挡，撤销本次异常结果并把同一 URL 放回队列，用新 IP 重试。
                        del crawler_instance._all_results[before_count:]
                        row["_proxy_retry_count"] = retry_count + 1
                        for k in ("_is_valid", "_validity_label", "_status_reason", "_metrics", "_fetch_fail_reason"):
                            row.pop(k, None)
                        await queue.put(row)
                        risk_streak = getattr(config, "URLCHECK_PROXY_BAD_STREAK_THRESHOLD", 3)
                        utils.logger.warning(
                            f"{tag} id={row['id']} 疑似代理出口被挡，换IP后重试"
                        )
                    else:
                        processed_count += 1
                        cookie_batch_count += 1
                        worker_results.extend(new_results)

                        if on_result:
                            for r in new_results:
                                on_result(r)

                    # Cookie/IP 失效检测
                    if platform == "toutiao" and fail_reason in (
                        UrlCheckCrawler._FETCH_FAIL_RISK,
                        UrlCheckCrawler._FETCH_FAIL_NETWORK,
                    ):
                        risk_streak += 1
                    else:
                        risk_streak = 0

                    rebuild_after = getattr(config, "URLCHECK_TOUTIAO_REBUILD_AFTER_RISK", 3)
                    if platform == "toutiao" and rebuild_after > 0 and risk_streak >= rebuild_after:
                        cooldown = getattr(
                            config,
                            "URLCHECK_TOUTIAO_REBUILD_COOLDOWN_SEC",
                            getattr(config, "URLCHECK_TOUTIAO_RISK_COOLDOWN_SEC", 5),
                        )
                        utils.logger.warning(
                            f"{tag} 连续 {risk_streak} 次疑似风控/加载异常，"
                            f"冷却 {cooldown}s 后重建浏览器上下文"
                        )
                        await asyncio.sleep(cooldown)
                        await crawler_instance._cleanup_browser()
                        if self._should_use_worker_proxy(platform):
                            self._drop_worker_proxy(proxy_ip_info, "连续空白页/加载异常")
                            try:
                                proxy_ip_info, worker_pw_proxy, worker_httpx_proxy = await self._checkout_worker_proxy(
                                    platform, worker_id, tag
                                )
                            except Exception as e:
                                utils.logger.error(f"{tag} 连续异常后换IP失败，头条检测熔断: {e}")
                                await self._mark_queue_proxy_exception(
                                    crawler_instance, queue, worker_results, on_result
                                )
                                break
                        client = await self._create_worker_client(
                            crawler_instance, platform, playwright,
                            current_cookie_str, cookie_free, worker_id,
                            playwright_proxy=worker_pw_proxy,
                            httpx_proxy=worker_httpx_proxy,
                        )
                        risk_streak = 0
                        if client is None:
                            utils.logger.error(f"{tag} 重建Client失败，Worker停止")
                            break

                    if fail_reason == UrlCheckCrawler._FETCH_FAIL_AUTH and not cookie_free:
                        new_client = await self._try_rebind_cookie(
                            crawler_instance, platform, playwright,
                            current_cookie_id, used_cookie_ids, tag,
                            purpose=cookie_purpose,
                            playwright_proxy=worker_pw_proxy,
                            httpx_proxy=worker_httpx_proxy,
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
                self._release_worker_proxy(proxy_ip_info)

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
        playwright_proxy: Optional[Dict] = None,
        httpx_proxy: Optional[str] = None,
    ):
        """为 Worker 创建浏览器 Client（cookie_free 平台开空白浏览器）"""
        from importlib import import_module

        pw_proxy = playwright_proxy
        if pw_proxy is None and not self._should_use_worker_proxy(platform):
            pw_proxy = await self._get_playwright_proxy(platform)

        # 每个 Worker 使用独立 user_data_dir 避免并发冲突
        chromium = playwright.chromium
        user_data_dir = os.path.join(
            os.getcwd(), "browser_data", f"worker_{platform}_{worker_id}"
        )
        crawler_instance._worker_user_data_dir = user_data_dir

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
            try:
                # 旧逻辑要求首页完整加载成功才创建 Client；代理出口偶发 ERR_EMPTY_RESPONSE 会让整个 worker 退出。
                # url_check 真正需要的是详情页导航，首页只用于初始化页面环境，因此失败时记录后继续。
                await crawler_instance.context_page.goto(
                    home_url, wait_until="domcontentloaded", timeout=15000
                )
            except Exception as e:
                utils.logger.warning(
                    f"[UrlCheckCrawler] Worker 首页预热失败，继续详情检测: {home_url}, err={e}"
                )
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
                proxy=httpx_proxy,
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

            try:
                # 首页预热失败通常是代理出口抖动，不应直接退出 worker；后续详情页仍可自行导航确认。
                await crawler_instance.context_page.goto(
                    home_url, wait_until="domcontentloaded", timeout=15000
                )
            except Exception as e:
                utils.logger.warning(
                    f"[UrlCheckCrawler] Worker 首页预热失败，继续详情检测: {home_url}, err={e}"
                )
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
                proxy=httpx_proxy,
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
        playwright_proxy: Optional[Dict] = None,
        httpx_proxy: Optional[str] = None,
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
            crawler_instance,
            platform,
            playwright,
            new_str,
            False,
            playwright_proxy=playwright_proxy,
            httpx_proxy=httpx_proxy,
        )
        # 记录新的cookie_id供外部使用
        crawler_instance._rebound_cookie_id = new_id
        return client

    # ────────────────── 浏览器管理 ──────────────────

    async def _get_playwright_proxy(self, platform: str = "") -> Optional[Dict]:
        """当 ENABLE_IP_PROXY=True 时，从代理池获取一个代理并转成 Playwright 格式"""
        if not config.ENABLE_IP_PROXY:
            return None
        generic_proxy_platforms = set(getattr(config, "URLCHECK_GENERIC_PROXY_PLATFORMS", []))
        if platform and platform not in generic_proxy_platforms:
            # 旧逻辑在全局开启代理后会让抖音等账号态平台也共用豌豆短效 IP；
            # 这类平台更依赖 Cookie 与出口稳定性，默认不套通用代理，避免批量时误触账号风控。
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
        pw_proxy = await self._get_playwright_proxy(platform)

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
            if self.context_page and not self.context_page.is_closed():
                try:
                    await self.context_page.close()
                except Exception:
                    pass
            self.context_page = None

            if self.cdp_manager:
                await self.cdp_manager.cleanup()
                self.cdp_manager = None
            elif self.browser_context:
                await self.browser_context.close()
                self.browser_context = None
        except Exception as e:
            utils.logger.warning(f"[UrlCheckCrawler] 清理浏览器时异常: {e}")
        finally:
            self._cleanup_worker_profile()

    def _cleanup_worker_profile(self):
        """只清理 url_check worker_* 临时 profile，避免删除用户登录态目录。"""
        if not getattr(config, "URLCHECK_CLEAN_WORKER_PROFILE", True):
            return
        user_data_dir = getattr(self, "_worker_user_data_dir", "")
        if not user_data_dir:
            return

        try:
            target = os.path.abspath(user_data_dir)
            browser_root = os.path.abspath(os.path.join(os.getcwd(), "browser_data"))
            basename = os.path.basename(target)
            # 清理前做双重边界校验：必须在当前工作区 browser_data 内，且目录名以 worker_ 开头。
            if os.path.commonpath([browser_root, target]) != browser_root:
                utils.logger.warning(f"[UrlCheckCrawler] 跳过异常 profile 路径: {target}")
                return
            if not basename.startswith("worker_"):
                return
            if os.path.isdir(target):
                shutil.rmtree(target, ignore_errors=True)
                utils.logger.info(f"[UrlCheckCrawler] 已清理临时浏览器 profile: {target}")
        except Exception as e:
            utils.logger.warning(f"[UrlCheckCrawler] 清理临时浏览器 profile 失败: {e}")
        finally:
            self._worker_user_data_dir = ""

    # ── AbstractCrawler 接口实现（url_check 模式不使用，但需实现接口）──

    async def search(self):
        pass

    async def launch_browser(self, chromium, playwright_proxy, user_agent, headless=True):
        pass
