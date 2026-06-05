# -*- coding: utf-8 -*-
# FastAPI 路由定义
# 所有 /api/v1 下的端点

import asyncio
import os
import random
import secrets
import time
import tempfile
from typing import List, Optional

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


async def _save_cookie_to_db(
    platform: str,
    cookie_id: str,
    cookie_str: str,
    note: str,
    is_valid: int = 1,
    cookie_type: str = "account",
    account_valid: Optional[int] = None,
    public_detail_valid: Optional[int] = None,
    public_comment_valid: Optional[int] = None,
):
    """将新 Cookie 写入数据库"""
    try:
        from database.external_db import external_db
        await external_db.ensure_pool()
        async with external_db._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SHOW COLUMNS FROM cookie_pool")
                existing_cols = {r[0] for r in await cur.fetchall()}

        account_valid = is_valid if account_valid is None else account_valid
        public_detail_valid = account_valid if public_detail_valid is None else public_detail_valid
        public_comment_valid = account_valid if public_comment_valid is None else public_comment_valid

        # 能力字段是增量改造；旧表没有列时只写旧 is_valid，避免部署时必须同时迁库。
        fields = {
            "platform": platform,
            "cookie_id": cookie_id,
            "cookie_str": cookie_str,
            "note": note,
            "is_valid": is_valid,
            "cookie_type": cookie_type,
            "account_valid": int(bool(account_valid)),
            "public_detail_valid": int(bool(public_detail_valid)),
            "public_comment_valid": int(bool(public_comment_valid)),
        }
        insert_cols = [
            name for name in fields
            if name in {"platform", "cookie_id", "cookie_str", "note", "is_valid"}
            or name in existing_cols
        ]
        placeholders = ", ".join(["%s"] * len(insert_cols))
        updates = ", ".join(
            f"{name} = VALUES({name})"
            for name in insert_cols
            if name not in {"platform", "cookie_id"}
        )
        sql = (
            f"INSERT INTO cookie_pool ({', '.join(insert_cols)}) "
            f"VALUES ({placeholders}) "
            f"ON DUPLICATE KEY UPDATE {updates}"
        )
        async with external_db._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, tuple(fields[name] for name in insert_cols))
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


# ════════════════════ Cookie 刷新/验证接口 ════════════════════

@router.post("/cookies/validate", summary="验证Cookie有效性", tags=["Cookie管理"])
async def validate_cookies(platform: str = ""):
    """
    用平台可用的方式验证 Cookie。
    B站/快手等可验证平台失败会标记失效；微博等跨域不稳定平台只做软验证。
    验证通过会恢复 Cookie 为可用并记录 last_validated_at。
    - platform 为空时验证所有平台
    - 返回各平台验证结果
    """
    from proxy.cookie_pool import cookie_pool

    results = {}
    platforms_to_check = [platform] if platform else list(cookie_pool._pool.keys())

    for plat in platforms_to_check:
        # 验证按钮用于重新判断状态，已被误标失效的 Cookie 也允许参与验证并在通过后恢复。
        entries = [e for e in cookie_pool._pool.get(plat, []) if e.cookie]
        plat_results = []
        for entry in entries:
            try:
                validate_result = await _pong_validate_cookie(plat, entry.cookie)
                if validate_result.get("valid") is True:
                    cookie_pool.mark_validated(plat, entry.id)
                elif validate_result.get("valid") is False and validate_result.get("mark_invalid"):
                    cookie_pool.mark_invalid(plat, entry.id)
                if validate_result.get("public_detail_valid") is True:
                    cookie_pool.mark_public_detail_valid(
                        plat,
                        entry.id,
                        True,
                        cookie_type=validate_result.get("cookie_type"),
                    )
                elif validate_result.get("public_detail_valid") is False:
                    cookie_pool.mark_public_detail_valid(plat, entry.id, False)
                plat_results.append({"cookie_id": entry.id, **validate_result})
            except Exception as e:
                plat_results.append({
                    "cookie_id": entry.id,
                    "valid": None,
                    "status": "unstable",
                    "reason": f"验证异常: {e}",
                    "confidence": "none",
                })
        results[plat] = plat_results

    return {"success": True, "results": results}


def _check_cookie_required_fields(platform: str, cookie_dict: dict) -> tuple[bool, str]:
    """按扫码保存的 required_cookies/check_cookie 规则做本地结构校验。"""
    pcfg = globals().get("_SCAN_PLATFORM_CONFIG", {}).get(platform, {})
    required = pcfg.get("required_cookies") or []
    if required:
        missing = [name for name in required if len(cookie_dict.get(name, "")) < 3]
        if missing:
            return False, f"缺少关键Cookie: {', '.join(missing)}"
        return True, "关键Cookie字段完整"

    check_key = pcfg.get("check_cookie")
    if check_key:
        min_len = pcfg.get("check_cookie_min_len", 3)
        if len(cookie_dict.get(check_key, "")) < min_len:
            return False, f"缺少或过短的关键Cookie: {check_key}"
    return True, "Cookie字段完整"


async def _pong_validate_cookie(platform: str, cookie_str: str) -> dict:
    """
    独立验证（供 /validate API 使用，非扫码流程）。
    返回:
    - valid=True：验证通过，可恢复为可用
    - valid=False：高置信失败，可由调用方标失效
    - valid=None：接口风控/网络/SSO 跨域等不确定结果
    """
    from proxy.cookie_pool import CookiePool
    cookie_dict = CookiePool.parse_cookie_string(cookie_str)
    fields_ok, fields_reason = _check_cookie_required_fields(platform, cookie_dict)

    if not fields_ok:
        detail_ok, detail_reason = await _validate_public_detail_cookie(
            platform, cookie_str, cookie_dict
        )
        return {
            "valid": False,
            "status": "public_detail_valid" if detail_ok else "invalid",
            "reason": f"{fields_reason}；{detail_reason}",
            "confidence": "public_detail" if detail_ok else "cookie_fields",
            "mark_invalid": True,
            "cookie_type": "public_session" if detail_ok else "virtual",
            "account_valid": False,
            "public_detail_valid": detail_ok,
            "public_comment_valid": False,
        }

    if platform == "wb":
        result = await _validate_weibo_cookie(cookie_str, cookie_dict, fields_reason)
        if result.get("valid") is True:
            result.update({
                "cookie_type": "account",
                "account_valid": True,
                "public_detail_valid": True,
                "public_comment_valid": True,
            })
            return result
        detail_ok, detail_reason = await _validate_public_detail_cookie(
            platform, cookie_str, cookie_dict
        )
        result.update({
            "cookie_type": "public_session" if detail_ok else "virtual",
            "account_valid": False,
            "public_detail_valid": detail_ok,
            "public_comment_valid": False,
            "reason": f"{result.get('reason', fields_reason)}；{detail_reason}",
        })
        return result

    if platform == "dy":
        return {
            "valid": True,
            "status": "valid",
            "reason": fields_reason,
            "confidence": "cookie_fields",
            "cookie_type": "account",
            "account_valid": True,
            "public_detail_valid": True,
            "public_comment_valid": True,
        }

    try:
        if platform == "ks":
            from media_platform.kuaishou.client import KuaiShouClient
            headers = {
                "User-Agent": utils.get_user_agent(),
                "Cookie": cookie_str,
                "Content-Type": "application/json;charset=UTF-8",
                "Origin": "https://www.kuaishou.com",
                "Referer": "https://www.kuaishou.com/search",
            }
            client = KuaiShouClient(headers=headers, playwright_page=None, cookie_dict=cookie_dict)
            for attempt in range(2):
                if await client.pong():
                    return {
                        "valid": True,
                        "status": "valid",
                        "reason": "快手 GraphQL pong 通过",
                        "confidence": "pong",
                        "cookie_type": "account",
                        "account_valid": True,
                        "public_detail_valid": True,
                        "public_comment_valid": True,
                    }
                if attempt == 0:
                    await asyncio.sleep(1)
            detail_ok, detail_reason = await _validate_public_detail_cookie(
                platform, cookie_str, cookie_dict
            )
            return {
                "valid": False,
                "status": "public_detail_valid" if detail_ok else "invalid",
                "reason": f"{fields_reason}，但快手 GraphQL pong 连续失败；{detail_reason}",
                "confidence": "public_detail" if detail_ok else "pong",
                "mark_invalid": True,
                "cookie_type": "public_session" if detail_ok else "virtual",
                "account_valid": False,
                "public_detail_valid": detail_ok,
                "public_comment_valid": False,
            }

        elif platform == "bili":
            from media_platform.bilibili.client import BilibiliClient
            headers = {
                "User-Agent": utils.get_user_agent(),
                "Cookie": cookie_str,
                "Origin": "https://www.bilibili.com",
                "Referer": "https://www.bilibili.com",
                "Content-Type": "application/json;charset=UTF-8",
            }
            client = BilibiliClient(headers=headers, playwright_page=None, cookie_dict=cookie_dict)
            for attempt in range(3):
                if await client.pong():
                    return {
                        "valid": True,
                        "status": "valid",
                        "reason": "B站 pong 通过",
                        "confidence": "pong",
                        "cookie_type": "account",
                        "account_valid": True,
                        "public_detail_valid": True,
                        "public_comment_valid": True,
                    }
                if attempt < 2:
                    await asyncio.sleep(1)
            detail_ok, detail_reason = await _validate_public_detail_cookie(
                platform, cookie_str, cookie_dict
            )
            return {
                "valid": False,
                "status": "public_detail_valid" if detail_ok else "invalid",
                "reason": f"{fields_reason}，但 B站 pong 连续失败；{detail_reason}",
                "confidence": "public_detail" if detail_ok else "pong",
                "mark_invalid": True,
                "cookie_type": "public_session" if detail_ok else "virtual",
                "account_valid": False,
                "public_detail_valid": detail_ok,
                "public_comment_valid": False,
            }

        else:
            # 头条等暂无可靠 httpx pong，详情检测按公开能力处理，不恢复账号态。
            detail_ok, detail_reason = await _validate_public_detail_cookie(
                platform, cookie_str, cookie_dict
            )
            return {
                "valid": False,
                "status": "public_detail_valid" if detail_ok else "unstable",
                "reason": f"{fields_reason}；{detail_reason}",
                "confidence": "public_detail" if detail_ok else "cookie_fields",
                "mark_invalid": True,
                "cookie_type": "public_session" if detail_ok else "virtual",
                "account_valid": False,
                "public_detail_valid": detail_ok,
                "public_comment_valid": False,
            }

    except Exception as e:
        utils.logger.warning(f"[Validate] {platform} pong异常: {e}")
        return {
            "valid": None,
            "status": "unstable",
            "reason": f"{fields_reason}，但 pong 异常: {e}",
            "confidence": "cookie_fields",
            "account_valid": None,
            "public_detail_valid": None,
            "public_comment_valid": None,
        }


async def _validate_public_detail_cookie(
    platform: str,
    cookie_str: str,
    cookie_dict: dict,
) -> tuple[bool, str]:
    """用公开详情 benchmark 验证非账号 Cookie 能力，避免把 pong 失败误判为完全不可用。"""
    try:
        if platform == "ks":
            from media_platform.kuaishou.client import KuaiShouClient
            headers = {
                "User-Agent": utils.get_user_agent(),
                "Cookie": cookie_str,
                "Content-Type": "application/json;charset=UTF-8",
                "Origin": "https://www.kuaishou.com",
                "Referer": "https://www.kuaishou.com/search",
            }
            client = KuaiShouClient(headers=headers, playwright_page=None, cookie_dict=cookie_dict)
            data = await client.get_video_info("3xru5cs4ju2pzjs")
            ok = bool(data.get("visionVideoDetail"))
            return ok, "快手公开详情 benchmark 通过" if ok else "快手公开详情 benchmark 无数据"

        if platform == "wb":
            from media_platform.weibo.client import WeiboClient
            headers = {
                "User-Agent": utils.get_user_agent(),
                "Cookie": cookie_str,
                "Referer": "https://weibo.com/",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
            client = WeiboClient(headers=headers, playwright_page=None, cookie_dict=cookie_dict)
            data = await client.get_note_info_by_url("https://weibo.com/1249548545/QFjPUrjSg")
            ok = bool(data.get("mblog") or data.get("status"))
            return ok, "微博公开详情 benchmark 通过" if ok else "微博公开详情 benchmark 无数据"

        if platform == "bili":
            from media_platform.bilibili.client import BilibiliClient
            headers = {
                "User-Agent": utils.get_user_agent(),
                "Cookie": cookie_str,
                "Origin": "https://www.bilibili.com",
                "Referer": "https://www.bilibili.com",
            }
            client = BilibiliClient(headers=headers, playwright_page=None, cookie_dict=cookie_dict)
            data = await client.get_video_info(bvid="BV1godYBUE3f")
            ok = bool(data.get("View") or data.get("Card") or data.get("Tags"))
            return ok, "B站公开详情 benchmark 通过" if ok else "B站公开详情 benchmark 无数据"

        if platform == "toutiao":
            return True, "头条详情检测走免账号路径"

        if platform == "dy":
            return False, "抖音公开详情 benchmark 不支持纯非账号 Cookie"

        return False, "当前平台未配置公开详情 benchmark"
    except Exception as e:
        utils.logger.info(f"[Validate] {platform} 公开详情 benchmark 失败: {e}")
        return False, f"公开详情 benchmark 失败: {e}"


async def _validate_weibo_cookie(cookie_str: str, cookie_dict: dict, fields_reason: str) -> dict:
    """
    微博验证采用两段式：
    1. 先用 m.weibo.cn/api/config 快速判断；
    2. 快速接口不认时，再用浏览器注入到 weibo.com/weibo.cn，按举报真实场景验证。
    """
    fast_result = await _validate_weibo_mobile_config(cookie_str)
    if fast_result is True:
        return {
            "valid": True,
            "status": "valid",
            "reason": "微博 m.weibo.cn/api/config 验证通过",
            "confidence": "pong",
        }

    browser_result = await _validate_weibo_with_browser(cookie_dict)
    if browser_result.get("valid") is True:
        return {
            "valid": True,
            "status": "valid",
            "reason": browser_result.get("reason", "微博浏览器登录态验证通过"),
            "confidence": "browser",
        }
    if browser_result.get("valid") is False:
        return {
            "valid": False,
            "status": "invalid",
            "reason": browser_result.get("reason", f"{fields_reason}，但微博浏览器验证未登录"),
            "confidence": "browser",
            "mark_invalid": True,
        }

    return {
        "valid": None,
        "status": "unstable",
        "reason": browser_result.get("reason", f"{fields_reason}，但微博验证结果不确定"),
        "confidence": "cookie_fields",
    }


async def _validate_weibo_mobile_config(cookie_str: str) -> Optional[bool]:
    """调用微博移动端配置接口；True=已登录，False=明确未登录，None=接口异常/不可判。"""
    try:
        from tools.httpx_util import make_async_client
        headers = {
            "User-Agent": utils.get_mobile_user_agent(),
            "Cookie": cookie_str,
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://m.weibo.cn/",
            "Origin": "https://m.weibo.cn",
            "X-Requested-With": "XMLHttpRequest",
        }
        async with make_async_client(follow_redirects=True) as client:
            resp = await client.get(
                "https://m.weibo.cn/api/config",
                headers=headers,
                timeout=15,
            )
        data = resp.json()
        if data.get("ok") == 1:
            login = data.get("data", {}).get("login")
            if login is True:
                return True
            if login is False:
                return False
    except Exception as e:
        utils.logger.debug(f"[Validate-wb] m.weibo.cn/api/config 验证异常: {e}")
    return None


async def _validate_weibo_with_browser(cookie_dict: dict) -> dict:
    """用浏览器按实际投诉/PC微博场景验证微博 Cookie。"""
    from playwright.async_api import async_playwright

    browser = None
    context = None
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1366, "height": 768},
            )
            stealth_js = os.path.join(os.getcwd(), "libs", "stealth.min.js")
            if os.path.exists(stealth_js):
                await context.add_init_script(path=stealth_js)

            browser_cookies = []
            for domain in (".weibo.com", ".weibo.cn"):
                for name, value in cookie_dict.items():
                    if value:
                        browser_cookies.append({
                            "name": name,
                            "value": value,
                            "domain": domain,
                            "path": "/",
                            "secure": True,
                        })
            if browser_cookies:
                await context.add_cookies(browser_cookies)

            page = await context.new_page()
            response = await page.goto("https://weibo.com", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)
            status_code = response.status if response else 0

            state = await page.evaluate("""async () => {
                const bodyText = document.body ? document.body.innerText : "";
                const cfg = window.$CONFIG || window.__CONFIG__ || {};
                const uid = String(cfg.uid || cfg.user?.id || cfg.viewer?.id || "");
                const nick = String(cfg.nick || cfg.user?.screen_name || "");
                let apiOk = false;
                let apiReason = "";
                for (const path of ["/ajax/statuses/config", "/ajax/profile/info"]) {
                    try {
                        const resp = await fetch(path, { credentials: "include" });
                        const text = await resp.text();
                        let data = {};
                        try { data = JSON.parse(text); } catch {}
                        const payload = data.data || data;
                        if (payload && (payload.login === true || payload.uid || payload.id || payload.user?.id)) {
                            apiOk = true;
                            apiReason = path;
                            break;
                        }
                    } catch (e) {}
                }
                return {
                    url: location.href,
                    uid,
                    nick,
                    apiOk,
                    apiReason,
                    hasLoginText: /扫码登录|账号登录|登录微博|立即登录|注册/.test(bodyText),
                    hasRiskText: /访问频繁|安全验证|验证码|异常访问|请稍后再试/.test(bodyText),
                    hasUserText: /我的首页|发微博|私信|消息|退出登录/.test(bodyText),
                    textPreview: bodyText.slice(0, 500)
                };
            }""")

            if state.get("apiOk"):
                return {"valid": True, "reason": f"微博 PC API 验证通过: {state.get('apiReason')}"}
            if state.get("uid") or state.get("hasUserText"):
                return {"valid": True, "reason": "微博 PC 页面检测到登录态"}
            if state.get("hasRiskText") or status_code in (403, 429):
                return {"valid": None, "reason": "微博页面触发风控/访问异常，未自动标失效"}
            if "login" in state.get("url", "") or state.get("hasLoginText"):
                return {"valid": False, "reason": "微博 PC 页面显示未登录/登录入口"}
            return {"valid": None, "reason": "微博浏览器验证未能确认登录态"}
    except Exception as e:
        utils.logger.warning(f"[Validate-wb] 浏览器验证异常: {e}")
        return {"valid": None, "reason": f"微博浏览器验证异常: {e}"}
    finally:
        if context:
            try:
                await context.close()
            except Exception:
                pass
        if browser:
            try:
                await browser.close()
            except Exception:
                pass


@router.post("/cookies/refresh", summary="刷新Cookie登录态", tags=["Cookie管理"])
async def refresh_cookies(platform: str = ""):
    """
    用 Playwright 打开平台首页刷新Cookie登录态。
    注入旧Cookie后加载页面，让服务端自动续期，再读取新Cookie写回DB。
    异步执行，立即返回 task_id。
    """
    import asyncio as _asyncio
    task_id = f"refresh_{int(time.time())}"

    async def _do_refresh():
        from proxy.cookie_pool import cookie_pool
        plats = [platform] if platform else list(cookie_pool._pool.keys())
        for plat in plats:
            # 刷新可用于恢复误标失效的 Cookie，因此不再只处理 valid=True。
            entries = [e for e in cookie_pool._pool.get(plat, []) if e.cookie]
            for entry in entries:
                try:
                    new_cookie = await _refresh_single_cookie(plat, entry.cookie)
                    if not new_cookie:
                        utils.logger.info(f"[CookieRefresh] {plat}/{entry.id} 未获取到新Cookie")
                        continue

                    validate_result = await _pong_validate_cookie(plat, new_cookie)
                    if validate_result.get("public_detail_valid") is True:
                        cookie_pool.mark_public_detail_valid(
                            plat,
                            entry.id,
                            True,
                            cookie_type=validate_result.get("cookie_type"),
                        )
                    elif validate_result.get("public_detail_valid") is False:
                        cookie_pool.mark_public_detail_valid(plat, entry.id, False)

                    if validate_result.get("valid") is True:
                        if new_cookie != entry.cookie:
                            cookie_pool.update_cookie_str(plat, entry.id, new_cookie)
                            utils.logger.info(f"[CookieRefresh] {plat}/{entry.id} Cookie内容已更新")
                        cookie_pool.mark_refreshed(plat, entry.id)
                        utils.logger.info(
                            f"[CookieRefresh] {plat}/{entry.id} 刷新后验证通过，已记录刷新时间"
                        )
                    elif validate_result.get("valid") is False and validate_result.get("mark_invalid"):
                        cookie_pool.mark_invalid(plat, entry.id)
                        utils.logger.warning(
                            f"[CookieRefresh] {plat}/{entry.id} 刷新后验证失败，已标记失效: "
                            f"{validate_result.get('reason')}"
                        )
                    else:
                        if new_cookie != entry.cookie:
                            # 验证不确定时只保留服务端 Set-Cookie 更新，不恢复有效状态。
                            cookie_pool.update_cookie_str(plat, entry.id, new_cookie)
                        utils.logger.warning(
                            f"[CookieRefresh] {plat}/{entry.id} 刷新后验证不确定，未恢复有效状态: "
                            f"{validate_result.get('reason')}"
                        )
                except Exception as e:
                    utils.logger.warning(f"[CookieRefresh] {plat}/{entry.id} 刷新失败: {e}")

    _asyncio.create_task(_do_refresh())
    return {"success": True, "message": "Cookie刷新任务已启动", "task_id": task_id}


async def _refresh_single_cookie(platform: str, cookie_str: str) -> Optional[str]:
    """用 Playwright 打开平台首页，注入旧Cookie让服务端刷新，返回新Cookie字符串"""
    from playwright.async_api import async_playwright
    from proxy.cookie_pool import CookiePool

    platform_urls = {
        "dy": "https://www.douyin.com",
        "bili": "https://www.bilibili.com",
        "ks": "https://www.kuaishou.com",
        "wb": "https://m.weibo.cn",
        "toutiao": "https://www.toutiao.com",
        "xhs": "https://www.xiaohongshu.com",
    }
    home_url = platform_urls.get(platform)
    if not home_url:
        return None

    cookie_dict = CookiePool.parse_cookie_string(cookie_str)
    domain = home_url.replace("https://", "").replace("http://", "").rstrip("/")
    if domain.startswith("www."):
        domain = domain[4:]
    if domain.startswith("m."):
        domain = domain[2:]

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=utils.get_user_agent(),
            viewport={"width": 1920, "height": 1080},
        )
        await context.add_init_script(path="libs/stealth.min.js")

        # 注入旧Cookie
        browser_cookies = [
            {"name": k, "value": v, "domain": f".{domain}", "path": "/",
             "secure": True, "httpOnly": False}
            for k, v in cookie_dict.items()
        ]
        if browser_cookies:
            await context.add_cookies(browser_cookies)

        page = await context.new_page()
        try:
            await page.goto(home_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)

            # 读取刷新后的Cookie
            new_cookies = await context.cookies()
            new_cookie_str = ";".join(f"{c['name']}={c['value']}" for c in new_cookies)
            return new_cookie_str
        except Exception as e:
            utils.logger.warning(f"[CookieRefresh] {platform} 页面加载失败: {e}")
            return None
        finally:
            await context.close()
            await browser.close()


# ════════════════════ 扫码登录接口 ════════════════════

import base64
import uuid
from typing import Dict as DictType

# 扫码会话存储（内存）
_scan_sessions: DictType[str, dict] = {}

# 虚拟 Cookie 只能用于免登录/公开数据能力测试，不能混入真实账号登录态。
# 这些字段一旦出现，通常代表浏览器目录里残留了账号会话，需要过滤掉。
_VIRTUAL_COOKIE_ACCOUNT_NAMES = {
    "sessionid", "sessionid_ss", "sid_guard", "sid_tt", "uid_tt", "uid_tt_ss",
    "passport_assist_user", "passport_mfa_token", "d_ticket", "n_mh",
    "login_status", "sid_ucp_v1", "ssid_ucp_v1", "is_staff_user",
    "has_biz_token", "userId", "passToken", "sub", "subp", "scf",
    "alf", "alc", "wbpsess", "sessdata", "bili_jct", "dedeuserid",
}


def _is_virtual_account_cookie(platform: str, name: str) -> bool:
    """判断 Cookie 名是否像账号登录态；虚拟模式必须过滤，避免误采历史真实账号。"""
    if not name:
        return True
    lowered = name.lower()
    if lowered in {n.lower() for n in _VIRTUAL_COOKIE_ACCOUNT_NAMES}:
        return True
    if platform == "dy" and lowered.startswith(("sessionid", "sid_", "uid_tt")):
        return True
    if platform == "wb" and lowered in {"sub", "subp", "scf", "alf", "alc", "wbpsess"}:
        return True
    if platform == "ks" and lowered in {"userid", "passtoken"}:
        return True
    return False


def _random_virtual_cookie_pairs(platform: str) -> dict:
    """生成每次都不同的非账号虚拟字段；只使用平台常见匿名字段，避免自定义标记暴露自动化特征。"""
    now_ms = str(int(time.time() * 1000))
    token = secrets.token_hex(16)
    pairs = {}
    if platform == "dy":
        pairs.update({
            "ttwid": f"1%7C{token}%7C{now_ms}%7C{secrets.token_hex(8)}",
            "s_v_web_id": f"verify_{secrets.token_hex(18)}",
            "msToken": secrets.token_urlsafe(88).rstrip("="),
        })
    elif platform == "ks":
        pairs.update({
            "kpf": "PC_WEB",
            "kpn": "KUAISHOU_VISION",
            "clientid": "3",
            "did": f"web_{secrets.token_hex(16)}",
        })
    elif platform == "wb":
        future_ts = str(int(time.time()) + random.randint(86400, 86400 * 30))
        pairs.update({
            # 微博公开详情页会根据 SUB/SUBP/SCF 这类字段进入正常页面链路；
            # 这里使用随机占位值模拟“过期/无效账号形态”，不代表真实登录态。
            "SCF": secrets.token_urlsafe(80).rstrip("="),
            "SUB": "_2A25" + secrets.token_urlsafe(120).rstrip("="),
            "SUBP": "0033WrSXqPxfM72-Ws9jqgMF55529P9D9W5" + secrets.token_urlsafe(60).rstrip("="),
            "ALF": future_ts,
            "ALC": secrets.token_urlsafe(32).rstrip("="),
            "WBPSESS": secrets.token_urlsafe(80).rstrip("="),
            "_T_WM": secrets.token_hex(16),
            "WEIBOCN_FROM": "1110006030",
            "MLOGIN": "0",
        })
    elif platform == "bili":
        pairs.update({
            "buvid3": f"{secrets.token_hex(8).upper()}-{secrets.token_hex(4).upper()}-{secrets.token_hex(4).upper()}",
            "b_nut": str(int(time.time())),
            "b_lsid": secrets.token_hex(4).upper() + "_" + secrets.token_hex(8).upper(),
        })
    elif platform == "toutiao":
        pairs.update({
            "ttwid": f"1%7C{token}%7C{now_ms}%7C{secrets.token_hex(8)}",
            "msToken": secrets.token_urlsafe(88).rstrip("="),
        })
    return pairs


def _build_virtual_cookie_str(platform: str, cookies: list) -> str:
    """把匿名浏览器 Cookie 转为字符串，并追加随机虚拟字段保证每次生成不重复。"""
    pairs = {}
    for c in cookies or []:
        name = (c.get("name") or "").strip()
        value = str(c.get("value") or "").strip()
        if not name or not value:
            continue
        if _is_virtual_account_cookie(platform, name):
            continue
        pairs[name] = value

    # 平台字段本身带随机值，用来保证不复用旧目录时每次生成仍然不同。
    for name, value in _random_virtual_cookie_pairs(platform).items():
        pairs.setdefault(name, value)

    return ";".join(f"{name}={value}" for name, value in pairs.items())

# 各平台登录页配置
# needs_login: 是否需要扫码登录（False 则自动采集页面 cookie 作为虚拟 Cookie）
# login_selectors: 打开登录弹窗需要点击的按钮选择器列表（按优先级尝试）
# required_cookies: 判断登录成功的必需 cookie 列表（所有都存在且长度>=min_len才算成功）
# check_cookie / check_cookie_min_len: 保留兼容旧逻辑的主 cookie 检查
_SCAN_PLATFORM_CONFIG = {
    "dy": {
        "name": "抖音",
        "url": "https://www.douyin.com",
        "needs_login": True,
        "login_selectors": [],
        "check_cookie": "sessionid",
        "check_cookie_min_len": 10,
        "required_cookies": ["sessionid"],
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
        # 只检查 SESSDATA 即可判定登录成功（与原始login.py一致）
        # bili_jct/DedeUserID 随后会自动写入，保存时一并采集
        "required_cookies": ["SESSDATA"],
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
        # 快手必须具备 userId + passToken 才算完整登录态
        # 注：kuaishou.server.web_st 在浏览器cookie中名字不确定，用 passToken 替代
        "required_cookies": ["userId", "passToken"],
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
        "required_cookies": ["web_session"],
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
        "required_cookies": ["SSOLoginState", "SUB"],
    },
    "toutiao": {
        "name": "今日头条",
        "url": "https://www.toutiao.com",
        "needs_login": True,
        "login_selectors": [
            "js:a.login-button",
            "text=登录",
            "text=立即登录",
        ],
        "check_cookie": "uid_tt",
        "check_cookie_min_len": 3,
        "required_cookies": ["uid_tt"],
    },
}

# 默认扫码平台顺序：抖音最慢放最后，快手→微博→今日头条→哔哩哔哩→抖音
_DEFAULT_SCAN_PLATFORMS = ["ks", "wb", "toutiao", "bili", "dy"]
# 不需要登录的平台单独处理（当前所有平台都改为真实扫码）
_NO_LOGIN_PLATFORMS: list[str] = []


@router.post("/cookies/scan/start", summary="启动扫码登录", tags=["扫码登录"])
async def start_scan_login(platform: str = "all", note: str = "", scan_mode: str = "force_new", platforms: str = "", scan_execution: str = "serial"):
    """
    启动扫码登录会话（支持单平台、全平台、或自选平台列表）。

    - platform="all" 或不传：为所有/指定平台扫码
    - platform="dy" 等：只为指定平台扫码
    - platforms="dy,ks,wb"：逗号分隔的自选平台列表（优先级高于platform参数）
    - scan_mode 三种模式：
      - "force_new"（默认）：清除浏览器缓存，强制显示二维码，用于添加新账号
      - "refresh"：复用已有登录态，直接录入 Cookie（刷新已有账号）
      - "virtual"：为不需登录的平台生成虚拟 Cookie（如头条）
    - scan_execution: "serial"（默认）或 "parallel"，控制串行/并行扫码

    返回 session_id，客户端用它轮询状态和获取二维码截图。
    """
    from fastapi.responses import JSONResponse

    valid_modes = ("force_new", "refresh", "virtual")
    if scan_mode not in valid_modes:
        raise HTTPException(status_code=400, detail=f"无效的扫码模式: {scan_mode}，可选: {valid_modes}")

    # 确定扫码平台列表（platforms参数优先，支持自选子集）
    if platforms:
        platforms_queue = [p.strip() for p in platforms.split(",") if p.strip() in _SCAN_PLATFORM_CONFIG]
        if not platforms_queue:
            raise HTTPException(status_code=400, detail=f"platforms参数中无有效平台，可选: {list(_SCAN_PLATFORM_CONFIG.keys())}")
    elif platform == "all":
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
        "scan_execution": scan_execution,
        "cancelled": False,
        "skip_current": False,
        # 并行模式：每个平台独立的 page/context 存储
        "parallel_sessions": {},
    }

    # 根据执行模式启动对应的后台任务
    if scan_execution == "parallel" and len(platforms_queue) > 1:
        asyncio.create_task(_run_scan_session_parallel(session_id, platforms_queue, note, scan_mode))
    else:
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
    # 并行模式下从 parallel_sessions 中获取当前平台的page
    if not page and session.get("parallel_sessions"):
        current_plat = session.get("current_platform", "")
        ps = session["parallel_sessions"].get(current_plat)
        if ps:
            page = ps.get("page")
        # 如果当前平台无page，尝试取第一个可用的
        if not page:
            for plat, ps_data in session["parallel_sessions"].items():
                if ps_data.get("page"):
                    page = ps_data["page"]
                    session["current_platform"] = plat
                    break
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
    elif status == "need_verify":
        msg = f"{plat_name} 需要身份验证，请在下方操作"
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
    JS 选择器（js:前缀）会轮询等待元素出现（适用于动态渲染的页面如头条）。
    点击后等待 1s 让弹窗渲染（由调用方再补充等待）。
    """
    for selector in selectors:
        try:
            if selector.startswith("js:"):
                css_sel = selector[3:]
                for attempt in range(5):
                    clicked = await page.evaluate(f'''() => {{
                        let el = document.querySelector('{css_sel}');
                        if (el) {{ el.click(); return true; }}
                        return false;
                    }}''')
                    if clicked:
                        utils.logger.info(f"[ScanAPI] {platform} JS点击登录按钮成功: {css_sel}")
                        await asyncio.sleep(1)
                        return True
                    await asyncio.sleep(1)
            elif selector.startswith("text="):
                locator = page.get_by_text(selector[5:], exact=False).first
            else:
                locator = page.locator(selector).first

            if not selector.startswith("js:"):
                if await locator.is_visible(timeout=2500):
                    await locator.click()
                    utils.logger.info(f"[ScanAPI] {platform} 点击登录按钮成功: {selector}")
                    await asyncio.sleep(1)
                    return True
        except Exception:
            continue
    utils.logger.info(f"[ScanAPI] {platform} 未找到登录按钮，可能已显示登录界面")
    return False


def _check_login_cookie(cookies: list, check_key: str, min_len: int,
                        required_cookies: list = None) -> bool:
    """
    检查 cookie 列表中是否包含完整的登录态。
    - required_cookies 非空时，列表中所有 cookie 名都必须存在且值长度>=3
    - 兼容旧逻辑：required_cookies 为空时退回单字段检查
    """
    cookie_map = {c["name"]: c.get("value", "") for c in cookies}

    if required_cookies:
        for name in required_cookies:
            val = cookie_map.get(name, "")
            if len(val) < 3:
                # 调试：打出当前所有 cookie 名，帮助定位缺少哪个字段
                all_names = [c["name"] for c in cookies if len(c.get("value", "")) >= 3]
                utils.logger.debug(
                    f"[_check_login_cookie] 缺少必需cookie '{name}'，"
                    f"当前有效cookie名: {all_names}"
                )
                return False
        return True

    val = cookie_map.get(check_key, "")
    return len(val) >= min_len


async def _verify_cookie_with_pong(platform: str, cookie_str: str,
                                    browser_context=None) -> bool:
    """
    用对应平台的 pong() 接口做软校验（非阻塞）。
    扫码时的 cookie 可能因 SSO 跨域等原因导致 httpx 直接请求失败，
    所以 pong() 失败只记录警告，不阻止保存。
    真正的硬校验是 required_cookies 字段完整性检查。
    """
    try:
        from proxy.cookie_pool import CookiePool
        cookie_dict = CookiePool.parse_cookie_string(cookie_str)

        pong_ok = False

        if platform == "bili":
            # B站 pong 已验证可用：API 与扫码同域
            from media_platform.bilibili.client import BilibiliClient
            headers = {
                "User-Agent": utils.get_user_agent(),
                "Cookie": cookie_str,
                "Origin": "https://www.bilibili.com",
                "Referer": "https://www.bilibili.com",
                "Content-Type": "application/json;charset=UTF-8",
            }
            client = BilibiliClient(
                headers=headers,
                playwright_page=None,
                cookie_dict=cookie_dict,
            )
            pong_ok = await client.pong()
        else:
            # 微博：扫码在 passport.weibo.com，API 在 m.weibo.cn，SSO 跨域 cookie 无法直接验证
            # 快手：扫码后 cookie 可能需要浏览器端完成初始化才能用于 GraphQL
            # 抖音：pong 依赖 localStorage，httpx 无法检查
            # 头条/小红书：无可靠的 httpx pong 方法
            # 这些平台全部跳过 pong，仅依赖 required_cookies 字段完整性检查
            return True

        if not pong_ok:
            # pong 失败只记录警告，不阻止保存
            utils.logger.warning(
                f"[ScanAPI] {platform} pong()验证未通过(非阻塞)，Cookie仍会保存"
            )
        return True  # 始终返回 True，不阻止保存

    except Exception as e:
        utils.logger.warning(f"[ScanAPI] {platform} pong验证异常(非阻塞): {e}")
        return True


def _build_scan_cookie_note(note: str, platform: str, fallback_note: str) -> str:
    """
    构建扫码保存备注：
    - 用户填写了备注：自动拼接平台缩写，避免批量扫码时各平台备注相同难区分。
    - 用户未填写备注：回退到原有默认备注文案。
    """
    base_note = (note or "").strip()
    if not base_note:
        return fallback_note

    suffix_map = {
        "dy": "dy",
        "ks": "ks",
        "bili": "bili",
        "wb": "wb",
        "toutiao": "toutiao",
    }
    suffix = suffix_map.get(platform, platform)

    # 兼容重复提交：若用户手动已带平台后缀，则不重复拼接。
    if base_note.lower().endswith(suffix.lower()):
        return base_note
    return f"{base_note}{suffix}"


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
            # 抖音会自动弹出登录弹窗，无需额外等待；其他平台等待页面渲染
            post_load_wait = 1.0 if platform == "dy" else 1.5
            await asyncio.sleep(post_load_wait)

            if session.get("cancelled"):
                break
            if session.get("skip_current"):
                session["skip_current"] = False
                session["status"] = "timeout"
                if platform not in session["skipped"]: session["skipped"].append(platform)
                utils.logger.info(f"[ScanAPI] {platform} 用户在浏览器加载时跳过")
                continue

            # 头条首页的"添加到桌面"浮层会遮挡登录弹窗
            if platform == "toutiao":
                try:
                    close_btn = page.locator('.pwa-download-popup .close, [class*="pwa"] [class*="close"]').first
                    if await close_btn.is_visible(timeout=1500):
                        await close_btn.click()
                        await asyncio.sleep(0.3)
                except Exception:
                    pass

            # 非抖音平台需手动点击登录按钮触发弹窗
            if pcfg["login_selectors"] and scan_mode == "force_new":
                session["status"] = "clicking_login"
                await _try_click_login_button(page, platform, pcfg["login_selectors"])
                await asyncio.sleep(1)

            session["status"] = "waiting"

            if session.get("cancelled"):
                break

            # 检测进入轮询前用户是否已手动跳过
            if session.get("skip_current"):
                session["skip_current"] = False
                session["status"] = "timeout"
                if platform not in session["skipped"]: session["skipped"].append(platform)
                utils.logger.info(f"[ScanAPI] {platform} 用户在等待前已跳过")
                continue

            # 轮询等待登录
            check_key = pcfg["check_cookie"]
            min_len = pcfg.get("check_cookie_min_len", 5)
            req_cookies = pcfg.get("required_cookies")
            start = time.time()
            logged_in = False

            # refresh 模式下先检查当前是否已登录
            if scan_mode == "refresh":
                cookies = await browser_context.cookies()
                if _check_login_cookie(cookies, check_key, min_len, req_cookies):
                    cookie_str = ";".join(f"{c['name']}={c['value']}" for c in cookies)
                    logged_in = True
                    from proxy.cookie_pool import cookie_pool
                    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                    cookie_note = _build_scan_cookie_note(note, platform, f"刷新登录态 {timestamp}")
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
                    # 检测用户手动跳过
                    if session.get("skip_current"):
                        session["skip_current"] = False
                        session["status"] = "timeout"
                        if platform not in session["skipped"]: session["skipped"].append(platform)
                        utils.logger.info(f"[ScanAPI] {platform} 用户手动跳过")
                        logged_in = None  # 标记为跳过（区别于超时）
                        break

                    # 验证码提交期间暂停轮询，避免与submit端点争抢Playwright通道
                    if session.get("_verify_in_progress"):
                        await asyncio.sleep(0.3)
                        continue

                    # 检测抖音身份验证弹窗（仅抖音平台）
                    if platform == "dy":
                        try:
                            has_verify = await page.evaluate('''() => {
                                let body = document.body ? document.body.innerText : '';
                                return body.includes('身份验证') || body.includes('接收短信验证码');
                            }''')
                            if has_verify and session.get("status") != "need_verify":
                                session["status"] = "need_verify"
                                # 检测到身份验证后重置计时器，给用户足够时间完成验证
                                start = time.time()
                                utils.logger.info(f"[ScanAPI] {platform} 检测到身份验证弹窗，已重置超时计时器")
                        except Exception:
                            pass

                    cookies = await browser_context.cookies()
                    if _check_login_cookie(cookies, check_key, min_len, req_cookies):
                        # 等待2秒让服务端写入完整cookie（部分平台分批设置cookie）
                        await asyncio.sleep(2)
                        cookies = await browser_context.cookies()
                        cookie_str = ";".join(f"{c['name']}={c['value']}" for c in cookies)

                        await _verify_cookie_with_pong(platform, cookie_str, browser_context)

                        from proxy.cookie_pool import cookie_pool
                        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                        cookie_note = _build_scan_cookie_note(note, platform, f"API扫码 {timestamp}")

                        cookie_id = cookie_pool.add_cookie(
                            platform=platform,
                            cookie_str=cookie_str,
                            note=cookie_note,
                        )
                        # 先INSERT再UPDATE，否则mark_validated的UPDATE找不到记录
                        await _save_cookie_to_db(platform, cookie_id, cookie_str, cookie_note)
                        cookie_pool.mark_validated(platform, cookie_id)

                        session["status"] = "success"
                        session["cookie_id"] = cookie_id
                        session["completed"][platform] = cookie_id
                        utils.logger.info(f"[ScanAPI] {platform} 扫码登录成功: id={cookie_id}")
                        logged_in = True
                        break
                    await asyncio.sleep(1)

            # logged_in=None 表示用户跳过，不重复标记
            if logged_in is False and not session.get("cancelled"):
                session["status"] = "timeout"
                if platform not in session["skipped"]: session["skipped"].append(platform)
                utils.logger.info(f"[ScanAPI] {platform} 扫码超时，跳过")

        except Exception as e:
            if not session.get("cancelled"):
                session["status"] = "failed"
                if platform not in session["skipped"]: session["skipped"].append(platform)
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


async def _run_scan_session_parallel(session_id: str, platforms: list, note: str, scan_mode: str = "force_new"):
    """
    并行为多个平台同时执行扫码登录。
    每个平台启动独立的浏览器，前端可同时获取多个平台的二维码。
    """
    import shutil
    import time
    from playwright.async_api import async_playwright

    session = _scan_sessions[session_id]
    per_platform_timeout = 120

    async def _scan_single_platform(platform: str):
        """单个平台的并行扫码协程"""
        if session.get("cancelled"):
            return

        pcfg = _SCAN_PLATFORM_CONFIG[platform]

        # 虚拟Cookie模式
        if scan_mode == "virtual" or not pcfg["needs_login"]:
            await _generate_virtual_cookie(session, platform, note)
            return

        try:
            pw = await async_playwright().start()
            user_data_dir = os.path.join(os.getcwd(), "browser_data", f"scan_api_{platform}")

            if scan_mode == "force_new" and os.path.exists(user_data_dir):
                try:
                    shutil.rmtree(user_data_dir)
                except Exception:
                    pass

            _scan_user_agent = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
            browser_context = await pw.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=False,
                viewport={"width": 1280, "height": 800},
                user_agent=_scan_user_agent,
            )

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
            # 并行模式：存储每个平台的独立page引用
            session["parallel_sessions"][platform] = {
                "page": page, "browser_context": browser_context, "playwright": pw
            }

            await page.goto(pcfg["url"], wait_until="domcontentloaded")
            await asyncio.sleep(1.0 if platform == "dy" else 1.5)

            if pcfg["login_selectors"] and scan_mode == "force_new":
                await _try_click_login_button(page, platform, pcfg["login_selectors"])
                await asyncio.sleep(1)

            # 轮询等待登录
            check_key = pcfg["check_cookie"]
            min_len = pcfg.get("check_cookie_min_len", 5)
            req_cookies = pcfg.get("required_cookies")
            start = time.time()
            logged_in = False

            while time.time() - start < per_platform_timeout:
                if session.get("cancelled"):
                    break
                if session.get("skip_current") and session.get("current_platform") == platform:
                    session["skip_current"] = False
                    if platform not in session["skipped"]: session["skipped"].append(platform)
                    utils.logger.info(f"[ScanAPI-Parallel] {platform} 用户手动跳过")
                    logged_in = None  # 标记为跳过，防止循环外重复追加
                    break
                cookies = await browser_context.cookies()
                if _check_login_cookie(cookies, check_key, min_len, req_cookies):
                    # 等待2秒让服务端写入完整cookie
                    await asyncio.sleep(2)
                    cookies = await browser_context.cookies()
                    cookie_str = ";".join(f"{c['name']}={c['value']}" for c in cookies)

                    await _verify_cookie_with_pong(platform, cookie_str, browser_context)

                    from proxy.cookie_pool import cookie_pool
                    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                    cookie_note = _build_scan_cookie_note(note, platform, f"API扫码(并行) {timestamp}")
                    cookie_id = cookie_pool.add_cookie(platform=platform, cookie_str=cookie_str, note=cookie_note)
                    await _save_cookie_to_db(platform, cookie_id, cookie_str, cookie_note)
                    cookie_pool.mark_validated(platform, cookie_id)
                    session["completed"][platform] = cookie_id
                    utils.logger.info(f"[ScanAPI-Parallel] {platform} 扫码登录成功: id={cookie_id}")
                    logged_in = True
                    break
                await asyncio.sleep(1)

            # logged_in=None 表示用户主动跳过，不重复标记
            if logged_in is False and not session.get("cancelled"):
                if platform not in session["skipped"]: session["skipped"].append(platform)
                utils.logger.info(f"[ScanAPI-Parallel] {platform} 扫码超时，跳过")

        except Exception as e:
            if not session.get("cancelled"):
                if platform not in session["skipped"]: session["skipped"].append(platform)
                utils.logger.error(f"[ScanAPI-Parallel] {platform} 扫码异常: {e}")
        finally:
            ps = session["parallel_sessions"].pop(platform, None)
            if ps:
                try:
                    await ps["browser_context"].close()
                    await ps["playwright"].stop()
                except Exception:
                    pass

    # 所有平台并行启动
    session["status"] = "waiting"
    session["current_platform"] = platforms[0]
    tasks = [_scan_single_platform(p) for p in platforms]
    await asyncio.gather(*tasks, return_exceptions=True)

    if not session.get("cancelled"):
        session["status"] = "all_done"
    else:
        session["status"] = "cancelled"
    session["current_platform"] = ""
    utils.logger.info(
        f"[ScanAPI-Parallel] 并行扫码完成: 成功={list(session['completed'].keys())}, "
        f"跳过={session['skipped']}"
    )


async def _generate_virtual_cookie(session: dict, platform: str, note: str):
    """为不需要登录的平台生成虚拟Cookie（匿名上下文+随机占位，不复用历史登录态）"""
    import time
    from playwright.async_api import async_playwright

    pcfg = _SCAN_PLATFORM_CONFIG[platform]
    session["status"] = "starting"
    pw = None
    browser = None
    browser_context = None
    cookies = []

    try:
        try:
            pw = await async_playwright().start()
            browser = await pw.chromium.launch(headless=True)
            browser_context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=utils.get_user_agent(),
            )
            page = await browser_context.new_page()
            # 使用一次性匿名上下文，不落盘；导航失败时也会回退到纯随机虚拟字段。
            try:
                await page.goto(pcfg["url"], wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(3)
                cookies = await browser_context.cookies()
            except Exception as nav_err:
                utils.logger.warning(f"[ScanAPI] {platform} 匿名页面Cookie采集失败，使用纯虚拟Cookie: {nav_err}")
        finally:
            if browser_context:
                await browser_context.close()
            if browser:
                await browser.close()
            if pw:
                await pw.stop()

        cookie_str = _build_virtual_cookie_str(platform, cookies)
        from proxy.cookie_pool import cookie_pool
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        cookie_note = _build_scan_cookie_note(note, platform, f"虚拟Cookie(匿名随机) {timestamp}")

        virtual_detail_ok = platform in {"bili", "toutiao", "wb"}
        cookie_id = cookie_pool.add_cookie(
            platform=platform,
            cookie_str=cookie_str,
            note=cookie_note,
            cookie_type="virtual",
            account_valid=False,
            public_detail_valid=virtual_detail_ok,
            public_comment_valid=False,
        )
        await _save_cookie_to_db(
            platform,
            cookie_id,
            cookie_str,
            cookie_note,
            is_valid=0,
            cookie_type="virtual",
            account_valid=0,
            public_detail_valid=1 if virtual_detail_ok else 0,
            public_comment_valid=0,
        )

        session["status"] = "success"
        session["cookie_id"] = cookie_id
        session["completed"][platform] = cookie_id
        utils.logger.info(
            f"[ScanAPI] {platform} 虚拟Cookie生成成功: id={cookie_id}, "
            f"cookie_count={len(cookie_str.split(';'))}"
        )
    except Exception as e:
        session["status"] = "failed"
        if platform not in session["skipped"]: session["skipped"].append(platform)
        utils.logger.error(f"[ScanAPI] {platform} 虚拟Cookie异常: {e}")


# ════════════════════ 跳过当前平台 ════════════════════

@router.post("/cookies/scan/skip/{session_id}", summary="跳过当前平台", tags=["扫码登录"])
async def skip_current_platform(session_id: str):
    """跳过当前正在扫码的平台，立即进入下一个。"""
    from fastapi.responses import JSONResponse

    session = _scan_sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    if session.get("status") in ("all_done", "cancelled"):
        return JSONResponse({"success": False, "message": "会话已结束"})

    # 设置跳过标记，轮询循环会检测到并退出
    session["skip_current"] = True
    plat = session.get("current_platform", "")
    utils.logger.info(f"[ScanAPI] 用户手动跳过平台: {plat}")
    return JSONResponse({"success": True, "message": f"已标记跳过 {_SCAN_PLATFORM_CONFIG.get(plat, {}).get('name', plat)}"})


# ════════════════════ 刷新二维码 ════════════════════

@router.post("/cookies/scan/refresh/{session_id}", summary="刷新二维码", tags=["扫码登录"])
async def refresh_scan_qrcode(session_id: str):
    """刷新当前扫码页面（重新加载二维码）。二维码过期时使用。"""
    from fastapi.responses import JSONResponse

    session = _scan_sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    plat = session.get("current_platform", "")

    # 并行模式下从 parallel_sessions 获取对应平台的 page
    page = session.get("page")
    parallel_sessions = session.get("parallel_sessions", {})
    if not page and plat in parallel_sessions:
        page = parallel_sessions[plat].get("page")

    if not page:
        return JSONResponse({"success": False, "message": "当前无活跃页面"})

    try:
        await page.reload(wait_until="domcontentloaded")
        await asyncio.sleep(2)
        pcfg = _SCAN_PLATFORM_CONFIG.get(plat, {})
        if pcfg.get("login_selectors") and session.get("scan_mode") == "force_new":
            await _try_click_login_button(page, plat, pcfg["login_selectors"])
        utils.logger.info(f"[ScanAPI] 用户手动刷新二维码: {plat}")
        return JSONResponse({"success": True, "message": "二维码已刷新"})
    except Exception as e:
        return JSONResponse({"success": False, "message": f"刷新失败: {e}"})


# ════════════════════ 身份验证（抖音风控） ════════════════════


async def _find_verify_target(page):
    """定位身份验证弹窗所在的frame/page。
    抖音弹窗可能在iframe中，给每个frame设2秒超时避免阻塞事件循环。
    """
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        try:
            text = await asyncio.wait_for(
                frame.evaluate("() => document.body ? document.body.innerText : ''"),
                timeout=2.0,
            )
            if any(kw in text for kw in ["验证码", "身份验证", "短信已发送"]):
                utils.logger.info(f"[ScanAPI] 验证弹窗定位到iframe: {frame.url}")
                return frame
        except Exception:
            continue
    return page


@router.post("/cookies/scan/verify/send/{session_id}", summary="发送验证码", tags=["扫码登录"])
async def send_verify_code(session_id: str):
    """
    处理抖音身份验证的短信发送。
    弹窗有两种状态：
    1. 验证方式选择页 → 点击"接收短信验证码"选项，短信自动发送
    2. 验证码输入页 → 短信已发送，若需重发则点击"重新发送"
    """
    from fastapi.responses import JSONResponse

    session = _scan_sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    page = session.get("page")
    if not page:
        return JSONResponse({"success": False, "message": "当前无活跃页面"})

    try:
        # 暂停扫码轮询，独占 Playwright 通道（与 submit 相同策略）
        session["_verify_in_progress"] = True
        await asyncio.sleep(0.5)

        # 通过 JS 在 #uc-second-verify 内判断当前弹窗状态
        dialog_state = await page.evaluate('''() => {
            let uc = document.querySelector("#uc-second-verify");
            if (!uc) return { has_dialog: false };
            let text = uc.innerText || "";
            return {
                has_dialog: true,
                has_sms_sent: text.includes("短信已发送") || text.includes("请输入验证码"),
                has_sms_option: text.includes("接收短信验证码"),
                text_preview: text.substring(0, 200)
            };
        }''')
        utils.logger.info(f"[ScanAPI] 弹窗状态: {dialog_state}")

        if not dialog_state.get("has_dialog"):
            session["_verify_in_progress"] = False
            return JSONResponse({"success": False, "message": "未检测到身份验证弹窗"})

        # 场景A: 已在验证码输入页（短信已发送）
        if dialog_state.get("has_sms_sent"):
            # 先截图留证，记录点击前状态
            try:
                before_path = "data/dy_verify_debug/before_resend.png"
                os.makedirs("data/dy_verify_debug", exist_ok=True)
                await page.screenshot(path=before_path)
                utils.logger.info(f"[ScanAPI] 重发前截图: {before_path}")
            except Exception:
                pass

            # 诊断"重新发送"按钮的真实DOM结构，找最内层叶子节点点击
            resend_result = await page.evaluate('''() => {
                let uc = document.querySelector("#uc-second-verify");
                if (!uc) return { clicked: false, reason: "no_dialog" };

                let allEls = uc.querySelectorAll("span, div, a, button, p");
                let candidates = [];
                for (let el of allEls) {
                    let t = (el.textContent || "").trim();
                    // 倒计时进行中
                    if (/^\d+s后重新发送$/.test(t)) {
                        return { clicked: false, reason: "countdown", remaining: t };
                    }
                    if (t === "重新发送") {
                        candidates.push({
                            el: el,
                            tag: el.tagName,
                            cls: el.className.substring(0, 100),
                            children: el.children.length,
                            rect: el.getBoundingClientRect()
                        });
                    }
                }
                if (candidates.length === 0) {
                    return { clicked: false, reason: "btn_not_found" };
                }

                // 优先选 children=0 的叶子节点（最内层才是真正的可点击目标）
                let leaf = candidates.find(c => c.children === 0) || candidates[candidates.length - 1];
                let el = leaf.el;

                // 多种方式尝试触发点击
                el.click();
                el.dispatchEvent(new MouseEvent("mousedown", {bubbles: true, cancelable: true}));
                el.dispatchEvent(new MouseEvent("mouseup", {bubbles: true, cancelable: true}));
                el.dispatchEvent(new PointerEvent("pointerdown", {bubbles: true, cancelable: true}));
                el.dispatchEvent(new PointerEvent("pointerup", {bubbles: true, cancelable: true}));

                // 也向父元素补一次 click（React 事件可能绑在父级）
                if (el.parentElement) {
                    el.parentElement.click();
                }

                return {
                    clicked: true,
                    tag: leaf.tag,
                    cls: leaf.cls,
                    children: leaf.children,
                    all_candidates: candidates.map(c => ({tag: c.tag, cls: c.cls, children: c.children}))
                };
            }''')
            utils.logger.info(f"[ScanAPI] 重发验证码结果: {resend_result}")

            # 点击后等待并截图验证
            if resend_result.get("clicked"):
                await asyncio.sleep(2)
                try:
                    after_path = "data/dy_verify_debug/after_resend.png"
                    await page.screenshot(path=after_path)
                    utils.logger.info(f"[ScanAPI] 重发后截图: {after_path}")
                except Exception:
                    pass

                # 截图后检查页面文字，确认倒计时是否重新开始（真正发送的标志）
                verify_text = await page.evaluate('''() => {
                    let uc = document.querySelector("#uc-second-verify");
                    return uc ? (uc.innerText || "").substring(0, 300) : "";
                }''')
                utils.logger.info(f"[ScanAPI] 重发后页面文字: {verify_text}")

                # 倒计时重新出现说明确实重发成功
                import re
                actually_sent = bool(re.search(r'\d+s后重新发送', verify_text))

                session["_verify_in_progress"] = False
                if actually_sent:
                    return JSONResponse({"success": True, "message": "已重新发送验证码，请查看手机短信"})
                else:
                    return JSONResponse({"success": False, "message": "点击了重发按钮但未检测到倒计时重启，可能未生效"})

            session["_verify_in_progress"] = False
            if resend_result.get("reason") == "countdown":
                remaining = resend_result.get("remaining", "")
                return JSONResponse({"success": False, "message": f"倒计时中（{remaining}），请等待结束后再重发"})
            return JSONResponse({"success": False, "message": "未找到'重新发送'按钮"})

        # 场景B: 还在验证方式选择页，用 JS 在 #uc-second-verify 内点击"接收短信验证码"
        # 遮罩层 #uc-second-verify 拦截 Playwright 的 pointer events，
        # 必须用 JS evaluate 的 .click() 才能绕过（headless Linux 尤其明显）
        sms_result = await page.evaluate('''() => {
            let uc = document.querySelector("#uc-second-verify");
            if (!uc) return { clicked: false, reason: "no_dialog" };
            // 查找包含"接收短信验证码"文字的最小元素并点击
            let candidates = uc.querySelectorAll("div, li, span, a");
            let best = null;
            let bestLen = Infinity;
            for (let el of candidates) {
                let t = (el.textContent || "").trim();
                if (t.includes("接收短信验证码") && t.length < bestLen) {
                    best = el;
                    bestLen = t.length;
                }
            }
            if (best) {
                best.click();
                return { clicked: true, tag: best.tagName, text: best.textContent.trim().substring(0, 30) };
            }
            return { clicked: false, reason: "option_not_found" };
        }''')
        utils.logger.info(f"[ScanAPI] 接收短信验证码点击结果: {sms_result}")

        if sms_result.get("clicked"):
            await asyncio.sleep(1.5)
            session["_verify_in_progress"] = False
            utils.logger.info("[ScanAPI] 已选择短信验证，短信自动发送")
            return JSONResponse({"success": True, "message": "已选择短信验证，验证码已自动发送，请查看手机"})

        session["_verify_in_progress"] = False
        utils.logger.warning(f"[ScanAPI] 未找到验证相关元素: {sms_result}")
        return JSONResponse({"success": False, "message": "未找到'接收短信验证码'选项，请检查页面状态"})
    except Exception as e:
        session["_verify_in_progress"] = False
        return JSONResponse({"success": False, "message": f"操作失败: {e}"})


@router.post("/cookies/scan/verify/submit/{session_id}", summary="提交验证码", tags=["扫码登录"])
async def submit_verify_code(session_id: str, body: dict):
    """
    填入验证码并点击"验证"按钮提交。
    body: { "code": "123456" }
    用单次 JS evaluate 完成填值+点击，避免多次 Playwright 命令与扫码轮询互相阻塞。
    """
    from fastapi.responses import JSONResponse

    session = _scan_sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    code = body.get("code", "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="验证码不能为空")

    page = session.get("page")
    if not page:
        return JSONResponse({"success": False, "message": "当前无活跃页面"})

    try:
        utils.logger.info(f"[ScanAPI] 开始提交验证码（长度={len(code)}）")

        # 暂停扫码轮询，独占 Playwright 通道
        session["_verify_in_progress"] = True
        await asyncio.sleep(0.5)

        # 定位弹窗内的输入框（非登录页的同名input）
        uc_input = await page.evaluate('''() => {
            let inp = document.querySelector("#uc-second-verify input.input-lrnhMm")
                   || document.querySelector("#uc-second-verify input[placeholder*='验证码']");
            return inp ? { found: true, value: inp.value } : { found: false };
        }''')
        utils.logger.info(f"[ScanAPI] 弹窗input状态: {uc_input}")

        if not uc_input.get("found"):
            session["_verify_in_progress"] = False
            return JSONResponse({"success": False, "message": "未找到身份验证弹窗内的输入框"})

        # 聚焦弹窗内的验证码输入框
        await page.evaluate('''() => {
            let inp = document.querySelector("#uc-second-verify input.input-lrnhMm")
                   || document.querySelector("#uc-second-verify input[placeholder*='验证码']");
            if (inp) inp.focus();
        }''')
        await asyncio.sleep(0.1)

        # 用 Ctrl+A 选中已有内容（如验证码填错后的旧值），
        # 再用 insert_text 替换选区——这样 React 能正确感知输入变更
        await page.keyboard.press("Control+a")
        await asyncio.sleep(0.05)
        await page.keyboard.insert_text(code)
        utils.logger.info(f"[ScanAPI] 验证码已插入弹窗input（select+insert_text）")

        # 等 React 处理 input 事件并启用"验证"按钮
        await asyncio.sleep(0.8)

        # 检查填入后值和按钮状态
        post_state = await page.evaluate('''() => {
            let inp = document.querySelector("#uc-second-verify input.input-lrnhMm");
            let btn = document.querySelector("#uc-second-verify .primary-Npo6wt");
            return {
                value: inp ? inp.value : null,
                btn_text: btn ? btn.textContent.trim() : null,
                btn_class: btn ? btn.className.substring(0, 150) : null
            };
        }''')
        utils.logger.info(f"[ScanAPI] 填入后状态: {post_state}")

        # 点击"验证"按钮（<div> 元素，用 JS click 绕过遮罩层）
        clicked = await page.evaluate('''() => {
            let btn = document.querySelector("#uc-second-verify .primary-Npo6wt");
            if (btn) { btn.click(); return true; }
            let container = document.querySelector("#uc-second-verify");
            if (!container) return false;
            let divs = container.querySelectorAll("div");
            for (let d of divs) {
                if (d.textContent.trim() === "验证" && d.children.length === 0) {
                    d.click(); return true;
                }
            }
            return false;
        }''')
        utils.logger.info(f"[ScanAPI] 验证按钮点击: {clicked}")

        await asyncio.sleep(2)

        # 提交后检测弹窗是否仍然存在：
        # 仍在 → 验证码错误，保持 need_verify 让用户重试
        # 消失 → 验证通过，恢复 waiting 让扫码流程继续
        still_verify = await page.evaluate('''() => {
            let uc = document.querySelector("#uc-second-verify");
            if (!uc) return { visible: false };
            let text = (uc.innerText || "").substring(0, 200);
            return {
                visible: true,
                has_error: text.includes("验证码错误") || text.includes("错误") || text.includes("重试"),
                text_preview: text
            };
        }''')
        utils.logger.info(f"[ScanAPI] 提交后弹窗检测: {still_verify}")

        if still_verify.get("visible"):
            # 弹窗仍在，验证码错误或等待中，保持 need_verify 状态
            session["status"] = "need_verify"
            session["_verify_in_progress"] = False
            err_msg = "验证码错误，请重新输入" if still_verify.get("has_error") else "验证码已提交，弹窗仍在，请检查"
            utils.logger.info(f"[ScanAPI] {err_msg}")
            return JSONResponse({"success": False, "message": err_msg})

        # 弹窗消失，验证通过
        session["status"] = "waiting"
        session["_verify_in_progress"] = False
        utils.logger.info("[ScanAPI] 身份验证通过，弹窗已消失")
        return JSONResponse({"success": True, "message": "验证通过！"})
    except Exception as e:
        session["_verify_in_progress"] = False
        utils.logger.error(f"[ScanAPI] 提交验证码异常: {e}")
        return JSONResponse({"success": False, "message": f"提交失败: {e}"})
