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
    """单条 Cookie 记录，含使用统计和刷新时间戳"""

    def __init__(self, cookie_id: str, cookie: str, note: str = "", platform: str = ""):
        self.id = cookie_id
        self.cookie = cookie
        self.note = note
        self.platform = platform
        self.valid = True
        # 兼容旧 is_valid，同时拆出不同用途能力：
        # account_valid 用于投诉/举报等账号态操作，public_detail_valid 用于公开详情/互动量检测。
        self.cookie_type = "account"
        self.account_valid = True
        self.public_detail_valid = True
        self.public_comment_valid = False
        self.fatal_count = 0
        self.last_used: float = 0
        self.created_at: str = ""
        # 新增：使用统计与刷新跟踪
        self.use_count: int = 0
        self.last_used_at: Optional[str] = None      # ISO 格式时间字符串
        self.last_validated_at: Optional[str] = None
        self.last_refreshed_at: Optional[str] = None
        # 标记 cookie 内容是否被 Set-Cookie 更新过（供外部判断是否需要写回DB）
        self._cookie_dirty: bool = False

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "cookie": self.cookie,
            "note": self.note,
            "valid": self.valid,
            "cookie_type": self.cookie_type,
            "account_valid": self.account_valid,
            "public_detail_valid": self.public_detail_valid,
            "public_comment_valid": self.public_comment_valid,
            "fatal_count": self.fatal_count,
            "use_count": self.use_count,
            "last_used_at": self.last_used_at,
            "last_validated_at": self.last_validated_at,
            "last_refreshed_at": self.last_refreshed_at,
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
        self._last_used_id: Dict[str, str] = {}
        # 缓存数据库表字段集合，写入前检查字段是否存在（兼容旧表）
        self._db_columns: set = set()

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
                account_val = entry.get("account_valid")
                detail_val = entry.get("public_detail_valid")
                comment_val = entry.get("public_comment_valid")
                legacy_valid = bool(entry.get("valid", True))
                ce.account_valid = legacy_valid if account_val is None else bool(account_val)
                ce.valid = ce.account_valid
                ce.cookie_type = entry.get("cookie_type") or ("account" if ce.account_valid else "virtual")
                # 旧文件没有能力字段时，账号有效 Cookie 默认也可用于详情/评论；失效 Cookie 需重新验证详情能力。
                ce.public_detail_valid = ce.account_valid if detail_val is None else bool(detail_val)
                ce.public_comment_valid = ce.account_valid if comment_val is None else bool(comment_val)
                ce.fatal_count = entry.get("fatal_count", 0)
                ce.use_count = entry.get("use_count", 0) or 0
                ce.last_used_at = entry.get("last_used_at")
                ce.last_validated_at = entry.get("last_validated_at")
                ce.last_refreshed_at = entry.get("last_refreshed_at")
                if ce.cookie:
                    cookie_list.append(ce)
            if cookie_list:
                self._pool[platform] = cookie_list

    async def _load_from_db(self):
        """从外部 MySQL 的 cookie_pool 表加载（包含所有cookie，含失效的，供前端展示状态）"""
        try:
            from database.external_db import external_db
            await external_db.ensure_pool()

            # 先探测表结构，缓存到 self._db_columns，所有写入方法据此判断字段是否可用
            import aiomysql
            extra_cols = []
            async with external_db._pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SHOW COLUMNS FROM cookie_pool")
                    existing_cols = {r[0] for r in await cur.fetchall()}
            self._db_columns = existing_cols
            for col in (
                "last_used_at", "use_count", "last_validated_at", "last_refreshed_at",
                "cookie_type", "account_valid", "public_detail_valid", "public_comment_valid",
            ):
                if col in existing_cols:
                    extra_cols.append(col)

            base_cols = "platform, cookie_id, cookie_str, note, fatal_count, is_valid"
            if extra_cols:
                base_cols += ", " + ", ".join(extra_cols)
            sql = f"SELECT {base_cols} FROM cookie_pool"

            async with external_db._pool.acquire() as conn:
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
                legacy_valid = bool(row.get("is_valid", 1))
                account_val = row.get("account_valid")
                detail_val = row.get("public_detail_valid")
                comment_val = row.get("public_comment_valid")
                ce.account_valid = legacy_valid if account_val is None else bool(account_val)
                ce.valid = ce.account_valid
                ce.cookie_type = row.get("cookie_type") or ("account" if ce.account_valid else "virtual")
                # 兼容旧表：没有能力字段时，只有账号有效 Cookie 默认具备详情/评论能力。
                ce.public_detail_valid = ce.account_valid if detail_val is None else bool(detail_val)
                ce.public_comment_valid = ce.account_valid if comment_val is None else bool(comment_val)
                ce.use_count = row.get("use_count", 0) or 0
                for ts_field in ("last_used_at", "last_validated_at", "last_refreshed_at"):
                    ts_val = row.get(ts_field)
                    if ts_val is None:
                        setattr(ce, ts_field, None)
                    elif hasattr(ts_val, "strftime"):
                        setattr(ce, ts_field, ts_val.strftime("%Y-%m-%d %H:%M:%S"))
                    else:
                        setattr(ce, ts_field, str(ts_val))
                if ce.cookie:
                    self._pool.setdefault(platform, []).append(ce)
            utils.logger.info(f"[CookiePool] 从数据库加载 {len(rows)} 条Cookie")
        except Exception as e:
            utils.logger.error(f"[CookiePool] 从数据库加载失败: {e}")

    # ────────────────── 获取 Cookie ──────────────────

    @staticmethod
    def _normalize_purpose(purpose: str) -> str:
        return purpose if purpose in {"account", "public_detail", "public_comment"} else "account"

    def _entry_matches_purpose(self, entry: CookieEntry, purpose: str) -> bool:
        """按用途选择 Cookie，避免公开详情检测误消耗账号有效池。"""
        purpose = self._normalize_purpose(purpose)
        if not entry.cookie:
            return False
        if purpose == "public_detail":
            return bool(entry.public_detail_valid)
        if purpose == "public_comment":
            return bool(entry.public_comment_valid)
        return bool(entry.account_valid)

    def _purpose_priority(self, entry: CookieEntry, purpose: str) -> int:
        """公开详情优先使用非账号 session，账号 Cookie 作为最后兜底。"""
        purpose = self._normalize_purpose(purpose)
        if purpose in {"public_detail", "public_comment"} and not entry.account_valid:
            return 0
        return 1

    def _set_account_state(self, entry: CookieEntry, is_valid: bool):
        """旧 valid 字段继续表示账号态，兼容历史调度和前端展示。"""
        entry.account_valid = bool(is_valid)
        entry.valid = entry.account_valid

    def get_cookie(self, platform: str, purpose: str = "account") -> Optional[str]:
        """
        智能选取：优先选使用次数最少、最久未用的cookie，降低单cookie高频使用风险。
        加权逻辑：use_count 越低权重越高，同 use_count 时 last_used 越早权重越高。
        """
        valid_entries = [
            e for e in self._pool.get(platform, [])
            if self._entry_matches_purpose(e, purpose)
        ]
        if not valid_entries:
            utils.logger.warning(f"[CookiePool] 平台 [{platform}] 无可用Cookie")
            return None

        # 按 (use_count, last_used) 升序排序，最少使用且最久未用的排前面
        valid_entries.sort(key=lambda e: (self._purpose_priority(e, purpose), e.use_count, e.last_used))

        # 从前 N 个中随机选一个（N = min(3, 总数)），避免完全固定顺序
        top_n = min(3, len(valid_entries))
        chosen = random.choice(valid_entries[:top_n])

        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        chosen.last_used = time.time()
        chosen.use_count += 1
        chosen.last_used_at = now_str
        self._last_used_id[platform] = chosen.id
        # 异步写回使用统计到DB
        self._persist_usage(platform, chosen.id, chosen.use_count, now_str)
        utils.logger.info(
            f"[CookiePool] 平台 [{platform}] 选中Cookie: {chosen.id} "
            f"(use_count={chosen.use_count}, {chosen.note})"
        )
        return chosen.cookie

    def get_another_cookie(
        self, platform: str, exclude_id: Optional[str] = None, purpose: str = "account"
    ) -> Optional[str]:
        """
        获取一个不同于 exclude_id 的有效 Cookie（用于切换重试），优先选低频cookie。
        """
        valid_entries = [
            e for e in self._pool.get(platform, [])
            if self._entry_matches_purpose(e, purpose)
        ]
        if not valid_entries:
            return None

        if exclude_id:
            candidates = [e for e in valid_entries if e.id != exclude_id]
        else:
            candidates = valid_entries

        if not candidates:
            candidates = valid_entries

        # 按使用次数升序，优先选低频cookie
        candidates.sort(key=lambda e: (self._purpose_priority(e, purpose), e.use_count, e.last_used))
        chosen = candidates[0]

        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        chosen.last_used = time.time()
        chosen.use_count += 1
        chosen.last_used_at = now_str
        self._last_used_id[platform] = chosen.id
        self._persist_usage(platform, chosen.id, chosen.use_count, now_str)
        utils.logger.info(
            f"[CookiePool] 平台 [{platform}] 切换到Cookie: {chosen.id} ({chosen.note})"
        )
        return chosen.cookie

    def get_current_id(self, platform: str) -> Optional[str]:
        """获取上次使用的 Cookie ID"""
        return self._last_used_id.get(platform)

    # ────────────────── 失败处理 ──────────────────

    def report_failure(
        self,
        platform: str,
        level: FailureLevel = FailureLevel.FATAL,
        purpose: str = "account",
    ) -> bool:
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
                # 链接检测走 public_detail 时，只降级详情能力；账号态由 pong/举报流程单独判断。
                if self._normalize_purpose(purpose) == "public_detail":
                    entry.public_detail_valid = False
                elif self._normalize_purpose(purpose) == "public_comment":
                    entry.public_comment_valid = False
                else:
                    self._set_account_state(entry, False)
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

        valid_entries = [
            e for e in self._pool.get(platform, [])
            if self._entry_matches_purpose(e, purpose)
        ]
        return len(valid_entries) > 0

    def report_failure_by_id(self, platform: str, cookie_id: str,
                             level: FailureLevel = FailureLevel.FATAL,
                             purpose: str = "account") -> bool:
        """
        按指定 cookie_id 报告失败，避免并发下 _last_used_id 误伤其他 Cookie。
        """
        if level == FailureLevel.PARSE:
            return True

        entry = self._find_entry(platform, cookie_id)
        if not entry:
            return False

        if level == FailureLevel.FATAL:
            entry.fatal_count += 1
            max_fatal = getattr(config, "COOKIE_MAX_FAILURES", 2)
            if entry.fatal_count >= max_fatal:
                # public_detail 失败只说明详情链路不可用，不能反推账号登录态失效。
                if self._normalize_purpose(purpose) == "public_detail":
                    entry.public_detail_valid = False
                elif self._normalize_purpose(purpose) == "public_comment":
                    entry.public_comment_valid = False
                else:
                    self._set_account_state(entry, False)
                utils.logger.warning(
                    f"[CookiePool] Cookie 致命失败 {entry.fatal_count} 次，"
                    f"标记失效: platform={platform} id={cookie_id}"
                )
            else:
                utils.logger.info(
                    f"[CookiePool] Cookie 致命失败 {entry.fatal_count}/{max_fatal}: "
                    f"platform={platform} id={cookie_id}"
                )
            self._persist_all()

        valid_entries = [
            e for e in self._pool.get(platform, [])
            if self._entry_matches_purpose(e, purpose)
        ]
        return len(valid_entries) > 0

    def mark_invalid(self, platform: str, cookie_id: str):
        """直接标记指定 Cookie 为失效"""
        entry = self._find_entry(platform, cookie_id)
        if entry:
            self._set_account_state(entry, False)
            utils.logger.warning(
                f"[CookiePool] 手动标记失效: platform={platform} id={cookie_id}"
            )
            self._persist_invalid(platform, cookie_id)

    # ────────────────── Cookie 管理（增删查） ──────────────────

    def add_cookie(
        self,
        platform: str,
        cookie_str: str,
        note: str = "",
        cookie_id: str = "",
        cookie_type: str = "account",
        account_valid: bool = True,
        public_detail_valid: Optional[bool] = None,
        public_comment_valid: Optional[bool] = None,
    ) -> str:
        """添加一个新 Cookie 到池中（仅更新内存），返回生成的 ID。DB写入由调用方负责。"""
        if not cookie_id:
            cookie_id = self._generate_id(platform)
        ce = CookieEntry(
            cookie_id=cookie_id,
            cookie=cookie_str,
            note=note,
            platform=platform,
        )
        ce.cookie_type = cookie_type or "account"
        self._set_account_state(ce, account_valid)
        # 新增账号 Cookie 默认继承公开详情能力；虚拟/公开 session 由调用方显式标记。
        ce.public_detail_valid = account_valid if public_detail_valid is None else bool(public_detail_valid)
        ce.public_comment_valid = account_valid if public_comment_valid is None else bool(public_comment_valid)
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
            account_count = sum(1 for e in entries if e.account_valid)
            detail_count = sum(1 for e in entries if e.public_detail_valid)
            comment_count = sum(1 for e in entries if e.public_comment_valid)
            stats[platform] = {
                "total": len(entries),
                "valid": account_count,
                "invalid": len(entries) - account_count,
                "account_valid": account_count,
                "public_detail_valid": detail_count,
                "public_comment_valid": comment_count,
                "account": sum(1 for e in entries if e.cookie_type == "account"),
                "public_session": sum(1 for e in entries if e.cookie_type == "public_session"),
                "virtual": sum(1 for e in entries if e.cookie_type == "virtual"),
                "last_used_id": self._last_used_id.get(platform),
            }
        return stats

    def has_valid_cookie(self, platform: str, purpose: str = "account") -> bool:
        """检查指定平台是否还有可用 Cookie"""
        return any(self._entry_matches_purpose(e, purpose) for e in self._pool.get(platform, []))

    def get_valid_count(self, platform: str, purpose: str = "account") -> int:
        """获取指定平台当前有效 Cookie 数量"""
        return self.get_available_count(platform, purpose)

    def get_available_count(self, platform: str, purpose: str = "account") -> int:
        """按用途统计可用 Cookie 数；默认仍是账号可用，兼容举报逻辑。"""
        return sum(
            1 for e in self._pool.get(platform, [])
            if self._entry_matches_purpose(e, purpose)
        )

    def get_account_valid_count(self, platform: str) -> int:
        return self.get_available_count(platform, "account")

    def get_public_detail_valid_count(self, platform: str) -> int:
        return self.get_available_count(platform, "public_detail")

    def allocate_cookies(self, platform: str, count: int,
                         exclude_ids: set = None,
                         purpose: str = "account") -> List[tuple]:
        """
        一次性分配 count 个不重复的有效 Cookie，用于多浏览器并发时绑定专属 Cookie。
        按 use_count 升序选取（优先使用次数最少的），并递增 use_count 持久化。
        exclude_ids: 本任务中已跑满的Cookie ID集合（strict模式下排除）。
        返回 [(cookie_id, cookie_str), ...]，实际数量 = min(count, 可用数)。
        """
        valid_entries = [
            e for e in self._pool.get(platform, [])
            if self._entry_matches_purpose(e, purpose)
            and (not exclude_ids or e.id not in exclude_ids)
        ]
        # 按 use_count 升序，同 use_count 时随机打乱避免固定顺序
        valid_entries.sort(key=lambda e: (self._purpose_priority(e, purpose), e.use_count, random.random()))
        allocated = []
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        for entry in valid_entries[:count]:
            entry.use_count += 1
            entry.last_used = time.time()
            entry.last_used_at = now_str
            self._persist_usage(platform, entry.id, entry.use_count, now_str)
            allocated.append((entry.id, entry.cookie))
        if allocated:
            utils.logger.info(
                f"[CookiePool] 平台 [{platform}] 批量分配 {len(allocated)} 个Cookie: "
                f"{[a[0] for a in allocated]}"
            )
        return allocated

    def get_unused_cookie(
        self, platform: str, exclude_ids: set, purpose: str = "account"
    ) -> Optional[tuple]:
        """
        获取一个不在 exclude_ids 中的有效 Cookie（Worker 失效后重新绑定用）。
        按 use_count 升序选取，并递增计数。
        返回 (cookie_id, cookie_str) 或 None。
        """
        valid_entries = [
            e for e in self._pool.get(platform, [])
            if self._entry_matches_purpose(e, purpose) and e.id not in exclude_ids
        ]
        if not valid_entries:
            return None
        # 优先选 use_count 最低的
        valid_entries.sort(key=lambda e: (self._purpose_priority(e, purpose), e.use_count, random.random()))
        chosen = valid_entries[0]
        chosen.use_count += 1
        chosen.last_used = time.time()
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        chosen.last_used_at = now_str
        self._persist_usage(platform, chosen.id, chosen.use_count, now_str)
        utils.logger.info(
            f"[CookiePool] 平台 [{platform}] 重新分配Cookie: {chosen.id} ({chosen.note})"
        )
        return (chosen.id, chosen.cookie)

    def update_cookie_str(self, platform: str, cookie_id: str, new_cookie_str: str):
        """Set-Cookie 回写：用响应中更新的cookie替换旧值，并持久化到DB"""
        entry = self._find_entry(platform, cookie_id)
        if entry and entry.cookie != new_cookie_str:
            entry.cookie = new_cookie_str
            entry._cookie_dirty = False
            self._persist_cookie_str_to_db(platform, cookie_id, new_cookie_str)
            utils.logger.debug(
                f"[CookiePool] Cookie内容已更新: platform={platform} id={cookie_id}"
            )

    def mark_validated(self, platform: str, cookie_id: str):
        """标记cookie通过了活体验证，并恢复为可用状态"""
        entry = self._find_entry(platform, cookie_id)
        if entry:
            entry.last_validated_at = time.strftime("%Y-%m-%d %H:%M:%S")
            entry.fatal_count = 0
            entry.cookie_type = "account"
            self._set_account_state(entry, True)
            entry.public_detail_valid = True
            entry.public_comment_valid = True
            self._persist_validated_full(platform, cookie_id, entry.last_validated_at)

    def mark_public_detail_valid(
        self,
        platform: str,
        cookie_id: str,
        is_valid: bool = True,
        cookie_type: Optional[str] = None,
    ):
        """标记公开详情能力；不会改变账号有效状态，避免把过期 session 当账号。"""
        entry = self._find_entry(platform, cookie_id)
        if entry:
            entry.public_detail_valid = bool(is_valid)
            if cookie_type:
                entry.cookie_type = cookie_type
            if is_valid:
                entry.fatal_count = 0
            self._persist_capabilities(platform, cookie_id)

    def _persist_validated_full(self, platform: str, cookie_id: str, validated_at: str):
        """验证通过后将 last_validated_at + fatal_count + is_valid 一并写入存储"""
        source = getattr(config, "COOKIE_POOL_SOURCE", "file")
        if source == "file":
            self._persist_all()
            return
        if source != "db":
            return
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._async_persist_validated(platform, cookie_id, validated_at))
        except Exception:
            pass

    async def _async_persist_validated(self, platform: str, cookie_id: str, validated_at: str):
        try:
            from database.external_db import external_db
            await external_db.ensure_pool()
            parts = ["fatal_count = 0", "is_valid = 1"]
            vals: list = []
            for field, value in {
                "cookie_type": "account",
                "account_valid": 1,
                "public_detail_valid": 1,
                "public_comment_valid": 1,
            }.items():
                if field in self._db_columns:
                    parts.append(f"{field} = %s")
                    vals.append(value)
            if "last_validated_at" in self._db_columns:
                parts.insert(0, "last_validated_at = %s")
                vals.append(validated_at)
            vals.extend([platform, cookie_id])
            sql = f"UPDATE cookie_pool SET {', '.join(parts)} WHERE platform = %s AND cookie_id = %s"
            async with external_db._pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(sql, tuple(vals))
        except Exception as e:
            utils.logger.debug(f"[CookiePool] 验证状态写回失败: {e}")

    def mark_refreshed(self, platform: str, cookie_id: str):
        """标记cookie已通过浏览器刷新，同时持久化 fatal_count=0, is_valid=1"""
        entry = self._find_entry(platform, cookie_id)
        if entry:
            entry.last_refreshed_at = time.strftime("%Y-%m-%d %H:%M:%S")
            entry.fatal_count = 0
            entry.cookie_type = "account"
            self._set_account_state(entry, True)
            entry.public_detail_valid = True
            entry.public_comment_valid = True
            # 刷新时间 + 重置状态一并写入DB，防止重启后旧的失效状态回来
            self._persist_refreshed_full(platform, cookie_id, entry.last_refreshed_at)

    def _persist_refreshed_full(self, platform: str, cookie_id: str, refreshed_at: str):
        """刷新成功后将 last_refreshed_at + fatal_count + is_valid 一并写入DB"""
        source = getattr(config, "COOKIE_POOL_SOURCE", "file")
        if source == "file":
            self._persist_all()
            return
        if source != "db":
            return
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._async_persist_refreshed(platform, cookie_id, refreshed_at))
        except Exception:
            pass

    async def _async_persist_refreshed(self, platform: str, cookie_id: str, refreshed_at: str):
        try:
            from database.external_db import external_db
            await external_db.ensure_pool()
            # 动态拼接 SQL，仅更新旧表中存在的字段
            parts = ["fatal_count = 0", "is_valid = 1"]
            vals: list = []
            for field, value in {
                "cookie_type": "account",
                "account_valid": 1,
                "public_detail_valid": 1,
                "public_comment_valid": 1,
            }.items():
                if field in self._db_columns:
                    parts.append(f"{field} = %s")
                    vals.append(value)
            if "last_refreshed_at" in self._db_columns:
                parts.insert(0, "last_refreshed_at = %s")
                vals.append(refreshed_at)
            vals.extend([platform, cookie_id])
            sql = f"UPDATE cookie_pool SET {', '.join(parts)} WHERE platform = %s AND cookie_id = %s"
            async with external_db._pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(sql, tuple(vals))
        except Exception as e:
            utils.logger.debug(f"[CookiePool] 刷新状态写回失败: {e}")

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
            parts = ["is_valid = 0"]
            if "account_valid" in self._db_columns:
                parts.append("account_valid = 0")
            sql = f"UPDATE cookie_pool SET {', '.join(parts)} WHERE platform = %s AND cookie_id = %s"
            async with external_db._pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(sql, (platform, cookie_id))
        except Exception as e:
            utils.logger.warning(f"[CookiePool] DB标记失效异常: {e}")

    def _persist_capabilities(self, platform: str, cookie_id: str):
        """持久化能力字段；旧表没有这些字段时自动跳过。"""
        source = getattr(config, "COOKIE_POOL_SOURCE", "file")
        if source == "file":
            self._persist_all()
            return
        if source != "db":
            return
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._async_persist_capabilities(platform, cookie_id))
        except Exception:
            pass

    async def _async_persist_capabilities(self, platform: str, cookie_id: str):
        entry = self._find_entry(platform, cookie_id)
        if not entry:
            return
        fields = {
            "cookie_type": entry.cookie_type,
            "account_valid": 1 if entry.account_valid else 0,
            "public_detail_valid": 1 if entry.public_detail_valid else 0,
            "public_comment_valid": 1 if entry.public_comment_valid else 0,
        }
        active = [(name, value) for name, value in fields.items() if name in self._db_columns]
        if not active:
            return
        try:
            from database.external_db import external_db
            await external_db.ensure_pool()
            set_sql = ", ".join(f"{name} = %s" for name, _ in active)
            vals = [value for _, value in active]
            vals.extend([platform, cookie_id])
            sql = f"UPDATE cookie_pool SET {set_sql} WHERE platform = %s AND cookie_id = %s"
            async with external_db._pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(sql, tuple(vals))
        except Exception as e:
            utils.logger.debug(f"[CookiePool] 能力字段写回失败: {e}")

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
                    "cookie_type": e.cookie_type,
                    "account_valid": e.account_valid,
                    "public_detail_valid": e.public_detail_valid,
                    "public_comment_valid": e.public_comment_valid,
                    "fatal_count": e.fatal_count,
                    "use_count": e.use_count,
                    "last_used_at": e.last_used_at,
                    "last_validated_at": e.last_validated_at,
                    "last_refreshed_at": e.last_refreshed_at,
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
        """异步将所有 Cookie 的状态同步到 DB，动态检查字段兼容旧表"""
        try:
            from database.external_db import external_db
            await external_db.ensure_pool()

            # 基础字段（旧表一定有）
            set_parts = ["is_valid = %s", "fatal_count = %s"]
            # 可选字段，旧表不一定有
            optional_fields = [
                ("cookie_type", lambda e: e.cookie_type),
                ("account_valid", lambda e: 1 if e.account_valid else 0),
                ("public_detail_valid", lambda e: 1 if e.public_detail_valid else 0),
                ("public_comment_valid", lambda e: 1 if e.public_comment_valid else 0),
                ("use_count", lambda e: e.use_count),
                ("last_used_at", lambda e: e.last_used_at),
                ("last_validated_at", lambda e: e.last_validated_at),
                ("last_refreshed_at", lambda e: e.last_refreshed_at),
            ]
            active_optionals = [
                (name, getter) for name, getter in optional_fields
                if name in self._db_columns
            ]
            for name, _ in active_optionals:
                set_parts.append(f"{name} = %s")

            sql = (
                f"UPDATE cookie_pool SET {', '.join(set_parts)} "
                "WHERE platform = %s AND cookie_id = %s"
            )

            async with external_db._pool.acquire() as conn:
                async with conn.cursor() as cur:
                    for platform, entries in self._pool.items():
                        for e in entries:
                            vals: list = [1 if e.account_valid else 0, e.fatal_count]
                            for _, getter in active_optionals:
                                vals.append(getter(e))
                            vals.extend([platform, e.id])
                            await cur.execute(sql, tuple(vals))
        except Exception as e:
            utils.logger.warning(f"[CookiePool] DB批量持久化异常: {e}")

    def _persist_usage(self, platform: str, cookie_id: str, use_count: int, last_used_at: str):
        """将使用统计异步写回DB"""
        source = getattr(config, "COOKIE_POOL_SOURCE", "file")
        if source != "db":
            return
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._async_persist_usage(platform, cookie_id, use_count, last_used_at))
        except Exception:
            pass

    async def _async_persist_usage(self, platform: str, cookie_id: str, use_count: int, last_used_at: str):
        try:
            from database.external_db import external_db
            await external_db.ensure_pool()
            # 只更新旧表中实际存在的字段
            parts, vals = [], []
            if "use_count" in self._db_columns:
                parts.append("use_count = %s"); vals.append(use_count)
            if "last_used_at" in self._db_columns:
                parts.append("last_used_at = %s"); vals.append(last_used_at)
            if not parts:
                return
            vals.extend([platform, cookie_id])
            sql = f"UPDATE cookie_pool SET {', '.join(parts)} WHERE platform = %s AND cookie_id = %s"
            async with external_db._pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(sql, tuple(vals))
        except Exception as e:
            utils.logger.debug(f"[CookiePool] 使用统计写回失败: {e}")

    def _persist_cookie_str_to_db(self, platform: str, cookie_id: str, new_cookie_str: str):
        """将更新后的cookie字符串写回DB（Set-Cookie回写）"""
        source = getattr(config, "COOKIE_POOL_SOURCE", "file")
        if source != "db":
            return
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._async_update_cookie_str(platform, cookie_id, new_cookie_str))
        except Exception:
            pass

    async def _async_update_cookie_str(self, platform: str, cookie_id: str, new_cookie_str: str):
        try:
            from database.external_db import external_db
            await external_db.ensure_pool()
            sql = "UPDATE cookie_pool SET cookie_str = %s WHERE platform = %s AND cookie_id = %s"
            async with external_db._pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(sql, (new_cookie_str, platform, cookie_id))
        except Exception as e:
            utils.logger.debug(f"[CookiePool] Cookie字符串写回失败: {e}")

    def _persist_timestamp_to_db(self, platform: str, cookie_id: str, field: str, value: str):
        """将时间戳字段写回DB（旧表缺少该字段时跳过）"""
        source = getattr(config, "COOKIE_POOL_SOURCE", "file")
        if source != "db":
            return
        if self._db_columns and field not in self._db_columns:
            return
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._async_update_timestamp(platform, cookie_id, field, value))
        except Exception:
            pass

    async def _async_update_timestamp(self, platform: str, cookie_id: str, field: str, value: str):
        # 只允许更新已知的时间戳字段，防止SQL注入
        allowed = {"last_validated_at", "last_refreshed_at", "last_used_at"}
        if field not in allowed:
            return
        try:
            from database.external_db import external_db
            await external_db.ensure_pool()
            sql = f"UPDATE cookie_pool SET {field} = %s WHERE platform = %s AND cookie_id = %s"
            async with external_db._pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(sql, (value, platform, cookie_id))
        except Exception as e:
            utils.logger.debug(f"[CookiePool] 时间戳写回失败: {e}")

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
