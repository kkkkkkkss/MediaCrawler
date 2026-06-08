# -*- coding: utf-8 -*-
# 外部业务库连接模块
# 独立于 MediaCrawler 自身数据库，用于读写 db_sdga_report.bigscreen_data_test 等业务表

import os
import json
import asyncio
from typing import Any, Dict, List, Optional
from datetime import datetime

import aiomysql
from tools import utils


class ExternalDB:
    """
    外部业务库（db_sdga_report）的异步连接管理器。
    与 MediaCrawler 自身的 database/db_session.py 完全独立，
    避免两套库连接互相干扰。
    """

    def __init__(self):
        self._pool: Optional[aiomysql.Pool] = None

    # ── 连接信息从 .env 读取，前缀 EXT_ 区分 ──
    @staticmethod
    def _cfg() -> Dict[str, Any]:
        return {
            "host": os.getenv("EXT_MYSQL_HOST", "127.0.0.1"),
            "port": int(os.getenv("EXT_MYSQL_PORT", 3306)),
            "user": os.getenv("EXT_MYSQL_USER", "root"),
            "password": os.getenv("EXT_MYSQL_PWD", ""),
            "db": os.getenv("EXT_MYSQL_DB", "db_sdga_report"),
            "charset": "utf8mb4",
            "autocommit": True,
        }

    async def ensure_pool(self):
        """确保连接池已创建（惰性初始化）"""
        if self._pool is None or self._pool.closed:
            cfg = self._cfg()
            utils.logger.info(
                f"[ExternalDB] 正在连接外部库 {cfg['host']}:{cfg['port']}/{cfg['db']}"
            )
            self._pool = await aiomysql.create_pool(
                minsize=1, maxsize=5, **cfg
            )

    async def close(self):
        if self._pool and not self._pool.closed:
            self._pool.close()
            await self._pool.wait_closed()
            utils.logger.info("[ExternalDB] 外部库连接池已关闭")

    # ────────────────── 读取待处理 URL ──────────────────

    async def fetch_pending_urls(
        self,
        batch_size: int = 50,
        mode: str = "both",
    ) -> List[Dict[str, Any]]:
        """
        从 bigscreen_data_test 读取待处理的行。
        mode:
          - validity : 只检测有效性（is_valid IS NULL 或 0）
          - metrics  : 只补充指标
          - both     : 同时做
        返回 [{id, url, ...}, ...]
        """
        await self.ensure_pool()
        sql = (
            "SELECT id, url, is_valid, praise_count, reply_count, "
            "visit_count, share_count, forward_count, follow_state "
            "FROM bigscreen_data_test "
            "WHERE url IS NOT NULL AND url != '' "
            "AND (is_valid IS NULL OR is_valid = 0) "
            "ORDER BY id ASC "
            "LIMIT %s"
        )
        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, (batch_size,))
                rows = await cur.fetchall()
        utils.logger.info(f"[ExternalDB] 读取到 {len(rows)} 条待处理 URL")
        return rows

    # ────────────────── 写回指标 ──────────────────

    async def update_metrics(
        self,
        row_id: int,
        is_valid: Optional[int] = None,
        praise_count: Optional[int] = None,
        reply_count: Optional[int] = None,
        visit_count: Optional[int] = None,
        share_count: Optional[int] = None,
        forward_count: Optional[str] = None,
        clear_metrics: bool = False,
    ):
        """
        按 id 更新 bigscreen_data_test 表中的有效性和指标字段。
        is_valid: 1=有效, 2=无效, 3=不支持, 4=检测异常/待复核
        """
        await self.ensure_pool()
        set_parts = []
        params: list = []

        if is_valid is not None:
            set_parts.append("is_valid = %s")
            params.append(is_valid)

        if clear_metrics:
            # None 平时表示“不更新”；只有 clear_metrics=True 才显式写 NULL，
            # 用于不支持/检测异常场景清掉旧互动量，避免审核误读历史数据。
            for col in ("praise_count", "reply_count", "visit_count", "share_count", "forward_count"):
                set_parts.append(f"{col} = %s")
                params.append(None)
        else:
            if praise_count is not None:
                set_parts.append("praise_count = %s")
                params.append(praise_count)
            if reply_count is not None:
                set_parts.append("reply_count = %s")
                params.append(reply_count)
            if visit_count is not None:
                set_parts.append("visit_count = %s")
                params.append(visit_count)
            if share_count is not None:
                set_parts.append("share_count = %s")
                params.append(share_count)
            if forward_count is not None:
                set_parts.append("forward_count = %s")
                params.append(forward_count)

        if not set_parts:
            utils.logger.debug(f"[ExternalDB] id={row_id} 无字段需要更新")
            return

        params.append(row_id)
        sql = f"UPDATE bigscreen_data_test SET {', '.join(set_parts)} WHERE id = %s"

        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
        utils.logger.info(
            f"[ExternalDB] 已更新 id={row_id} is_valid={is_valid} clear_metrics={clear_metrics}"
        )

    # ────────────────── 写入评论 ──────────────────

    async def insert_comment(self, comment: Dict[str, Any]):
        """
        向 bigscreen_content_comments 表写入一条评论，
        使用 ON DUPLICATE KEY UPDATE 实现去重（重跑安全）。
        """
        await self.ensure_pool()
        sql = """
            INSERT INTO bigscreen_content_comments
                (source_platform, content_url, content_id, comment_id,
                 parent_comment_id, author_id, author_name, comment_text,
                 comment_like_count, comment_reply_count, comment_time,
                 crawl_time, raw_json)
            VALUES
                (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                comment_text = VALUES(comment_text),
                comment_like_count = VALUES(comment_like_count),
                comment_reply_count = VALUES(comment_reply_count),
                crawl_time = VALUES(crawl_time)
        """
        raw_json_str = json.dumps(comment.get("raw_json"), ensure_ascii=False) if comment.get("raw_json") else None
        params = (
            comment.get("source_platform"),
            comment.get("content_url"),
            comment.get("content_id"),
            comment.get("comment_id"),
            comment.get("parent_comment_id"),
            comment.get("author_id"),
            comment.get("author_name"),
            comment.get("comment_text"),
            comment.get("comment_like_count"),
            comment.get("comment_reply_count"),
            comment.get("comment_time"),
            datetime.now(),
            raw_json_str,
        )
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)

    async def batch_insert_comments(self, comments: List[Dict[str, Any]]):
        """批量写入评论"""
        for c in comments:
            try:
                await self.insert_comment(c)
            except Exception as e:
                utils.logger.error(
                    f"[ExternalDB] 评论写入失败 comment_id={c.get('comment_id')}: {e}"
                )


# 全局单例，供各模块 import 使用
external_db = ExternalDB()
