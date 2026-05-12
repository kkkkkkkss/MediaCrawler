# -*- coding: utf-8 -*-
# 评论统一存储模块
# 将各平台原始评论数据转换为 bigscreen_content_comments 统一格式
# 通过 external_db 写入外部业务库

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from database.external_db import external_db
from tools import utils


async def store_comments_to_external_db(
    platform: str,
    content_id: str,
    content_url: str,
    raw_comments: List[Dict],
):
    """
    将各平台评论统一转换后写入 bigscreen_content_comments 表。

    Args:
        platform: 平台代码 (dy/bili/ks/toutiao/xhs/wb)
        content_id: 平台侧作品 ID
        content_url: 作品原始 URL
        raw_comments: 平台原始评论列表（各平台格式不同）
    """
    if not raw_comments:
        return

    converter = _PLATFORM_CONVERTERS.get(platform, _default_converter)
    comments = []
    for raw in raw_comments:
        try:
            comment = converter(raw, platform, content_id, content_url)
            if comment:
                comments.append(comment)
        except Exception as e:
            utils.logger.warning(
                f"[url_check_comment_store] 评论转换失败: {e}"
            )

    if comments:
        await external_db.batch_insert_comments(comments)
        utils.logger.info(
            f"[url_check_comment_store] 写入 {len(comments)} 条 {platform} 评论"
        )


# ── 各平台评论格式转换器 ──

def _convert_douyin_comment(
    raw: Dict, platform: str, content_id: str, content_url: str
) -> Optional[Dict]:
    """抖音评论格式转换"""
    user_info = raw.get("user", {})
    comment_time = raw.get("create_time")
    dt = datetime.fromtimestamp(comment_time) if comment_time else None

    return {
        "source_platform": "douyin",
        "content_url": content_url,
        "content_id": content_id,
        "comment_id": str(raw.get("cid", "")),
        "parent_comment_id": str(raw.get("reply_id", "")) if raw.get("reply_id") and str(raw.get("reply_id")) != "0" else None,
        "author_id": user_info.get("uid"),
        "author_name": user_info.get("nickname"),
        "comment_text": raw.get("text"),
        "comment_like_count": raw.get("digg_count"),
        "comment_reply_count": raw.get("reply_comment_total", 0),
        "comment_time": dt,
        "raw_json": raw,
    }


def _convert_bilibili_comment(
    raw: Dict, platform: str, content_id: str, content_url: str
) -> Optional[Dict]:
    """B站评论格式转换"""
    member = raw.get("member", {})
    ctime = raw.get("ctime")
    dt = datetime.fromtimestamp(ctime) if ctime else None
    parent_id = raw.get("parent") if raw.get("parent") and raw.get("parent") != 0 else None

    return {
        "source_platform": "bilibili",
        "content_url": content_url,
        "content_id": content_id,
        "comment_id": str(raw.get("rpid", "")),
        "parent_comment_id": str(parent_id) if parent_id else None,
        "author_id": str(member.get("mid", "")),
        "author_name": member.get("uname"),
        "comment_text": raw.get("content", {}).get("message"),
        "comment_like_count": raw.get("like"),
        "comment_reply_count": raw.get("rcount", 0),
        "comment_time": dt,
        "raw_json": raw,
    }


def _convert_kuaishou_comment(
    raw: Dict, platform: str, content_id: str, content_url: str
) -> Optional[Dict]:
    """快手评论格式转换"""
    author_info = raw.get("authorInfo", raw.get("author", {}))
    ts = raw.get("timestamp") or raw.get("time")
    dt = datetime.fromtimestamp(ts / 1000) if ts and ts > 1e12 else (datetime.fromtimestamp(ts) if ts else None)

    return {
        "source_platform": "kuaishou",
        "content_url": content_url,
        "content_id": content_id,
        "comment_id": str(raw.get("commentId", "")),
        "parent_comment_id": str(raw.get("replyTo", "")) if raw.get("replyTo") else None,
        "author_id": author_info.get("id"),
        "author_name": author_info.get("name"),
        "comment_text": raw.get("content"),
        "comment_like_count": raw.get("likedCount"),
        "comment_reply_count": raw.get("subCommentCount", 0),
        "comment_time": dt,
        "raw_json": raw,
    }


def _convert_toutiao_comment(
    raw: Dict, platform: str, content_id: str, content_url: str
) -> Optional[Dict]:
    """头条评论格式转换"""
    user = raw.get("user", {})
    create_time = raw.get("create_time")
    dt = datetime.fromtimestamp(create_time) if create_time else None

    return {
        "source_platform": "toutiao",
        "content_url": content_url,
        "content_id": content_id,
        "comment_id": str(raw.get("id", "")),
        "parent_comment_id": str(raw.get("reply_to_comment_id", "")) if raw.get("reply_to_comment_id") else None,
        "author_id": user.get("user_id"),
        "author_name": user.get("name") or user.get("screen_name"),
        "comment_text": raw.get("text"),
        "comment_like_count": raw.get("digg_count"),
        "comment_reply_count": raw.get("reply_count", 0),
        "comment_time": dt,
        "raw_json": raw,
    }


def _convert_weibo_comment(
    raw: Dict, platform: str, content_id: str, content_url: str
) -> Optional[Dict]:
    """微博评论格式转换"""
    user = raw.get("user", {})
    created_at = raw.get("created_at")
    dt = None
    if created_at:
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(created_at)
        except Exception:
            pass
    parent_id = raw.get("rootid") if raw.get("rootid") and str(raw.get("rootid")) != str(raw.get("id")) else None

    return {
        "source_platform": "weibo",
        "content_url": content_url,
        "content_id": content_id,
        "comment_id": str(raw.get("id", "")),
        "parent_comment_id": str(parent_id) if parent_id else None,
        "author_id": str(user.get("id", "")),
        "author_name": user.get("screen_name"),
        "comment_text": raw.get("text"),
        "comment_like_count": raw.get("like_count", raw.get("like_counts", 0)),
        "comment_reply_count": raw.get("total_number", 0),
        "comment_time": dt,
        "raw_json": raw,
    }


def _default_converter(
    raw: Dict, platform: str, content_id: str, content_url: str
) -> Optional[Dict]:
    """通用回退转换器"""
    return {
        "source_platform": platform,
        "content_url": content_url,
        "content_id": content_id,
        "comment_id": str(raw.get("comment_id", raw.get("id", ""))),
        "parent_comment_id": raw.get("parent_comment_id"),
        "author_id": raw.get("user_id"),
        "author_name": raw.get("nickname"),
        "comment_text": raw.get("content") or raw.get("text"),
        "comment_like_count": raw.get("like_count"),
        "comment_reply_count": raw.get("sub_comment_count", 0),
        "comment_time": None,
        "raw_json": raw,
    }


_PLATFORM_CONVERTERS = {
    "dy": _convert_douyin_comment,
    "bili": _convert_bilibili_comment,
    "ks": _convert_kuaishou_comment,
    "toutiao": _convert_toutiao_comment,
    "wb": _convert_weibo_comment,
}
