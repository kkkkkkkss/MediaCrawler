# -*- coding: utf-8 -*-
# FastAPI 应用主入口
# 启动命令: uvicorn api.app:app --host 0.0.0.0 --port 8888

import os
import pathlib
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import config
from api.routes import router
from api.task_manager import task_manager
from tools import utils

load_dotenv()

# 确保必要目录存在
pathlib.Path("data/url_check/excel").mkdir(parents=True, exist_ok=True)
pathlib.Path("data/url_check").mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：启动时初始化资源，关闭时清理"""
    utils.logger.info("[FastAPI] 服务启动中...")
    await task_manager.start()

    # 初始化外部数据库连接池（MySQL 回写依赖）
    from database.external_db import external_db
    try:
        await external_db.ensure_pool()
        utils.logger.info("[FastAPI] 外部数据库连接池已就绪")
    except Exception as e:
        utils.logger.warning(f"[FastAPI] 外部数据库连接池初始化失败（非致命）: {e}")

    # 启动时加载 Cookie 池
    if getattr(config, "ENABLE_COOKIE_POOL", False):
        from proxy.cookie_pool import cookie_pool
        await cookie_pool.load()
        stats = cookie_pool.get_stats()
        utils.logger.info(f"[FastAPI] Cookie池已加载: {stats}")

    utils.logger.info("[FastAPI] 服务已就绪")
    yield
    utils.logger.info("[FastAPI] 服务关闭中...")
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api.app:app",
        host="0.0.0.0",
        port=int(os.getenv("API_PORT", "8000")),
        reload=False,
        log_level="info",
    )
