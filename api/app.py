# -*- coding: utf-8 -*-
# FastAPI 应用主入口
# 启动命令: uvicorn api.app:app --host 0.0.0.0 --port 8888

import asyncio
import os
import pathlib
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import config
from api.routes import router
from api.routes_report import report_router
from api.task_manager import task_manager
from tools import utils

load_dotenv()

pathlib.Path("data/url_check/excel").mkdir(parents=True, exist_ok=True)
pathlib.Path("data/url_check").mkdir(parents=True, exist_ok=True)
pathlib.Path("data/report_screenshots").mkdir(parents=True, exist_ok=True)

# 后台定时刷新任务引用（用于关闭时取消）
_refresh_task: asyncio.Task = None


async def _cookie_refresh_loop():
    """
    后台定时 Cookie 刷新循环。
    按 COOKIE_REFRESH_INTERVAL 配置的各平台周期，逐平台逐条刷新Cookie。
    每60秒检查一次是否有需要刷新的Cookie（基于 last_refreshed_at 时间戳）。
    """
    check_interval = 60  # 每60秒检查一次
    # 启动后延迟5分钟再开始首次检查，避免启动时与其他初始化冲突
    await asyncio.sleep(300)
    utils.logger.info("[CookieRefreshLoop] 定时Cookie刷新循环已启动")

    while True:
        try:
            from proxy.cookie_pool import cookie_pool
            from api.routes import _pong_validate_cookie, _refresh_single_cookie

            refresh_intervals = getattr(config, "COOKIE_REFRESH_INTERVAL", {})

            for platform, entries in cookie_pool._pool.items():
                interval = refresh_intervals.get(platform, 0)
                if interval <= 0:
                    continue

                for entry in entries:
                    if not entry.valid:
                        continue
                    # 判断是否需要刷新：last_refreshed_at 为空或距今超过刷新周期
                    needs_refresh = False
                    if not entry.last_refreshed_at:
                        needs_refresh = True
                    else:
                        try:
                            from datetime import datetime
                            last_ts = datetime.strptime(entry.last_refreshed_at, "%Y-%m-%d %H:%M:%S")
                            elapsed = (datetime.now() - last_ts).total_seconds()
                            if elapsed >= interval:
                                needs_refresh = True
                        except Exception:
                            needs_refresh = True

                    if needs_refresh:
                        utils.logger.info(
                            f"[CookieRefreshLoop] 刷新 {platform}/{entry.id} "
                            f"(上次刷新: {entry.last_refreshed_at or '从未'})"
                        )
                        try:
                            new_cookie = await _refresh_single_cookie(platform, entry.cookie)
                            if not new_cookie:
                                utils.logger.info(f"[CookieRefreshLoop] {platform}/{entry.id} 未获取到新Cookie")
                            else:
                                validate_result = await _pong_validate_cookie(platform, new_cookie)
                                if validate_result.get("valid") is True:
                                    if new_cookie != entry.cookie:
                                        cookie_pool.update_cookie_str(platform, entry.id, new_cookie)
                                    cookie_pool.mark_refreshed(platform, entry.id)
                                    utils.logger.info(f"[CookieRefreshLoop] {platform}/{entry.id} 刷新后验证通过")
                                elif validate_result.get("valid") is False and validate_result.get("mark_invalid"):
                                    cookie_pool.mark_invalid(platform, entry.id)
                                    utils.logger.warning(
                                        f"[CookieRefreshLoop] {platform}/{entry.id} 刷新后验证失败，已标记失效: "
                                        f"{validate_result.get('reason')}"
                                    )
                                else:
                                    if new_cookie != entry.cookie:
                                        cookie_pool.update_cookie_str(platform, entry.id, new_cookie)
                                    utils.logger.warning(
                                        f"[CookieRefreshLoop] {platform}/{entry.id} 刷新后验证不确定，未恢复有效状态: "
                                        f"{validate_result.get('reason')}"
                                    )
                        except Exception as e:
                            utils.logger.warning(f"[CookieRefreshLoop] {platform}/{entry.id} 刷新失败: {e}")
                        # 每条Cookie刷新后暂停5秒，避免同时开太多浏览器
                        await asyncio.sleep(5)

        except asyncio.CancelledError:
            utils.logger.info("[CookieRefreshLoop] 定时刷新任务被取消")
            return
        except Exception as e:
            utils.logger.error(f"[CookieRefreshLoop] 刷新循环异常: {e}")

        await asyncio.sleep(check_interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：启动时初始化资源，关闭时清理"""
    global _refresh_task
    utils.logger.info("[FastAPI] 服务启动中...")
    await task_manager.start()

    from database.external_db import external_db
    try:
        await external_db.ensure_pool()
        utils.logger.info("[FastAPI] 外部数据库连接池已就绪")
    except Exception as e:
        utils.logger.warning(f"[FastAPI] 外部数据库连接池初始化失败（非致命）: {e}")

    if getattr(config, "ENABLE_COOKIE_POOL", False):
        from proxy.cookie_pool import cookie_pool
        await cookie_pool.load()
        stats = cookie_pool.get_stats()
        utils.logger.info(f"[FastAPI] Cookie池已加载: {stats}")

        # 启动定时Cookie刷新后台任务
        _refresh_task = asyncio.create_task(_cookie_refresh_loop())
        utils.logger.info("[FastAPI] Cookie定时刷新任务已启动")

    utils.logger.info("[FastAPI] 服务已就绪")
    yield
    utils.logger.info("[FastAPI] 服务关闭中...")
    if _refresh_task and not _refresh_task.done():
        _refresh_task.cancel()
    await task_manager.stop()
    await external_db.close()
    utils.logger.info("[FastAPI] 服务已关闭")


app = FastAPI(
    title="MediaCrawler URL检测服务",
    description="提供URL有效性检测、转赞评指标抓取、评论采集等功能的REST API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(report_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api.app:app",
        host="0.0.0.0",
        port=int(os.getenv("API_PORT", "8000")),
        reload=False,
        log_level="info",
    )
