# -*- coding: utf-8 -*-
# url_check 模式 JSON 格式结果输出
# 将处理结果序列化为标准化 JSON 结构，用于 API 返回和文件下载

import json
import pathlib
from datetime import datetime
from typing import Any, Dict, List, Optional

from tools import utils

_PLATFORM_NAMES = {
    "dy": "抖音", "ks": "快手", "bili": "B站",
    "toutiao": "今日头条", "xhs": "小红书", "wb": "微博",
    "unknown": "未知",
}

_PLATFORM_DEFAULT_TYPE = {
    "dy": "视频", "ks": "视频", "bili": "视频",
    "toutiao": "视频", "xhs": "笔记", "wb": "微博",
}


def results_to_json_data(
    results: List[Dict],
    task_id: str = "",
) -> Dict[str, Any]:
    """
    将 _all_results 转换为标准化 JSON 结构。

    返回格式:
    {
        "task_id": "batch-abc123",
        "total": 10,
        "completed_at": "2026-05-12T15:30:00",
        "results": [ ... ]
    }
    """
    from store.url_check_excel_store import (
        _detect_content_type,
        _extract_author,
        _extract_title,
    )

    items = []
    for result in results:
        platform = result.get("_platform", "unknown")
        metrics = result.get("_metrics", {})
        raw_json = result.get("_raw_json")
        is_valid = result.get("_is_valid", 0)

        items.append({
            "id": result.get("id", 0),
            "url": result.get("url", ""),
            "platform": platform,
            "platform_name": _PLATFORM_NAMES.get(platform, "未知"),
            "content_type": _detect_content_type(platform, raw_json),
            "is_valid": is_valid == 1,
            "author": _extract_author(platform, raw_json, metrics),
            "title": _extract_title(platform, raw_json, metrics, result.get("_title", "")),
            "praise_count": metrics.get("praise_count"),
            "reply_count": metrics.get("reply_count"),
            "visit_count": metrics.get("visit_count"),
            "share_count": metrics.get("share_count"),
        })

    return {
        "task_id": task_id,
        "total": len(items),
        "completed_at": datetime.now().isoformat(timespec="seconds"),
        "results": items,
    }


def generate_json_file(
    results: List[Dict],
    task_id: str = "",
    output_path: Optional[str] = None,
) -> str:
    """
    将结果输出为 .json 文件。

    Returns:
        生成的 JSON 文件路径
    """
    if not output_path:
        base_dir = pathlib.Path("data/url_check/json")
        base_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(base_dir / f"url_check_{timestamp}.json")
    else:
        pathlib.Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    data = results_to_json_data(results, task_id)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    utils.logger.info(f"[url_check_json] JSON 结果已保存: {output_path}")
    return output_path
