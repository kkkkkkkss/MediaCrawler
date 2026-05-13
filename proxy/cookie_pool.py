# Cookie 池管理模块（重构版）
# 支持随机选取、分级失败判定、实时失效移除、文件/DB 双模式。
#
# 失败分级策略：
#   FATAL  — Cookie 本身失效（登录过期/被封），1~2 次直接标记失效
#   BUSINESS — 网络超时/链接不存在等业务失败，不计入 Cookie 失败，切换下一个
#   PARSE  — 解析失败（能拿到接口但提取不到指标），Cookie 正常，不做任何处理

import json
import os
import random
import time
from enum import Enum
from typing import Any, Dict, List, Optional

import config
from tools import utils


class FailureLevel(str, Enum):
    """失败等级分类"""
    FATAL = "fatal"         # Cookie 致命失效（登录过期、账号封禁）
    BUSINESS = "business"   # 业务失败（网络超时、链接不存在）
    PARSE = "parse"         # 解析失败（接口正常但字段提取失败）


class CookieEntry:
    """单条 Cookie 记录"""

    def __init__(self, cookie_id: str, cookie: str, note: str = "", platform: str = ""):
        self.id = cookie_id
        self.cookie = cookie
        self.note = note
        self.platform = platform
        self.valid = True
        self.fatal_count = 0    # 致命失败累计
        self.last_used: float = 0
        self.created_at: str = ""

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "cookie": self.cookie,
            "note": self.note,
            "valid": self.valid,
            "fatal_count": self.fatal_count,
        }


class CookiePool:
    """
    多平台 Cookie 池（重构版）。

    核心改进：
    - 随机选取：每次从有效池中随机获取，避免固定顺序导致单个 Cookie 高频使用
    - 分级失败判定：区分 FATAL/BUSINESS/PARSE 三种失败类型
    - 实时移除：FATAL 失效的 Cookie 立刻从可用列表移除
    - 切换保证：切换时绝不返回刚刚失败的同一个 Cookie
    """

    def __init__(self):
        self._pool: Dict[str, List[CookieEntry]] = {}
        self._last_used_id: Dict[str, str] = {}  # 记录每个平台上次使用的 Cookie ID

    async def load(self):
        """根据配置从文件或数据库加载 Cookie（每次加载前清空内存池，确保与存储同步）"""
        self._pool = {}
        self._last_used_id = {}

        source = getattr(config, "COOKIE_POOL_SOURCE", "file")
        if source == "file":
            await self._load_from_file()
        elif source == "db":
            await self._load_from_db()
        else:
            utils.logger.warning(f"[CookiePool] 未知来源 {source}，跳过加载")

        total = sum(len(v) for v in self._pool.values())
        platforms = list(self._pool.keys())
        utils.logger.info(
            f"[CookiePool] 加载完成: {total} 条Cookie, 平台={platforms}"
        )

    async def _load_from_file(self):
        """从本地 JSON 文件加载（包含 valid/fatal_count 状态恢复）"""
        path = getattr(config, "COOKIE_POOL_FILE", "config/cookie_pool.json")
        if not os.path.exists(path):
            utils.logger.warning(f"[CookiePool] Cookie文件不存在: {path}")
            return

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        for platform, entries in data.items():
            cookie_list = []
            for entry in entries:
                ce = CookieEntry(
                    cookie_id=entry.get("id", f"{platform}_auto"),
                    cookie=entry.get("cookie", ""),
                    note=entry.get("note", ""),
                    platform=platform,
                )
                ce.created_at = entry.get("created_at", "")
                # 恢复持久化的状态字段
                ce.valid = entry.get("valid", True)
                ce.fatal_count = entry.get("fatal_count", 0)
                if ce.cookie:
                    cookie_list.append(ce)
            if cookie_list:
                self._pool[platform] = cookie_list

    async def _load_from_db(self):
        """从外部 MySQL 的 cookie_pool 表加载（包含 fatal_count 状态）"""
        try:
            from database.external_db import external_db
            await external_db.ensure_pool()

            sql = (
                "SELECT platform, cookie_id, cookie_str, note, fatal_count "
                "FROM cookie_pool WHERE is_valid = 1"
            )
            async with external_db._pool.acquire() as conn:
                import aiomysql
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute(sql)
                    rows = await cur.fetchall()

            for row in rows:
                platform = row["platform"]
                ce = CookieEntry(
                    cookie_id=row.get("cookie_id", ""),
                    cookie=row.get("cookie_str", ""),
                    note=row.get("note", ""),
                    platform=platform,
                )
                ce.fatal_count = row.get("fatal_count", 0) or 0
                if ce.cookie:
                    self._pool.setdefault(platform, []).append(ce)
            utils.logger.info(f"[CookiePool] 从数据库加载 {len(rows)} 条Cookie")
        except Exception as e:
            utils.logger.error(f"[CookiePool] 从数据库加载失败: {e}")

    # ────────────────── 获取 Cookie ──────────────────

    def get_cookie(self, platform: str) -> Optional[str]:
        """
        纯随机获取指定平台的一个有效 Cookie。
        只要 valid=True 就参与随机（含 fatal_count>0 但未达阈值的）。
        """
        valid_entries = [e for e in self._pool.get(platform, []) if e.valid]
        if not valid_entries:
            utils.logger.warning(f"[CookiePool] 平台 [{platform}] 无可用Cookie")
            return None

        # last_id = self._last_used_id.get(platform)

        # # 如果有多个，尽量避免选中上次用的那个
        # if len(valid_entries) > 1 and last_id:
        #     candidates = [e for e in valid_entries if e.id != last_id]
        #     if candidates:
        #         chosen = random.choice(candidates)
        #     else:
        #         chosen = random.choice(valid_entries)
        # else:
        #     chosen = random.choice(valid_entries)
        chosen = random.choice(valid_entries)
        chosen.last_used = time.time()
        self._last_used_id[platform] = chosen.id
        utils.logger.info(
            f"[CookiePool] 平台 [{platform}] 选中Cookie: {chosen.id} ({chosen.note})"
        )
        return chosen.cookie

    def get_another_cookie(self, platform: str, exclude_id: Optional[str] = None) -> Optional[str]:
        """
        获取一个不同于 exclude_id 的有效 Cookie（用于切换重试）。
        """
        valid_entries = [e for e in self._pool.get(platform, []) if e.valid]
        if not valid_entries:
            return None

        if exclude_id:
            candidates = [e for e in valid_entries if e.id != exclude_id]
        else:
            candidates = valid_entries

        if not candidates:
            # 所有有效的都是被排除的那个，只能返回它
            candidates = valid_entries

        chosen = random.choice(candidates)
        chosen.last_used = time.time()
        self._last_used_id[platform] = chosen.id
        utils.logger.info(
            f"[CookiePool] 平台 [{platform}] 切换到Cookie: {chosen.id} ({chosen.note})"
        )
        return chosen.cookie

    def get_current_id(self, platform: str) -> Optional[str]:
        """获取上次使用的 Cookie ID"""
        return self._last_used_id.get(platform)

    # ────────────────── 失败处理 ──────────────────

    def report_failure(self, platform: str, level: FailureLevel = FailureLevel.FATAL) -> bool:
        """
        报告当前 Cookie 的一次失败。

        分级处理：
        - FATAL: 累计计数，达阈值标记失效并移除
        - BUSINESS: 不计数，直接建议切换
        - PARSE: 不做处理，Cookie 正常

        返回 True 表示还有可用 Cookie 可切换，False 表示全部失效。
        """
        if level == FailureLevel.PARSE:
            return True

        current_id = self._last_used_id.get(platform)
        if not current_id:
            return False

        entry = self._find_entry(platform, current_id)
        if not entry:
            return False

        if level == FailureLevel.FATAL:
            entry.fatal_count += 1
            max_fatal = getattr(config, "COOKIE_MAX_FAILURES", 2)
            if entry.fatal_count >= max_fatal:
                entry.valid = False
                utils.logger.warning(
                    f"[CookiePool] Cookie 致命失败 {entry.fatal_count} 次，"
                    f"标记失效: platform={platform} id={entry.id} ({entry.note})"
                )
            else:
                utils.logger.info(
                    f"[CookiePool] Cookie 致命失败 {entry.fatal_count}/{max_fatal}: "
                    f"platform={platform} id={entry.id}"
                )
            # 每次 FATAL 都持久化，确保 fatal_count/valid 状态不丢失
            self._persist_all()

        # 检查是否还有可用 Cookie
        valid_entries = [e for e in self._pool.get(platform, []) if e.valid]
        return len(valid_entries) > 0

    def mark_invalid(self, platform: str, cookie_id: str):
        """直接标记指定 Cookie 为失效"""
        entry = self._find_entry(platform, cookie_id)
        if entry:
            entry.valid = False
            utils.logger.warning(
                f"[CookiePool] 手动标记失效: platform={platform} id={cookie_id}"
            )
            self._persist_invalid(platform, cookie_id)

    # ────────────────── Cookie 管理（增删查） ──────────────────

    def add_cookie(self, platform: str, cookie_str: str, note: str = "", cookie_id: str = "") -> str:
        """添加一个新 Cookie 到池中（仅更新内存），返回生成的 ID。DB写入由调用方负责。"""
        if not cookie_id:
            cookie_id = self._generate_id(platform)
        ce = CookieEntry(
            cookie_id=cookie_id,
            cookie=cookie_str,
            note=note,
            platform=platform,
        )
        ce.created_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self._pool.setdefault(platform, []).append(ce)
        # file模式下持久化到JSON；db模式下由调用方显式写入DB（因为需要INSERT而非UPDATE）
        source = getattr(config, "COOKIE_POOL_SOURCE", "file")
        if source == "file":
            self._persist_all()
        utils.logger.info(f"[CookiePool] 新增Cookie: platform={platform} id={cookie_id}")
        return cookie_id

    def remove_cookie(self, platform: str, cookie_id: str) -> bool:
        """从池中删除指定 Cookie"""
        entries = self._pool.get(platform, [])
        for i, e in enumerate(entries):
            if e.id == cookie_id:
                entries.pop(i)
                self._persist_all()
                utils.logger.info(f"[CookiePool] 删除Cookie: platform={platform} id={cookie_id}")
                return True
        return False

    def list_cookies(self, platform: Optional[str] = None) -> Dict[str, List[Dict]]:
        """列出 Cookie 池状态"""
        result = {}
        platforms = [platform] if platform else list(self._pool.keys())
        for p in platforms:
            entries = self._pool.get(p, [])
            result[p] = [e.to_dict() for e in entries]
        return result

    def get_stats(self) -> Dict[str, Dict[str, Any]]:
        """统计各平台 Cookie 数量"""
        stats = {}
        for platform, entries in self._pool.items():
            valid_count = sum(1 for e in entries if e.valid)
            stats[platform] = {
                "total": len(entries),
                "valid": valid_count,
                "invalid": len(entries) - valid_count,
                "last_used_id": self._last_used_id.get(platform),
            }
        return stats

    def has_valid_cookie(self, platform: str) -> bool:
        """检查指定平台是否还有可用 Cookie"""
        return any(e.valid for e in self._pool.get(platform, []))

    def get_valid_count(self, platform: str) -> int:
        """获取指定平台当前有效 Cookie 数量"""
        return sum(1 for e in self._pool.get(platform, []) if e.valid)

    def allocate_cookies(self, platform: str, count: int) -> List[tuple]:
        """
        一次性分配 count 个不重复的有效 Cookie，用于多浏览器并发时绑定专属 Cookie。
        返回 [(cookie_id, cookie_str), ...]，实际数量 = min(count, 可用数)。
        """
        valid_entries = [e for e in self._pool.get(platform, []) if e.valid]
        # 随机打乱避免每次都用相同的前N个
        random.shuffle(valid_entries)
        allocated = []
        for entry in valid_entries[:count]:
            entry.last_used = time.time()
            allocated.append((entry.id, entry.cookie))
        if allocated:
            utils.logger.info(
                f"[CookiePool] 平台 [{platform}] 批量分配 {len(allocated)} 个Cookie: "
                f"{[a[0] for a in allocated]}"
            )
        return allocated

    def get_unused_cookie(self, platform: str, exclude_ids: set) -> Optional[tuple]:
        """
        获取一个不在 exclude_ids 中的有效 Cookie（Worker 失效后重新绑定用）。
        返回 (cookie_id, cookie_str) 或 None。
        """
        valid_entries = [
            e for e in self._pool.get(platform, [])
            if e.valid and e.id not in exclude_ids
        ]
        if not valid_entries:
            return None
        chosen = random.choice(valid_entries)
        chosen.last_used = time.time()
        utils.logger.info(
            f"[CookiePool] 平台 [{platform}] 重新分配Cookie: {chosen.id} ({chosen.note})"
        )
        return (chosen.id, chosen.cookie)

    # ────────────────── 辅助方法 ──────────────────

    def _find_entry(self, platform: str, cookie_id: str) -> Optional[CookieEntry]:
        for e in self._pool.get(platform, []):
            if e.id == cookie_id:
                return e
        return None

    def _generate_id(self, platform: str) -> str:
        existing = self._pool.get(platform, [])
        max_num = 0
        for entry in existing:
            parts = entry.id.rsplit("_", 1)
            if len(parts) == 2 and parts[1].isdigit():
                max_num = max(max_num, int(parts[1]))
        return f"{platform}_{max_num + 1:02d}"

    def _persist_invalid(self, platform: str, cookie_id: str):
        """将失效状态持久化（文件模式写文件，DB模式写数据库）"""
        source = getattr(config, "COOKIE_POOL_SOURCE", "file")
        if source == "file":
            self._persist_all()
        elif source == "db":
            self._persist_invalid_to_db(platform, cookie_id)

    def _persist_invalid_to_db(self, platform: str, cookie_id: str):
        """将失效状态写回数据库（异步转同步，fire-and-forget）"""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._async_mark_invalid_db(platform, cookie_id))
            else:
                loop.run_until_complete(self._async_mark_invalid_db(platform, cookie_id))
        except Exception as e:
            utils.logger.warning(f"[CookiePool] DB失效标记失败: {e}")

    async def _async_mark_invalid_db(self, platform: str, cookie_id: str):
        """异步将 Cookie 标记为失效写入 DB"""
        try:
            from database.external_db import external_db
            await external_db.ensure_pool()
            sql = "UPDATE cookie_pool SET is_valid = 0 WHERE platform = %s AND cookie_id = %s"
            async with external_db._pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(sql, (platform, cookie_id))
        except Exception as e:
            utils.logger.warning(f"[CookiePool] DB标记失效异常: {e}")

    def _persist_all(self):
        """持久化当前内存中的 Cookie 池状态（file 写 JSON，db 写 MySQL）"""
        source = getattr(config, "COOKIE_POOL_SOURCE", "file")
        if source == "db":
            self._persist_all_to_db()
            return

        path = getattr(config, "COOKIE_POOL_FILE", "config/cookie_pool.json")
        data = {}
        for platform, entries in self._pool.items():
            data[platform] = []
            for e in entries:
                item = {
                    "id": e.id,
                    "cookie": e.cookie,
                    "note": e.note,
                    "valid": e.valid,
                    "fatal_count": e.fatal_count,
                }
                if e.created_at:
                    item["created_at"] = e.created_at
                data[platform].append(item)
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            utils.logger.error(f"[CookiePool] 写入文件失败: {e}")

    def _persist_all_to_db(self):
        """将 fatal_count/is_valid 状态同步回数据库"""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._async_persist_all_db())
            else:
                loop.run_until_complete(self._async_persist_all_db())
        except Exception as e:
            utils.logger.warning(f"[CookiePool] DB持久化失败: {e}")

    async def _async_persist_all_db(self):
        """异步将所有 Cookie 的 fatal_count/is_valid 同步到 DB"""
        try:
            from database.external_db import external_db
            await external_db.ensure_pool()
            sql = (
                "UPDATE cookie_pool SET is_valid = %s, fatal_count = %s "
                "WHERE platform = %s AND cookie_id = %s"
            )
            async with external_db._pool.acquire() as conn:
                async with conn.cursor() as cur:
                    for platform, entries in self._pool.items():
                        for e in entries:
                            await cur.execute(sql, (
                                1 if e.valid else 0,
                                e.fatal_count,
                                platform,
                                e.id,
                            ))
        except Exception as e:
            utils.logger.warning(f"[CookiePool] DB批量持久化异常: {e}")

    @staticmethod
    def parse_cookie_string(cookie_str: str) -> Dict[str, str]:
        """将 'k1=v1; k2=v2' 格式的 Cookie 字符串解析为字典"""
        cookies = {}
        for pair in cookie_str.split(";"):
            pair = pair.strip()
            if "=" in pair:
                key, _, val = pair.partition("=")
                cookies[key.strip()] = val.strip()
        return cookies


# 全局单例
cookie_pool = CookiePool()
