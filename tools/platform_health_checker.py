# -*- coding: utf-8 -*-
# 平台健康检测器
# 三层兜底策略中的第二层：用基准帖子验证平台接口是否正常
# 全局缓存检测结果，避免重复请求

import os
import time
import pathlib
from datetime import datetime
from typing import Any, Callable, Coroutine, Dict, Optional

import config
from tools import utils
from tools.fallback_field_map import fallback_extract


# 全局缓存：{platform: {"healthy": bool, "checked_at": float}}
_benchmark_cache: Dict[str, Dict] = {}


def has_content_fields(result: Dict) -> bool:
    """第一层兜底：检查硬编码提取的 author/title 是否非空"""
    author = result.get("author")
    title = result.get("title")
    return bool(author and str(author).strip()) or bool(title and str(title).strip())


def _is_cache_valid(platform: str) -> bool:
    """检查缓存是否在有效期内"""
    entry = _benchmark_cache.get(platform)
    if not entry:
        return False
    ttl = getattr(config, "BENCHMARK_CACHE_TTL_SECONDS", 1800)
    return (time.time() - entry["checked_at"]) < ttl


def get_cached_health(platform: str) -> Optional[bool]:
    """获取缓存中的平台健康状态，缓存失效返回 None"""
    if _is_cache_valid(platform):
        return _benchmark_cache[platform]["healthy"]
    return None


async def check_benchmark(
    platform: str,
    client: Any,
    fetch_detail_func: Callable,
) -> bool:
    """
    用基准帖子检测平台接口是否正常。

    Args:
        platform: 平台代码
        client: 已创建的平台 API 客户端
        fetch_detail_func: 获取帖子详情的异步函数，签名: (platform, client, content_id, url, row) -> dict|None

    Returns:
        True=平台正常（基准帖子能取到数据），False=平台异常
    """
    cached = get_cached_health(platform)
    if cached is not None:
        utils.logger.info(
            f"[HealthChecker] 平台 [{platform}] 基准检测命中缓存: healthy={cached}"
        )
        return cached

    benchmarks = getattr(config, "PLATFORM_BENCHMARK_POSTS", {})
    benchmark = benchmarks.get(platform)
    if not benchmark:
        utils.logger.warning(f"[HealthChecker] 平台 [{platform}] 无基准帖子配置，跳过")
        _update_cache(platform, True)
        return True

    content_id = benchmark["content_id"]
    url = benchmark["url"]

    try:
        utils.logger.info(
            f"[HealthChecker] 平台 [{platform}] 开始基准帖子检测: {url}"
        )
        row = {"id": 0, "url": url, "_content_id": content_id}
        raw_json = await fetch_detail_func(platform, client, content_id, url, row=row)

        if raw_json is None:
            utils.logger.warning(
                f"[HealthChecker] 平台 [{platform}] 基准帖子接口返回空"
            )
            _update_cache(platform, False)
            return False

        # 用硬编码提取基准帖子的指标
        benchmark_metrics = fallback_extract(platform, raw_json)
        praise = benchmark_metrics.get("praise_count")
        reply = benchmark_metrics.get("reply_count")
        visit = benchmark_metrics.get("visit_count")
        author = benchmark_metrics.get("author")

        has_metrics = any(
            v is not None and v != 0 for v in [praise, reply, visit]
        )
        has_author = bool(author and str(author).strip())

        healthy = has_metrics or has_author
        utils.logger.info(
            f"[HealthChecker] 平台 [{platform}] 基准帖子检测结果: "
            f"healthy={healthy} metrics={benchmark_metrics}"
        )
        _update_cache(platform, healthy)
        return healthy

    except Exception as e:
        utils.logger.error(
            f"[HealthChecker] 平台 [{platform}] 基准帖子检测异常: {e}"
        )
        _update_cache(platform, False)
        return False


def _update_cache(platform: str, healthy: bool):
    """更新缓存"""
    _benchmark_cache[platform] = {
        "healthy": healthy,
        "checked_at": time.time(),
    }


def generate_diagnostic_report(
    platform: str,
    raw_json: Dict,
    hardcode_result: Dict,
    ai_result: Optional[Dict],
    ai_diagnosis: str = "",
) -> str:
    """
    生成诊断报告文件，返回文件路径。
    当第三层 AI 检测触发时调用。
    """
    report_dir = pathlib.Path("data/diagnostics")
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{platform}_{timestamp}.md"
    filepath = report_dir / filename

    lines = [
        f"# 平台接口诊断报告 - {platform.upper()}",
        f"",
        f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**平台**: {platform}",
        f"",
        f"## 硬编码提取结果（全部为空/零，触发诊断）",
        f"```",
    ]
    for k, v in hardcode_result.items():
        lines.append(f"  {k}: {v}")
    lines.extend([
        f"```",
        f"",
        f"## AI 提取结果",
        f"```",
    ])
    if ai_result:
        for k, v in ai_result.items():
            lines.append(f"  {k}: {v}")
    else:
        lines.append("  AI 提取失败")
    lines.extend([
        f"```",
        f"",
    ])

    if ai_diagnosis:
        lines.extend([
            f"## AI 诊断摘要",
            f"",
            ai_diagnosis,
            f"",
        ])

    # 展示原始 JSON 前 2000 字符供参考
    import json
    json_preview = json.dumps(raw_json, ensure_ascii=False, indent=2)
    if len(json_preview) > 2000:
        json_preview = json_preview[:2000] + "\n... (截断)"
    lines.extend([
        f"## 原始 JSON 片段",
        f"```json",
        json_preview,
        f"```",
    ])

    content = "\n".join(lines)
    filepath.write_text(content, encoding="utf-8")
    utils.logger.info(f"[HealthChecker] 诊断报告已生成: {filepath}")
    return str(filepath)
