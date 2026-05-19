# -*- coding: utf-8 -*-
# 硬编码回退映射
# 当 AI 字段映射失败时，按已知的平台 JSON 结构直接提取指标
# 这些映射基于 MediaCrawler 已有的字段提取逻辑

import re
from typing import Any, Dict, Optional

from tools import utils


def _deep_get(d: Dict, path: str, default=None) -> Any:
    """按点分路径从嵌套 dict 中取值，例如 'statistics.digg_count'"""
    keys = path.split(".")
    current = d
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return default
        if current is None:
            return default
    return current


def _to_int(val: Any) -> Optional[int]:
    """安全转 int，兼容中文数字格式（如 '1.2万'、'53万播放'、'1.5亿'）"""
    if val is None:
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        return int(val)
    try:
        return int(val)
    except (ValueError, TypeError):
        pass
    # 中文数字单位解析：微博等平台的 play_count 可能是 "1.2万" / "1000次播放" 格式
    if isinstance(val, str):
        s = val.strip().rstrip("次播放浏览")
        # 先尝试去掉中文后缀后直接解析为数字（处理 "1000次播放" 这类）
        try:
            return int(float(s))
        except (ValueError, TypeError):
            pass
        _CN_UNITS = {"万": 10_000, "亿": 100_000_000}
        for unit_char, multiplier in _CN_UNITS.items():
            if unit_char in s:
                num_part = s.replace(unit_char, "").strip()
                try:
                    return int(float(num_part) * multiplier)
                except (ValueError, TypeError):
                    pass
    return None


# ── 各平台的字段路径映射 ──
# 格式: {目标字段: [候选JSONPath1, 候选JSONPath2, ...]}
# 按优先级从高到低排列
# 注意：微博 client 返回结构为 {"mblog": {...}}，所以需要 mblog. 前缀

_PLATFORM_FIELD_PATHS: Dict[str, Dict[str, list]] = {
    "dy": {
        "praise_count": ["statistics.digg_count"],
        "reply_count": ["statistics.comment_count"],
        "visit_count": ["statistics.play_count"],
        "share_count": ["statistics.share_count"],
        "author": ["author.nickname", "author_info.nickname"],
        "title": ["desc", "title", "share_info.share_title"],
    },
    "bili": {
        "praise_count": ["View.stat.like", "stat.like"],
        "reply_count": ["View.stat.reply", "stat.reply"],
        "visit_count": ["View.stat.view", "stat.view"],
        "share_count": ["View.stat.share", "stat.share"],
        "author": ["View.owner.name", "owner.name"],
        "title": ["View.title", "title"],
    },
    "ks": {
        "praise_count": ["photo.likeCount", "photo.realLikeCount", "likeCount", "like_count"],
        "reply_count": ["photo.commentCount", "commentCount", "comment_count"],
        "visit_count": ["photo.viewCount", "viewCount", "view_count"],
        "share_count": ["photo.shareCount", "shareCount", "share_count"],
        "author": ["author.name", "photo.userName", "userName"],
        "title": ["photo.caption", "caption"],
    },
    "toutiao": {
        "praise_count": ["digg_count", "like_count"],
        "reply_count": ["comment_count"],
        "visit_count": ["read_count", "play_count", "video_play_count"],
        "share_count": ["share_count", "forward_count"],
        "author": ["source", "media_name", "author.name"],
        "title": ["title", "articleInfo.title"],
    },
    "xhs": {
        "praise_count": ["liked_count", "interact_info.liked_count"],
        "reply_count": ["comment_count", "interact_info.comment_count"],
        "visit_count": ["view_count", "interact_info.view_count"],
        "share_count": ["share_count", "interact_info.share_count"],
        "author": ["user.nickname", "note_user.nickname"],
        "title": ["title", "display_title"],
    },
    "wb": {
        "praise_count": [
            "mblog.attitudes_count", "attitudes_count",
            "mblog.like_count", "like_count",
        ],
        "reply_count": ["mblog.comments_count", "comments_count"],
        "visit_count": [
            # 视频播放量在 page_info.play_count 下，是微博最常见的浏览量来源
            "mblog.page_info.play_count", "page_info.play_count",
            "mblog.reads_count", "reads_count",
            "mblog.play_count", "play_count",
        ],
        "share_count": ["mblog.reposts_count", "reposts_count"],
        "author": ["mblog.user.screen_name", "user.screen_name"],
        # 长微博文章标题
        "title": ["mblog._article_title", "mblog.text"],
    },
}


def _extract_wb_title(raw_json: Dict) -> Optional[str]:
    """
    从微博 JSON 中提取标题。
    优先从 text_raw 中提取，规则：
      1. 有【xxx】→ 取括号内文字作为标题
      2. 无【】→ 截取正文前 25 字左右作为标题
    """
    text = (
        _deep_get(raw_json, "mblog.text_raw")
        or _deep_get(raw_json, "text_raw")
        or _deep_get(raw_json, "mblog.longText.content")
        or _deep_get(raw_json, "longText.content")
        or ""
    )
    if not text:
        # 尝试从 HTML 格式的 text 中去标签提取
        html_text = _deep_get(raw_json, "mblog.text") or _deep_get(raw_json, "text") or ""
        if html_text:
            text = re.sub(r"<.*?>", "", html_text).strip()

    if not text:
        return None

    # 规则 1：提取【xxx】内的内容
    match = re.search(r"【(.+?)】", text)
    if match:
        return match.group(1).strip()

    # 规则 2：截取前 25 字作为标题（按自然断句截取）
    clean = text.strip().replace("\n", " ").replace("\r", "")
    if len(clean) > 25:
        return clean[:25] + "..."
    return clean if clean else None


def fallback_extract(
    platform: str, raw_json: Dict
) -> Dict[str, Optional[int]]:
    """
    按硬编码路径从原始 JSON 中提取指标。
    对于未知平台或取不到的字段，返回 None。
    微博额外提取 title 字段（从正文生成）。
    """
    field_paths = _PLATFORM_FIELD_PATHS.get(platform, {})
    result: Dict[str, Any] = {
        "praise_count": None,
        "reply_count": None,
        "visit_count": None,
        "share_count": None,
        "author": None,
        "title": None,
    }

    for target_field, candidate_paths in field_paths.items():
        for path in candidate_paths:
            val = _deep_get(raw_json, path)
            if target_field in ("author", "title"):
                if val and isinstance(val, str):
                    result[target_field] = val
                    break
            else:
                int_val = _to_int(val)
                if int_val is not None:
                    result[target_field] = int_val
                    break

    # 微博专用：从正文提取标题（微博没有独立 title 字段，需要特殊处理）
    if platform == "wb":
        result["title"] = _extract_wb_title(raw_json)

    utils.logger.debug(f"[fallback_extract] platform={platform} result={result}")
    return result
