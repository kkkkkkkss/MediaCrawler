# -*- coding: utf-8 -*-
# 回调核心逻辑
# 任务完成后自动将结果 POST 到指定回调地址
# 支持全局默认地址 + 任务级覆盖，失败自动重试

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

import config
from tools import utils
from tools.url_check_status import STATUS_VALID, validity_label


def _format_single_result_for_callback(single_result: Dict[str, Any]) -> Dict[str, Any]:
    """单条回调补齐批量 JSON 的四态字段，避免下游因两种任务格式不一致而误判。"""
    item = dict(single_result)
    status_code = item.get("is_valid")
    item["is_valid_code"] = status_code
    item["is_valid"] = status_code == STATUS_VALID
    item["validity_label"] = item.get("validity_label") or validity_label(status_code)
    item["status_reason"] = item.get("status_reason", "")
    return item


async def send_callback(
    callback_url: str,
    event: str,
    task_id: str,
    status: str,
    data: Any,
) -> bool:
    """
    向回调地址发送 POST 请求。

    Args:
        callback_url: 回调地址
        event: 事件类型 (task_completed / comments_ready)
        task_id: 任务 ID
        status: 任务状态
        data: 完整结果数据

    Returns:
        True=发送成功, False=发送失败
    """
    payload = {
        "event": event,
        "task_id": task_id,
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }

    max_retries = getattr(config, "CALLBACK_MAX_RETRIES", 3)
    retry_intervals = getattr(config, "CALLBACK_RETRY_INTERVALS", [5, 15, 30])

    for attempt in range(max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=30, verify=False) as client:
                resp = await client.post(
                    callback_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                if 200 <= resp.status_code < 300:
                    utils.logger.info(
                        f"[Callback] 回调成功: {callback_url} "
                        f"event={event} task={task_id} status={resp.status_code}"
                    )
                    return True
                else:
                    utils.logger.warning(
                        f"[Callback] 回调返回非200: {resp.status_code} "
                        f"body={resp.text[:200]}"
                    )
        except Exception as e:
            utils.logger.warning(
                f"[Callback] 回调失败(attempt={attempt+1}/{max_retries+1}): {e}"
            )

        # 重试间隔
        if attempt < max_retries:
            wait = retry_intervals[attempt] if attempt < len(retry_intervals) else retry_intervals[-1]
            utils.logger.info(f"[Callback] {wait}秒后重试...")
            await asyncio.sleep(wait)

    utils.logger.error(
        f"[Callback] 回调最终失败: {callback_url} event={event} task={task_id}"
    )
    return False


def resolve_callback_url(task_callback_url: Optional[str]) -> Optional[str]:
    """
    确定实际使用的回调地址。
    优先级：任务级 > 全局配置。
    返回 None 表示不需要回调。
    """
    if task_callback_url and task_callback_url.strip():
        return task_callback_url.strip()

    global_enabled = getattr(config, "CALLBACK_ENABLED", False)
    global_url = getattr(config, "CALLBACK_URL", "")
    if global_enabled and global_url and global_url.strip():
        return global_url.strip()

    return None


async def trigger_task_callback(info) -> None:
    """
    任务完成后触发回调（在 worker_loop 中调用）。
    分两次 POST：主结果 + 评论（如果有）。
    """
    callback_url = resolve_callback_url(info.callback_url)
    if not callback_url:
        return

    utils.logger.info(
        f"[Callback] 任务 {info.task_id} 完成，开始回调: {callback_url}"
    )

    # 第一次回调：主结果
    result_data = None
    if info.result_data:
        from store.url_check_json_store import results_to_json_data
        result_data = results_to_json_data(info.result_data, info.task_id)
    elif info.single_result:
        result_data = {
            "task_id": info.task_id,
            "total": 1,
            "completed_at": datetime.now().isoformat(timespec="seconds"),
            "results": [_format_single_result_for_callback(info.single_result)],
        }

    if result_data:
        await send_callback(
            callback_url,
            event="task_completed",
            task_id=info.task_id,
            status=info.status,
            data=result_data,
        )
        info.add_log(f"主结果已回调至: {callback_url}")

    # 第二次回调：评论（分开发送）
    if info.comments_data:
        from store.url_check_comment_export import comments_to_json_data
        comments_payload = comments_to_json_data(info.comments_data, info.task_id)
        await send_callback(
            callback_url,
            event="comments_ready",
            task_id=info.task_id,
            status=info.status,
            data=comments_payload,
        )
        info.add_log(f"评论数据已回调至: {callback_url}")
