# -*- coding: utf-8 -*-
# FastAPI 路由定义
# 所有 /api/v1 下的端点

import os
import tempfile
from typing import List

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

import config
from api.schemas import (
    BatchUrlRequest,
    CallbackConfigRequest,
    CallbackConfigResponse,
    CheckMode,
    CookieActionResponse,
    CookieAddRequest,
    CookiePoolResponse,
    CookieRemoveRequest,
    HealthResponse,
    MysqlSourceRequest,
    SingleUrlRequest,
    SingleUrlResponse,
    TaskCommentsResponse,
    TaskJsonResultResponse,
    TaskProgressResponse,
    TaskResponse,
    TaskStatus,
    UrlCheckResult,
)
from api.task_manager import TaskInfo, task_manager
from tools import utils
from tools.url_detector import detect_platform

router = APIRouter(prefix="/api/v1")

# ── 平台中文名映射（复用 excel_store 的定义） ──
_PLATFORM_NAMES = {
    "dy": "抖音", "ks": "快手", "bili": "B站",
    "toutiao": "今日头条", "xhs": "小红书", "wb": "微博",
}


@router.get("/health", response_model=HealthResponse, summary="健康检查", tags=["系统"])
async def health_check():
    """检查API服务是否正常运行。返回 status=ok 表示服务正常。"""
    return HealthResponse()


@router.post("/check/url", response_model=TaskResponse, summary="单链接检测（异步+实时日志）", tags=["链接检测"])
async def check_single_url(req: SingleUrlRequest):
    """
    检测单个URL的有效性和指标数据，**异步**执行，实时日志。

    适用场景：检测1条链接，通过 task_id 轮询实时日志和结果。

    使用流程：
    1. 调用此接口 → 获取 task_id
    2. 用 GET /task/{task_id} 轮询进度和日志
    3. 任务完成后用 GET /check/url/result/{task_id} 获取检测结果

    支持平台：抖音、快手、B站、微博、今日头条。
    """
    from api.service import run_single_url_check

    async def task_coro(info: TaskInfo):
        await run_single_url_check(info, req.url, req.mode.value, req.enable_comments)

    task_id = task_manager.submit(task_coro, total=1, prefix="single")
    info = task_manager.get_task(task_id)
    if info and req.callback_url:
        info.callback_url = req.callback_url
    return TaskResponse(task_id=task_id, status=TaskStatus.PENDING, message="单条检测任务已提交")


@router.get("/check/url/result/{task_id}", response_model=SingleUrlResponse, summary="获取单链接检测结果", tags=["链接检测"])
async def get_single_url_result(task_id: str):
    """
    获取单条检测任务的最终结果。

    任务完成后（status=completed），返回检测结果和完整日志。
    """
    info = task_manager.get_task(task_id)
    if not info:
        raise HTTPException(status_code=404, detail="任务不存在")
    if info.status not in ("completed", "failed"):
        return SingleUrlResponse(
            result=UrlCheckResult(id=1, url=""),
            logs=info.logs,
            message=f"任务尚未完成，当前状态: {info.status}",
        )
    if info.single_result:
        result = UrlCheckResult(**info.single_result)
    else:
        result = UrlCheckResult(id=1, url="", is_valid=0)
    return SingleUrlResponse(result=result, logs=info.logs)


@router.post("/check/batch", response_model=TaskResponse, summary="批量URL检测（异步）", tags=["链接检测"])
async def check_batch_urls(req: BatchUrlRequest):
    """
    批量检测多个URL，**异步**执行，立即返回 task_id。

    适用场景：一次性检测多条链接（2-500条），通过 task_id 轮询进度。

    **如何自定义检测的网址**：直接修改请求体中的 urls 数组，添加/删除/替换你想检测的链接即可。
    无需修改后端代码，Postman 里改 Body 就行。

    使用流程：
    1. 调用此接口提交任务 → 获取 task_id
    2. 用 GET /task/{task_id} 轮询进度
    3. 任务完成后用 GET /task/{task_id}/result 下载Excel结果
    """
    from api.service import run_batch_check

    async def task_coro(info: TaskInfo):
        await run_batch_check(info, req.urls, req.mode.value, req.enable_comments)

    task_id = task_manager.submit(task_coro, total=len(req.urls), prefix="batch")
    info = task_manager.get_task(task_id)
    if info and req.callback_url:
        info.callback_url = req.callback_url
    return TaskResponse(task_id=task_id, status=TaskStatus.PENDING, message="任务已提交")


@router.post("/check/upload", response_model=TaskResponse, summary="上传文件检测（异步）", tags=["链接检测"])
async def check_upload_excel(
    file: UploadFile = File(..., description="上传的文件（支持 .xlsx/.csv/.txt）"),
    url_column: str = Form(default="url", description="文件中存放URL的列名（Excel/CSV有列名时使用）"),
    mode: str = Form(default="both", description="检测模式: validity/metrics/both"),
    enable_comments: bool = Form(default=False, description="是否抓取评论"),
    callback_url: str = Form(default="", description="任务完成后的回调地址（可选）"),
):
    """
    上传 Excel/CSV/TXT 文件进行批量检测，**异步**执行。

    适用场景：URL存在本地文件中，不想手动逐条复制。

    支持格式：
    - .xlsx/.csv：自动读取指定列（url_column参数）的URL
    - .txt：每行一个URL

    在 Postman 中使用：Body → form-data，file 字段选择文件，其他字段填参数值。
    """
    from api.service import run_file_upload_check

    suffix = os.path.splitext(file.filename or "upload")[1] or ".xlsx"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir="data/url_check")
    content = await file.read()
    tmp.write(content)
    tmp.close()

    async def task_coro(info: TaskInfo):
        await run_file_upload_check(info, tmp.name, url_column, mode, enable_comments)

    fname = os.path.splitext(file.filename or "file")[0][:10]
    task_id = task_manager.submit(task_coro, prefix=f"file-{fname}")
    info = task_manager.get_task(task_id)
    if info and callback_url:
        info.callback_url = callback_url
    return TaskResponse(task_id=task_id, status=TaskStatus.PENDING, message="文件已上传，任务已提交")


@router.post("/check/mysql", response_model=TaskResponse, summary="MySQL数据源检测（异步）", tags=["链接检测"])
async def check_mysql_source(req: MysqlSourceRequest):
    """
    从指定的 MySQL 数据库表中读取URL进行批量检测，**异步**执行。

    适用场景：URL存储在数据库中，希望直接从DB读取并检测。

    **如何更换数据库**：直接修改请求体中的 host/port/user/password/database/table 字段即可，
    无需修改后端代码。你在 Postman 里改 Body 的连接信息就能连接不同的数据库。

    检测结果会回写到同一张表（更新 is_valid、praise_count 等字段）。
    """
    from api.service import run_mysql_check

    async def task_coro(info: TaskInfo):
        await run_mysql_check(info, req)

    task_id = task_manager.submit(task_coro, prefix="mysql")
    info = task_manager.get_task(task_id)
    if info and req.callback_url:
        info.callback_url = req.callback_url
    return TaskResponse(task_id=task_id, status=TaskStatus.PENDING, message="任务已提交")


@router.get("/task/{task_id}", response_model=TaskProgressResponse, summary="查询任务进度", tags=["任务管理"])
async def get_task_status(task_id: str, log_offset: int = 0):
    """
    查询异步任务的执行进度。

    参数：
    - log_offset: 日志偏移量，只返回该索引之后的新日志（用于增量拉取）

    返回当前处理进度（百分比、已处理数/总数）和实时日志。
    任务完成后（status=completed），可通过 /task/{id}/result 下载结果。
    """
    info = task_manager.get_task(task_id)
    if not info:
        raise HTTPException(status_code=404, detail="任务不存在")
    logs = info.logs[log_offset:] if log_offset < len(info.logs) else []
    return TaskProgressResponse(
        task_id=info.task_id,
        status=TaskStatus(info.status),
        progress=info.progress,
        total=info.total,
        processed=info.processed,
        message=info.message,
        result_file=info.result_file,
        logs=logs,
        log_total=len(info.logs),
    )


@router.post("/task/{task_id}/cancel", summary="终止任务", tags=["任务管理"])
async def cancel_task(task_id: str):
    """终止正在执行的任务。"""
    from fastapi.responses import JSONResponse
    info = task_manager.get_task(task_id)
    if not info:
        raise HTTPException(status_code=404, detail="任务不存在")
    if info.status in ("completed", "failed", "cancelled"):
        return JSONResponse({"success": False, "message": f"任务已处于终态: {info.status}"})
    info.cancel()
    return JSONResponse({"success": True, "message": "任务已标记为取消"})


@router.get("/task/{task_id}/result", summary="下载任务结果文件", tags=["结果获取"])
async def download_task_result(task_id: str, format: str = "excel"):
    """
    下载任务结果文件。

    参数：
    - format: 文件格式，excel（默认）或 json

    注意：仅当任务 status=completed 时可下载。
    """
    info = task_manager.get_task(task_id)
    if not info:
        raise HTTPException(status_code=404, detail="任务不存在")
    if info.status != "completed":
        raise HTTPException(status_code=400, detail=f"任务状态: {info.status}，尚未完成")

    if format == "json":
        if info.result_data:
            from store.url_check_json_store import generate_json_file
            import pathlib
            output_dir = pathlib.Path("data/url_check/json")
            output_dir.mkdir(parents=True, exist_ok=True)
            json_path = str(output_dir / f"{info.task_id}.json")
            generate_json_file(info.result_data, info.task_id, json_path)
            return FileResponse(
                json_path,
                media_type="application/json",
                filename=f"{info.task_id}.json",
            )
        raise HTTPException(status_code=404, detail="JSON 结果数据不存在")

    if not info.result_file or not os.path.exists(info.result_file):
        raise HTTPException(status_code=404, detail="结果文件不存在")
    return FileResponse(
        info.result_file,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=os.path.basename(info.result_file),
    )


@router.get("/task/{task_id}/result/json", response_model=TaskJsonResultResponse, summary="获取任务 JSON 结果", tags=["结果获取"])
async def get_task_json_result(task_id: str):
    """
    直接以 JSON 响应体返回任务结果（不需要下载文件）。

    适用于程序化调用、回调接收方解析结果。
    """
    info = task_manager.get_task(task_id)
    if not info:
        raise HTTPException(status_code=404, detail="任务不存在")
    if info.status != "completed":
        raise HTTPException(status_code=400, detail=f"任务状态: {info.status}，尚未完成")
    if not info.result_data:
        return TaskJsonResultResponse(task_id=task_id, total=0, results=[])

    from store.url_check_json_store import results_to_json_data
    data = results_to_json_data(info.result_data, info.task_id)
    return TaskJsonResultResponse(**data)


@router.get("/task/{task_id}/comments", response_model=TaskCommentsResponse, summary="获取评论 JSON 数据", tags=["结果获取"])
async def get_task_comments(task_id: str):
    """
    获取任务的评论数据（JSON 格式）。

    仅当提交任务时开启了评论抓取(enable_comments=true)才有数据。
    评论按作品 URL 分组返回。
    """
    info = task_manager.get_task(task_id)
    if not info:
        raise HTTPException(status_code=404, detail="任务不存在")
    if info.status != "completed":
        raise HTTPException(status_code=400, detail=f"任务状态: {info.status}，尚未完成")

    comments_data = info.comments_data or []
    total = sum(len(item.get("comments", [])) for item in comments_data)
    return TaskCommentsResponse(
        task_id=task_id,
        total_comments=total,
        results=comments_data,
    )


@router.get("/task/{task_id}/comments/download", summary="下载评论文件", tags=["结果获取"])
async def download_task_comments(task_id: str, format: str = "json"):
    """
    下载评论数据文件。

    参数：
    - format: json（默认）或 excel
    """
    info = task_manager.get_task(task_id)
    if not info:
        raise HTTPException(status_code=404, detail="任务不存在")
    if info.status != "completed":
        raise HTTPException(status_code=400, detail=f"任务状态: {info.status}，尚未完成")
    if not info.comments_data:
        raise HTTPException(status_code=404, detail="无评论数据（未开启评论抓取或无评论）")

    from store.url_check_comment_export import export_comments
    file_path = export_comments(info.comments_data, info.task_id, format)
    if format == "excel":
        return FileResponse(
            file_path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=os.path.basename(file_path),
        )
    return FileResponse(
        file_path,
        media_type="application/json",
        filename=os.path.basename(file_path),
    )


@router.post("/task/{task_id}/delete", summary="删除任务记录", tags=["任务管理"])
async def delete_task(task_id: str):
    """删除已完成/失败/取消的任务记录。运行中的任务需先终止。"""
    from fastapi.responses import JSONResponse
    info = task_manager.get_task(task_id)
    if not info:
        raise HTTPException(status_code=404, detail="任务不存在")
    if info.status in ("running", "pending"):
        raise HTTPException(status_code=400, detail="任务仍在执行中，请先终止")
    task_manager.remove_task(task_id)
    return JSONResponse({"success": True, "message": "任务已删除"})


@router.post("/tasks/delete/batch", summary="批量删除任务记录", tags=["任务管理"])
async def batch_delete_tasks(req: dict):
    """批量删除任务记录。请求体: { "task_ids": ["id1", "id2", ...] }"""
    from fastapi.responses import JSONResponse
    task_ids = req.get("task_ids", [])
    if not task_ids:
        raise HTTPException(status_code=400, detail="任务ID列表不能为空")
    deleted = 0
    for tid in task_ids:
        info = task_manager.get_task(tid)
        if info and info.status not in ("running", "pending"):
            task_manager.remove_task(tid)
            deleted += 1
    return JSONResponse({"success": True, "message": f"已删除 {deleted} 个任务"})


# ════════════════════ 回调配置接口 ════════════════════

@router.get("/callback/config", response_model=CallbackConfigResponse, summary="查看回调配置", tags=["回调配置"])
async def get_callback_config():
    """查看当前全局回调配置。"""
    return CallbackConfigResponse(
        enabled=getattr(config, "CALLBACK_ENABLED", False),
        url=getattr(config, "CALLBACK_URL", ""),
        max_retries=getattr(config, "CALLBACK_MAX_RETRIES", 3),
        retry_intervals=getattr(config, "CALLBACK_RETRY_INTERVALS", [5, 15, 30]),
    )


@router.post("/callback/config", response_model=CallbackConfigResponse, summary="更新回调配置", tags=["回调配置"])
async def update_callback_config(req: CallbackConfigRequest):
    """
    动态更新全局回调配置（运行时生效，重启后恢复默认值）。

    只传需要修改的字段即可，未传的字段保持不变。
    如需持久化，请修改 config/base_config.py 中的对应配置项。
    """
    if req.enabled is not None:
        config.CALLBACK_ENABLED = req.enabled
    if req.url is not None:
        config.CALLBACK_URL = req.url
    if req.max_retries is not None:
        config.CALLBACK_MAX_RETRIES = req.max_retries
    if req.retry_intervals is not None:
        config.CALLBACK_RETRY_INTERVALS = req.retry_intervals

    return CallbackConfigResponse(
        enabled=config.CALLBACK_ENABLED,
        url=config.CALLBACK_URL,
        max_retries=config.CALLBACK_MAX_RETRIES,
        retry_intervals=config.CALLBACK_RETRY_INTERVALS,
    )


# ════════════════════ Cookie 池管理接口 ════════════════════

@router.get("/cookies", response_model=CookiePoolResponse, summary="查看Cookie池", tags=["Cookie管理"])
async def list_cookies(platform: str = ""):
    """
    查看 Cookie 池当前状态。

    - 不传 platform：返回所有平台的Cookie
    - 传 platform=dy：只返回抖音的Cookie

    返回每个Cookie的 id、有效状态、失败次数等信息。
    """
    from proxy.cookie_pool import cookie_pool
    pool_data = cookie_pool.list_cookies(platform or None)
    stats = cookie_pool.get_stats()
    return CookiePoolResponse(pool=pool_data, stats=stats)


@router.post("/cookies/add", response_model=CookieActionResponse, summary="手动添加Cookie", tags=["Cookie管理"])
async def add_cookie(req: CookieAddRequest):
    """
    通过 API 手动添加 Cookie 到池中。

    适用场景：在浏览器中登录后，从开发者工具(F12)复制Cookie字符串，通过此接口提交。

    在 Postman 中使用：修改 Body 中的 platform 和 cookie 字段即可。
    """
    from proxy.cookie_pool import cookie_pool

    valid_platforms = ["dy", "bili", "ks", "xhs", "wb", "toutiao"]
    if req.platform not in valid_platforms:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的平台: {req.platform}，可选: {valid_platforms}"
        )
    if not req.cookie.strip():
        raise HTTPException(status_code=400, detail="Cookie 不能为空")

    cookie_id = cookie_pool.add_cookie(
        platform=req.platform,
        cookie_str=req.cookie.strip(),
        note=req.note,
    )

    # DB 模式也同步写入数据库
    if getattr(config, "COOKIE_POOL_SOURCE", "file") == "db":
        await _save_cookie_to_db(req.platform, cookie_id, req.cookie.strip(), req.note)

    return CookieActionResponse(
        success=True, message="Cookie 添加成功", cookie_id=cookie_id
    )


@router.post("/cookies/remove", response_model=CookieActionResponse, summary="删除Cookie", tags=["Cookie管理"])
async def remove_cookie(req: CookieRemoveRequest):
    """删除指定平台的指定Cookie。先通过 GET /cookies 查看现有ID，再调用此接口删除。"""
    from proxy.cookie_pool import cookie_pool

    success = cookie_pool.remove_cookie(req.platform, req.cookie_id)
    if not success:
        raise HTTPException(status_code=404, detail="Cookie 不存在")

    # DB 模式同步删除
    if getattr(config, "COOKIE_POOL_SOURCE", "file") == "db":
        await _remove_cookie_from_db(req.platform, req.cookie_id)

    return CookieActionResponse(success=True, message="Cookie 已删除")


@router.post("/cookies/reload", response_model=CookieActionResponse, summary="重新加载Cookie池", tags=["Cookie管理"])
async def reload_cookies():
    """重新从数据库/文件加载Cookie池到内存。当手动修改了数据库中的Cookie后，调用此接口刷新。"""
    from proxy.cookie_pool import cookie_pool
    await cookie_pool.load()
    stats = cookie_pool.get_stats()
    total = sum(s["valid"] for s in stats.values())
    return CookieActionResponse(
        success=True, message=f"Cookie 池已重新加载，共 {total} 条有效Cookie"
    )


@router.post("/cookies/remove/batch", response_model=CookieActionResponse, summary="批量删除Cookie", tags=["Cookie管理"])
async def batch_remove_cookies(req: dict):
    """
    批量删除多个Cookie。

    请求体: { "items": [{"platform": "dy", "cookie_id": "dy_01"}, ...] }
    """
    from proxy.cookie_pool import cookie_pool

    items = req.get("items", [])
    if not items:
        raise HTTPException(status_code=400, detail="删除列表不能为空")

    deleted = 0
    for item in items:
        platform = item.get("platform", "")
        cookie_id = item.get("cookie_id", "")
        if cookie_pool.remove_cookie(platform, cookie_id):
            deleted += 1
        if getattr(config, "COOKIE_POOL_SOURCE", "file") == "db":
            await _remove_cookie_from_db(platform, cookie_id)

    return CookieActionResponse(success=True, message=f"批量删除完成，已删除 {deleted} 条Cookie")


async def _save_cookie_to_db(platform: str, cookie_id: str, cookie_str: str, note: str):
    """将新 Cookie 写入数据库"""
    try:
        from database.external_db import external_db
        await external_db.ensure_pool()
        sql = (
            "INSERT INTO cookie_pool (platform, cookie_id, cookie_str, note, is_valid) "
            "VALUES (%s, %s, %s, %s, 1) "
            "ON DUPLICATE KEY UPDATE cookie_str = VALUES(cookie_str), note = VALUES(note), is_valid = 1"
        )
        async with external_db._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (platform, cookie_id, cookie_str, note))
    except Exception as e:
        utils.logger.error(f"[API] Cookie写入DB失败: {e}")


async def _remove_cookie_from_db(platform: str, cookie_id: str):
    """从数据库删除 Cookie"""
    try:
        from database.external_db import external_db
        await external_db.ensure_pool()
        sql = "DELETE FROM cookie_pool WHERE platform = %s AND cookie_id = %s"
        async with external_db._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (platform, cookie_id))
    except Exception as e:
        utils.logger.error(f"[API] Cookie从DB删除失败: {e}")


# ════════════════════ 扫码登录接口 ════════════════════

import asyncio
import base64
import uuid
from typing import Dict as DictType

# 扫码会话存储（内存）
_scan_sessions: DictType[str, dict] = {}

# 各平台登录页配置
# needs_login: 是否需要扫码登录（False 则自动采集页面 cookie 作为虚拟 Cookie）
# login_selectors: 打开登录弹窗需要点击的按钮选择器列表（按优先级尝试）
# check_cookie: 判断登录成功的 cookie 名
# check_cookie_min_len: cookie 值最小长度（排除页面自动设置的空/短 cookie）
_SCAN_PLATFORM_CONFIG = {
    "dy": {
        "name": "抖音",
        "url": "https://www.douyin.com",
        "needs_login": True,
        "login_selectors": [],  # 抖音自动弹出登录二维码
        "check_cookie": "sessionid",
        "check_cookie_min_len": 10,
    },
    "bili": {
        "name": "B站",
        "url": "https://www.bilibili.com",
        "needs_login": True,
        "login_selectors": [
            ".header-login-entry",
            ".login-btn",
            "text=登录",
        ],
        "check_cookie": "SESSDATA",
        "check_cookie_min_len": 10,
    },
    "ks": {
        "name": "快手",
        "url": "https://www.kuaishou.com",
        "needs_login": True,
        "login_selectors": [
            "text=立即登录",
            "text=登录",
            ".login-btn",
        ],
        "check_cookie": "userId",
        "check_cookie_min_len": 3,
    },
    "xhs": {
        "name": "小红书",
        "url": "https://www.xiaohongshu.com",
        "needs_login": True,
        "login_selectors": [
            ".login-btn",
            "text=登录",
        ],
        "check_cookie": "web_session",
        "check_cookie_min_len": 10,
    },
    "wb": {
        "name": "微博",
        "url": "https://passport.weibo.com/sso/signin?entry=miniblog&source=miniblog",
        "needs_login": True,
        "login_selectors": [
            "text=扫码登录",
            ".qrcode-btn",
            "text=登录",
        ],
        "check_cookie": "SSOLoginState",
        "check_cookie_min_len": 5,
    },
    "toutiao": {
        "name": "今日头条",
        "url": "https://www.toutiao.com",
        "needs_login": True,
        # 头条登录按钮在视口外（x>3000），需用 JS 点击；js: 前缀触发 evaluate
        "login_selectors": [
            "js:a.login-button",
            "text=登录",
            "text=立即登录",
        ],
        # uid_tt 仅在扫码登录成功后才出现，ttwid 是页面自动生成的跟踪 cookie 不能用于判断登录
        "check_cookie": "uid_tt",
        "check_cookie_min_len": 3,
    },
}

# 默认扫码平台顺序（不含小红书）
_DEFAULT_SCAN_PLATFORMS = ["dy", "ks", "bili", "wb", "toutiao"]
# 不需要登录的平台单独处理（当前所有平台都改为真实扫码）
_NO_LOGIN_PLATFORMS: list[str] = []


@router.post("/cookies/scan/start", summary="启动扫码登录", tags=["扫码登录"])
async def start_scan_login(platform: str = "all", note: str = "", scan_mode: str = "force_new"):
    """
    启动扫码登录会话（支持单平台或全平台串行）。

    - platform="all" 或不传：串行为所有需要登录的平台依次扫码
    - platform="dy" 等：只为指定平台扫码
    - scan_mode 三种模式：
      - "force_new"（默认）：清除浏览器缓存，强制显示二维码，用于添加新账号
      - "refresh"：复用已有登录态，直接录入 Cookie（刷新已有账号）
      - "virtual"：为不需登录的平台生成虚拟 Cookie（如头条）

    返回 session_id，客户端用它轮询状态和获取二维码截图。
    """
    from fastapi.responses import JSONResponse

    valid_modes = ("force_new", "refresh", "virtual")
    if scan_mode not in valid_modes:
        raise HTTPException(status_code=400, detail=f"无效的扫码模式: {scan_mode}，可选: {valid_modes}")

    # 确定扫码平台列表
    if platform == "all":
        if scan_mode == "virtual":
            # 虚拟Cookie模式：为所有平台生成虚拟Cookie（用于无需登录的场景如链接检测）
            platforms_queue = list(_DEFAULT_SCAN_PLATFORMS)
        else:
            platforms_queue = list(_DEFAULT_SCAN_PLATFORMS)
    else:
        if platform not in _SCAN_PLATFORM_CONFIG:
            raise HTTPException(status_code=400, detail=f"不支持的平台: {platform}，可选: {list(_SCAN_PLATFORM_CONFIG.keys())} 或 'all'")
        platforms_queue = [platform]

    session_id = str(uuid.uuid4())[:8]
    first_platform = platforms_queue[0]

    _scan_sessions[session_id] = {
        "platform": first_platform,
        "current_platform": first_platform,
        "platforms_queue": platforms_queue,
        "completed": {},
        "skipped": [],
        "status": "starting",
        "note": note,
        "cookie_id": None,
        "browser_context": None,
        "page": None,
        "playwright": None,
        "scan_mode": scan_mode,
        "cancelled": False,
    }

    # 后台启动串行扫码
    asyncio.create_task(_run_scan_session_serial(session_id, platforms_queue, note, scan_mode))

    mode_descs = {
        "force_new": "强制扫码登录（清除缓存，显示二维码）",
        "refresh": "刷新已有登录态（复用浏览器会话）",
        "virtual": "生成虚拟Cookie（无需登录的平台）",
    }
    return JSONResponse({
        "success": True,
        "message": f"扫码会话已启动，模式: {mode_descs[scan_mode]}，平台队列: {[_SCAN_PLATFORM_CONFIG[p]['name'] for p in platforms_queue]}",
        "cookie_id": session_id,
        "platforms": platforms_queue,
    })


@router.get("/cookies/scan/qrcode/{session_id}", summary="获取扫码二维码截图", tags=["扫码登录"])
async def get_scan_qrcode(session_id: str):
    """
    获取扫码登录页面截图（PNG base64）。

    客户端将 base64 解码为图片展示给用户扫码。
    多平台模式下返回的是当前正在扫码的平台的页面截图。
    """
    from fastapi.responses import JSONResponse

    session = _scan_sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    if session["status"] == "starting":
        return JSONResponse({"status": "starting", "message": "浏览器启动中，请稍后重试"})

    if session["status"] in ("all_done", "failed", "cancelled"):
        return JSONResponse({"status": session["status"], "message": "会话已结束"})

    page = session.get("page")
    if not page:
        return JSONResponse({"status": "error", "message": "页面未就绪"})

    try:
        # 裁剪截图：取页面中央偏大区域（覆盖各平台登录弹窗/QR码位置）
        vp = page.viewport_size or {"width": 1280, "height": 800}
        w, h = vp["width"], vp["height"]
        # 取中心 80% 宽 × 85% 高，确保完整覆盖头条等平台的宽登录弹窗
        clip_w, clip_h = int(w * 0.8), int(h * 0.85)
        clip_x = (w - clip_w) // 2
        clip_y = int(h * 0.05)
        screenshot = await page.screenshot(
            type="png",
            clip={"x": clip_x, "y": clip_y, "width": clip_w, "height": clip_h},
        )
        b64 = base64.b64encode(screenshot).decode()
        current_plat = session.get("current_platform", "")
        plat_name = _SCAN_PLATFORM_CONFIG.get(current_plat, {}).get("name", current_plat)
        return JSONResponse({
            "status": "waiting",
            "current_platform": current_plat,
            "message": f"请使用 {plat_name} App 扫描页面中的二维码",
            "qrcode_base64": b64,
        })
    except Exception as e:
        return JSONResponse({"status": "error", "message": f"截图失败: {e}"})


@router.get("/cookies/scan/status/{session_id}", summary="轮询扫码状态", tags=["扫码登录"])
async def get_scan_status(session_id: str):
    """
    轮询扫码登录状态。

    响应字段：
    - status: starting/waiting/success/timeout/all_done/failed
    - current_platform: 当前正在扫码的平台
    - completed: 已成功的平台及其 cookie_id
    - skipped: 超时跳过的平台
    - platforms_queue: 总平台队列
    """
    from fastapi.responses import JSONResponse

    session = _scan_sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    current_plat = session.get("current_platform", "")
    plat_name = _SCAN_PLATFORM_CONFIG.get(current_plat, {}).get("name", current_plat)

    # 构建状态消息
    status = session["status"]
    if status == "starting":
        msg = f"正在启动 {plat_name} 浏览器..."
    elif status == "clicking_login":
        msg = f"正在打开 {plat_name} 登录页面，等待二维码加载..."
    elif status == "waiting":
        msg = f"请使用 {plat_name} App 扫描二维码"
    elif status == "success":
        msg = f"{plat_name} 登录成功! Cookie: {session.get('cookie_id')}"
    elif status == "timeout":
        msg = f"{plat_name} 扫码超时，已跳过"
    elif status == "cancelled":
        msg = f"扫码已终止。成功 {len(session.get('completed', {}))} 个, 跳过 {len(session.get('skipped', []))} 个"
    elif status == "all_done":
        msg = f"全部完成! 成功 {len(session.get('completed', {}))} 个, 跳过 {len(session.get('skipped', []))} 个"
    else:
        msg = "登录失败"

    return JSONResponse({
        "status": status,
        "current_platform": current_plat,
        "current_platform_name": plat_name,
        "platforms_queue": session.get("platforms_queue", []),
        "completed": session.get("completed", {}),
        "skipped": session.get("skipped", []),
        "cookie_id": session.get("cookie_id"),
        "message": msg,
    })


@router.post("/cookies/scan/cancel/{session_id}", summary="终止扫码会话", tags=["扫码登录"])
async def cancel_scan_session(session_id: str):
    """终止正在进行的扫码登录会话，关闭浏览器并停止后续平台扫码。"""
    from fastapi.responses import JSONResponse

    session = _scan_sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    if session["status"] in ("all_done", "cancelled"):
        return JSONResponse({"success": False, "message": "会话已结束"})

    session["cancelled"] = True

    # 立即关闭当前浏览器
    try:
        if session.get("browser_context"):
            await session["browser_context"].close()
        if session.get("playwright"):
            await session["playwright"].stop()
    except Exception:
        pass
    session["browser_context"] = None
    session["page"] = None
    session["playwright"] = None

    session["status"] = "cancelled"
    utils.logger.info(f"[ScanAPI] 扫码会话 {session_id} 已被用户终止")
    return JSONResponse({"success": True, "message": "扫码会话已终止"})


async def _try_click_login_button(page, platform: str, selectors: list):
    """尝试点击平台登录按钮，触发登录弹窗/二维码显示。
    JS 选择器（js:前缀）会轮询最多 8 秒等待元素出现（适用于动态渲染的页面如头条）。
    """
    for selector in selectors:
        try:
            if selector.startswith("js:"):
                css_sel = selector[3:]
                # 轮询等待元素出现后再点击（页面可能还在动态渲染登录按钮）
                for attempt in range(6):
                    clicked = await page.evaluate(f'''() => {{
                        let el = document.querySelector('{css_sel}');
                        if (el) {{ el.click(); return true; }}
                        return false;
                    }}''')
                    if clicked:
                        utils.logger.info(f"[ScanAPI] {platform} JS点击登录按钮成功: {css_sel}")
                        await asyncio.sleep(2)
                        return True
                    await asyncio.sleep(1.5)
            elif selector.startswith("text="):
                locator = page.get_by_text(selector[5:], exact=False).first
            else:
                locator = page.locator(selector).first

            if not selector.startswith("js:"):
                if await locator.is_visible(timeout=3000):
                    await locator.click()
                    utils.logger.info(f"[ScanAPI] {platform} 点击登录按钮成功: {selector}")
                    await asyncio.sleep(2)
                    return True
        except Exception:
            continue
    utils.logger.info(f"[ScanAPI] {platform} 未找到登录按钮，可能已显示登录界面")
    return False


def _check_login_cookie(cookies: list, check_key: str, min_len: int) -> bool:
    """检查 cookie 列表中是否存在有效的登录标志 cookie"""
    for c in cookies:
        if c["name"] == check_key and len(c.get("value", "")) >= min_len:
            return True
    return False


async def _run_scan_session_serial(session_id: str, platforms: list, note: str, scan_mode: str = "force_new"):
    """
    串行为多个平台执行扫码登录。
    每个平台独立超时（120秒），超时后自动跳过进入下一个。

    scan_mode:
      - "force_new": 清除浏览器数据，强制二维码登录
      - "refresh": 复用已有登录态
      - "virtual": 不需要登录，直接采集页面 cookie
    """
    import shutil
    import time
    from playwright.async_api import async_playwright

    session = _scan_sessions[session_id]
    per_platform_timeout = 120  # 每个平台120秒超时

    for platform in platforms:
        # 检查是否已被取消
        if session.get("cancelled"):
            utils.logger.info(f"[ScanAPI] 会话已取消，跳过剩余平台")
            break

        pcfg = _SCAN_PLATFORM_CONFIG[platform]
        session["current_platform"] = platform
        session["platform"] = platform
        session["status"] = "starting"
        session["cookie_id"] = None

        # 虚拟Cookie模式 或 平台不需要登录
        if scan_mode == "virtual" or not pcfg["needs_login"]:
            await _generate_virtual_cookie(session, platform, note)
            if platform != platforms[-1]:
                await asyncio.sleep(1)
            continue

        try:
            pw = await async_playwright().start()
            user_data_dir = os.path.join(
                os.getcwd(), "browser_data", f"scan_api_{platform}"
            )

            # 强制扫码模式：清除浏览器持久化数据
            if scan_mode == "force_new" and os.path.exists(user_data_dir):
                try:
                    shutil.rmtree(user_data_dir)
                    utils.logger.info(f"[ScanAPI] 强制扫码模式: 已清除 {platform} 浏览器缓存")
                except Exception as e:
                    utils.logger.warning(f"[ScanAPI] 清除浏览器缓存失败: {e}")

            # 使用 Windows UA + stealth 反检测，防止抖音等平台检测到 Linux 环境或自动化特征
            _scan_user_agent = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
            browser_context = await pw.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=False,
                viewport={"width": 1280, "height": 800},
                accept_downloads=True,
                user_agent=_scan_user_agent,
            )

            # 注入反检测脚本 + 平台指纹覆写（Linux 上 navigator.platform 默认是 "Linux x86_64"，需伪装为 Windows）
            _stealth_js_path = os.path.join(os.getcwd(), "libs", "stealth.min.js")
            if os.path.exists(_stealth_js_path):
                await browser_context.add_init_script(path=_stealth_js_path)
            await browser_context.add_init_script("""
                Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
                Object.defineProperty(navigator, 'userAgentData', {
                    get: () => ({ platform: 'Windows', brands: [
                        {brand: 'Chromium', version: '124'},
                        {brand: 'Google Chrome', version: '124'},
                    ], mobile: false })
                });
            """)

            page = await browser_context.new_page()
            session["browser_context"] = browser_context
            session["page"] = page
            session["playwright"] = pw

            await page.goto(pcfg["url"], wait_until="domcontentloaded")
            await asyncio.sleep(3)

            if session.get("cancelled"):
                break

            # 头条首页会弹出"添加到桌面"浮层，需先关闭以免遮挡登录弹窗
            if platform == "toutiao":
                try:
                    close_btn = page.locator('.pwa-download-popup .close, [class*="pwa"] [class*="close"]').first
                    if await close_btn.is_visible(timeout=2000):
                        await close_btn.click()
                        await asyncio.sleep(0.5)
                except Exception:
                    pass

            # 自动点击登录按钮（非抖音平台需要手动触发登录弹窗）
            if pcfg["login_selectors"] and scan_mode == "force_new":
                session["status"] = "clicking_login"
                await _try_click_login_button(page, platform, pcfg["login_selectors"])
                await asyncio.sleep(2)

            session["status"] = "waiting"

            if session.get("cancelled"):
                break

            # 轮询等待登录
            check_key = pcfg["check_cookie"]
            min_len = pcfg.get("check_cookie_min_len", 5)
            start = time.time()
            logged_in = False

            # refresh 模式下先检查当前是否已登录
            if scan_mode == "refresh":
                cookies = await browser_context.cookies()
                if _check_login_cookie(cookies, check_key, min_len):
                    cookie_str = ";".join(f"{c['name']}={c['value']}" for c in cookies)
                    logged_in = True
                    from proxy.cookie_pool import cookie_pool
                    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                    cookie_note = note or f"刷新登录态 {timestamp}"
                    cookie_id = cookie_pool.add_cookie(platform=platform, cookie_str=cookie_str, note=cookie_note)
                    await _save_cookie_to_db(platform, cookie_id, cookie_str, cookie_note)
                    session["status"] = "success"
                    session["cookie_id"] = cookie_id
                    session["completed"][platform] = cookie_id
                    utils.logger.info(f"[ScanAPI] {platform} 刷新登录态成功: id={cookie_id}")

            if not logged_in:
                while time.time() - start < per_platform_timeout:
                    if session.get("cancelled"):
                        break
                    cookies = await browser_context.cookies()
                    if _check_login_cookie(cookies, check_key, min_len):
                        cookie_str = ";".join(f"{c['name']}={c['value']}" for c in cookies)
                        from proxy.cookie_pool import cookie_pool
                        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                        cookie_note = note or f"API扫码 {timestamp}"

                        cookie_id = cookie_pool.add_cookie(
                            platform=platform,
                            cookie_str=cookie_str,
                            note=cookie_note,
                        )
                        await _save_cookie_to_db(platform, cookie_id, cookie_str, cookie_note)

                        session["status"] = "success"
                        session["cookie_id"] = cookie_id
                        session["completed"][platform] = cookie_id
                        utils.logger.info(f"[ScanAPI] {platform} 扫码登录成功: id={cookie_id}")
                        logged_in = True
                        break
                    await asyncio.sleep(2)

            if not logged_in and not session.get("cancelled"):
                session["status"] = "timeout"
                session["skipped"].append(platform)
                utils.logger.info(f"[ScanAPI] {platform} 扫码超时，跳过")

        except Exception as e:
            if not session.get("cancelled"):
                session["status"] = "failed"
                session["skipped"].append(platform)
                utils.logger.error(f"[ScanAPI] {platform} 扫码异常: {e}")
        finally:
            try:
                if session.get("browser_context"):
                    await session["browser_context"].close()
                if session.get("playwright"):
                    await session["playwright"].stop()
            except Exception:
                pass
            session["browser_context"] = None
            session["page"] = None
            session["playwright"] = None

        # 平台间等待2秒再开始下一个
        if platform != platforms[-1] and not session.get("cancelled"):
            await asyncio.sleep(2)

    # 全部平台处理完毕
    if not session.get("cancelled"):
        session["status"] = "all_done"
    else:
        session["status"] = "cancelled"
    session["current_platform"] = ""
    utils.logger.info(
        f"[ScanAPI] 全平台扫码完成: 成功={list(session['completed'].keys())}, "
        f"跳过={session['skipped']}, 取消={'是' if session.get('cancelled') else '否'}"
    )


async def _generate_virtual_cookie(session: dict, platform: str, note: str):
    """为不需要登录的平台生成虚拟Cookie（打开页面采集页面自动生成的cookie）"""
    import time
    from playwright.async_api import async_playwright

    pcfg = _SCAN_PLATFORM_CONFIG[platform]
    session["status"] = "starting"

    try:
        pw = await async_playwright().start()
        browser_context = await pw.chromium.launch_persistent_context(
            user_data_dir=os.path.join(os.getcwd(), "browser_data", f"scan_api_{platform}"),
            headless=True,
            viewport={"width": 1280, "height": 800},
        )
        page = await browser_context.new_page()
        await page.goto(pcfg["url"], wait_until="domcontentloaded")
        await asyncio.sleep(3)

        cookies = await browser_context.cookies()
        if cookies:
            cookie_str = ";".join(f"{c['name']}={c['value']}" for c in cookies)
            from proxy.cookie_pool import cookie_pool
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            cookie_note = note or f"虚拟Cookie(免登录) {timestamp}"

            cookie_id = cookie_pool.add_cookie(platform=platform, cookie_str=cookie_str, note=cookie_note)
            await _save_cookie_to_db(platform, cookie_id, cookie_str, cookie_note)

            session["status"] = "success"
            session["cookie_id"] = cookie_id
            session["completed"][platform] = cookie_id
            utils.logger.info(f"[ScanAPI] {platform} 虚拟Cookie生成成功: id={cookie_id}")
        else:
            session["status"] = "failed"
            session["skipped"].append(platform)
            utils.logger.warning(f"[ScanAPI] {platform} 虚拟Cookie生成失败: 无cookie")

        await browser_context.close()
        await pw.stop()
    except Exception as e:
        session["status"] = "failed"
        session["skipped"].append(platform)
        utils.logger.error(f"[ScanAPI] {platform} 虚拟Cookie异常: {e}")
