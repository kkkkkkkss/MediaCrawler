# -*- coding: utf-8 -*-
# 扫码登录 Cookie 收集器
# 启动浏览器打开各平台登录页，用户扫码后自动抓取 Cookie 并写入 Cookie 池（支持 JSON 文件或 MySQL 数据库）。
# 支持批量多平台、多账号连续扫码。

import asyncio
import json
import os
import sys
import time
from typing import Dict, List, Optional, Tuple

from playwright.async_api import BrowserContext, Page, async_playwright

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import config
from tools import utils

# 各平台登录页 URL 及验证用的关键 Cookie 名
_PLATFORM_LOGIN_CONFIG: Dict[str, Dict] = {
    "dy": {
        "name": "抖音",
        "login_url": "https://www.douyin.com",
        "cookie_urls": ["https://douyin.com", "https://www.douyin.com"],
        "key_cookies": ["sessionid", "passport_csrf_token"],
        "check_cookie": "sessionid",
    },
    "bili": {
        "name": "B站",
        "login_url": "https://www.bilibili.com",
        "cookie_urls": ["https://www.bilibili.com"],
        "key_cookies": ["SESSDATA", "bili_jct", "DedeUserID"],
        "check_cookie": "SESSDATA",
    },
    "ks": {
        "name": "快手",
        "login_url": "https://www.kuaishou.com",
        "cookie_urls": ["https://www.kuaishou.com"],
        "key_cookies": ["userId", "kuaishou.server.web_st", "did"],
        "check_cookie": "userId",
    },
    # 小红书暂不爬取，注释掉
    # "xhs": {
    #     "name": "小红书",
    #     "login_url": "https://www.xiaohongshu.com",
    #     "cookie_urls": ["https://www.xiaohongshu.com"],
    #     "key_cookies": ["a1", "webId", "web_session"],
    #     "check_cookie": "web_session",
    # },
    "wb": {
        "name": "微博",
        "login_url": "https://m.weibo.cn",
        "cookie_urls": ["https://m.weibo.cn", "https://weibo.com"],
        "key_cookies": ["SUBP", "SUB"],
        "check_cookie": "SUB",
    },
    "toutiao": {
        "name": "今日头条",
        "login_url": "https://www.toutiao.com",
        "cookie_urls": ["https://www.toutiao.com"],
        "key_cookies": ["ttwid", "msToken"],
        "check_cookie": "ttwid",
    },
}


# ════════════════════ 存储后端 ════════════════════

def _load_cookie_pool(file_path: str) -> Dict[str, List[Dict]]:
    """加载现有 Cookie 池文件"""
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_cookie_pool(file_path: str, pool: Dict[str, List[Dict]]):
    """保存 Cookie 池到 JSON 文件"""
    os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(pool, f, ensure_ascii=False, indent=2)


def _generate_cookie_id(platform: str, pool: Dict[str, List[Dict]]) -> str:
    """生成自增 Cookie ID（从 JSON 文件中推断）"""
    existing = pool.get(platform, [])
    max_num = 0
    for entry in existing:
        cid = entry.get("id", "")
        parts = cid.rsplit("_", 1)
        if len(parts) == 2 and parts[1].isdigit():
            max_num = max(max_num, int(parts[1]))
    return f"{platform}_{max_num + 1:02d}"


async def _generate_cookie_id_from_db(platform: str) -> str:
    """从数据库中推断下一个自增 Cookie ID"""
    import aiomysql
    pool = await aiomysql.create_pool(
        host=os.getenv("EXT_MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("EXT_MYSQL_PORT", 3306)),
        user=os.getenv("EXT_MYSQL_USER", "root"),
        password=os.getenv("EXT_MYSQL_PWD", ""),
        db=os.getenv("EXT_MYSQL_DB", "db_sdga_report"),
        charset="utf8mb4",
        autocommit=True,
        minsize=1, maxsize=2,
    )
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT cookie_id FROM cookie_pool WHERE platform = %s ORDER BY id DESC LIMIT 1",
                    (platform,)
                )
                row = await cur.fetchone()
                if row:
                    parts = row[0].rsplit("_", 1)
                    if len(parts) == 2 and parts[1].isdigit():
                        return f"{platform}_{int(parts[1]) + 1:02d}"
                return f"{platform}_01"
    finally:
        pool.close()
        await pool.wait_closed()


async def _save_cookie_to_db(platform: str, cookie_id: str, cookie_str: str, note: str):
    """将 Cookie 写入 MySQL 数据库的 cookie_pool 表"""
    import aiomysql
    pool = await aiomysql.create_pool(
        host=os.getenv("EXT_MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("EXT_MYSQL_PORT", 3306)),
        user=os.getenv("EXT_MYSQL_USER", "root"),
        password=os.getenv("EXT_MYSQL_PWD", ""),
        db=os.getenv("EXT_MYSQL_DB", "db_sdga_report"),
        charset="utf8mb4",
        autocommit=True,
        minsize=1, maxsize=2,
    )
    try:
        sql = (
            "INSERT INTO cookie_pool (platform, cookie_id, cookie_str, note, is_valid, fatal_count) "
            "VALUES (%s, %s, %s, %s, 1, 0) "
            "ON DUPLICATE KEY UPDATE cookie_str = VALUES(cookie_str), note = VALUES(note), "
            "is_valid = 1, fatal_count = 0"
        )
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (platform, cookie_id, cookie_str, note))
        print(f"[DB] Cookie 已写入数据库: platform={platform} id={cookie_id}")
    except Exception as e:
        print(f"[DB] 写入失败: {e}")
        raise
    finally:
        pool.close()
        await pool.wait_closed()


# ════════════════════ 扫码核心逻辑 ════════════════════

async def _wait_for_login(
    browser_context: BrowserContext,
    platform: str,
    timeout_seconds: int = 120,
) -> Optional[str]:
    """
    轮询等待用户扫码登录完成。
    通过检测关键 Cookie 是否存在来判断登录是否成功。
    """
    pcfg = _PLATFORM_LOGIN_CONFIG[platform]
    check_key = pcfg["check_cookie"]
    cookie_urls = pcfg["cookie_urls"]

    start = time.time()
    while time.time() - start < timeout_seconds:
        cookies = await browser_context.cookies(urls=cookie_urls)
        cookie_dict = {c["name"]: c["value"] for c in cookies}
        if cookie_dict.get(check_key):
            cookie_str = ";".join(
                [f"{c['name']}={c['value']}" for c in cookies]
            )
            return cookie_str
        await asyncio.sleep(2)

    return None


async def collect_cookie_for_platform(
    platform: str,
    pool_file: str,
    storage: str = "db",
    note: str = "",
    headless: bool = False,
    timeout: int = 120,
) -> bool:
    """
    为指定平台执行一次扫码登录并将 Cookie 写入存储。
    storage: "db" 写入 MySQL, "file" 写入 JSON 文件
    返回 True 表示成功。
    """
    pcfg = _PLATFORM_LOGIN_CONFIG.get(platform)
    if not pcfg:
        print(f"[CookieCollector] 不支持的平台: {platform}")
        return False

    print(f"\n{'='*60}")
    print(f"  平台: {pcfg['name']} ({platform})")
    print(f"  存储: {'MySQL 数据库' if storage == 'db' else 'JSON 文件'}")
    print(f"  请在弹出的浏览器中完成扫码登录")
    print(f"  超时时间: {timeout} 秒")
    print(f"{'='*60}\n")

    async with async_playwright() as p:
        user_data_dir = os.path.join(
            os.getcwd(), "browser_data", f"cookie_collector_{platform}"
        )
        browser_context = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=headless,
            viewport={"width": 1920, "height": 1080},
            accept_downloads=True,
        )

        page = await browser_context.new_page()
        await page.goto(pcfg["login_url"])

        print(f"[CookieCollector] 等待 {pcfg['name']} 扫码登录...")
        cookie_str = await _wait_for_login(browser_context, platform, timeout)

        if cookie_str:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            final_note = note or f"扫码登录 {timestamp}"

            if storage == "db":
                # 数据库模式
                cookie_id = await _generate_cookie_id_from_db(platform)
                await _save_cookie_to_db(platform, cookie_id, cookie_str, final_note)
            else:
                # JSON 文件模式
                pool = _load_cookie_pool(pool_file)
                cookie_id = _generate_cookie_id(platform, pool)
                entry = {
                    "id": cookie_id,
                    "cookie": cookie_str,
                    "note": final_note,
                    "valid": True,
                    "fatal_count": 0,
                    "created_at": timestamp,
                }
                pool.setdefault(platform, []).append(entry)
                _save_cookie_pool(pool_file, pool)

            print(f"[CookieCollector] 登录成功! Cookie 已保存: id={cookie_id}")
            await browser_context.close()
            return True
        else:
            print(f"[CookieCollector] 登录超时，未检测到有效 Cookie")
            await browser_context.close()
            return False


async def batch_collect(
    platforms: List[str],
    pool_file: str = "config/cookie_pool.json",
    storage: str = "db",
    headless: bool = False,
    timeout: int = 120,
):
    """
    批量对多个平台执行扫码登录并收集 Cookie。
    每个平台可连续扫多个账号。
    """
    print("\n" + "=" * 60)
    print("  Cookie 批量收集器")
    print(f"  目标平台: {', '.join(platforms)}")
    print(f"  存储模式: {'MySQL 数据库' if storage == 'db' else 'JSON 文件 → ' + pool_file}")
    print("=" * 60)

    for platform in platforms:
        if platform not in _PLATFORM_LOGIN_CONFIG:
            print(f"\n[CookieCollector] 跳过不支持的平台: {platform}")
            continue

        while True:
            note = input(
                f"\n[{_PLATFORM_LOGIN_CONFIG[platform]['name']}] "
                f"输入备注（如'账号1'，直接回车使用默认，输入 skip 跳过）: "
            ).strip()

            if note.lower() == "skip":
                break

            success = await collect_cookie_for_platform(
                platform=platform,
                pool_file=pool_file,
                storage=storage,
                note=note,
                headless=headless,
                timeout=timeout,
            )

            if success:
                another = input(
                    f"[{_PLATFORM_LOGIN_CONFIG[platform]['name']}] "
                    f"是否继续添加该平台的另一个账号? (y/N): "
                ).strip().lower()
                if another != "y":
                    break
            else:
                retry = input("登录失败，是否重试? (y/N): ").strip().lower()
                if retry != "y":
                    break

    # 显示最终统计
    if storage == "db":
        await _show_db_stats()
    else:
        pool = _load_cookie_pool(pool_file)
        print("\n" + "=" * 60)
        print("  Cookie 收集完成! 当前 Cookie 池统计:")
        print("-" * 60)
        for plat, entries in pool.items():
            name = _PLATFORM_LOGIN_CONFIG.get(plat, {}).get("name", plat)
            print(f"  {name} ({plat}): {len(entries)} 个账号")
        print("=" * 60)


async def _show_db_stats():
    """从数据库显示 Cookie 池统计"""
    import aiomysql
    pool = await aiomysql.create_pool(
        host=os.getenv("EXT_MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("EXT_MYSQL_PORT", 3306)),
        user=os.getenv("EXT_MYSQL_USER", "root"),
        password=os.getenv("EXT_MYSQL_PWD", ""),
        db=os.getenv("EXT_MYSQL_DB", "db_sdga_report"),
        charset="utf8mb4",
        autocommit=True,
        minsize=1, maxsize=2,
    )
    try:
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT platform, "
                    "COUNT(*) AS total, "
                    "SUM(is_valid = 1) AS valid_count "
                    "FROM cookie_pool GROUP BY platform"
                )
                rows = await cur.fetchall()

        print("\n" + "=" * 60)
        print("  Cookie 收集完成! 数据库 Cookie 池统计:")
        print("-" * 60)
        for row in rows:
            plat = row["platform"]
            name = _PLATFORM_LOGIN_CONFIG.get(plat, {}).get("name", plat)
            print(f"  {name} ({plat}): {row['total']} 条 (有效 {row['valid_count']})")
        print("=" * 60)
    finally:
        pool.close()
        await pool.wait_closed()


def main():
    """命令行入口"""
    import argparse

    parser = argparse.ArgumentParser(description="扫码登录 Cookie 收集器")
    parser.add_argument(
        "--platforms", "-p",
        nargs="+",
        default=list(_PLATFORM_LOGIN_CONFIG.keys()),
        choices=list(_PLATFORM_LOGIN_CONFIG.keys()),
        help="要收集 Cookie 的平台列表",
    )
    parser.add_argument(
        "--storage", "-s",
        choices=["db", "file"],
        default="db",
        help="存储模式: db=MySQL数据库(默认), file=JSON文件",
    )
    parser.add_argument(
        "--file", "-f",
        default="config/cookie_pool.json",
        help="Cookie 池 JSON 文件路径（仅 file 模式有效）",
    )
    parser.add_argument(
        "--timeout", "-t",
        type=int,
        default=120,
        help="单次扫码等待超时(秒)",
    )
    parser.add_argument(
        "--single",
        action="store_true",
        help="单平台单账号模式（不进入批量交互循环）",
    )
    args = parser.parse_args()

    if args.single and len(args.platforms) == 1:
        asyncio.run(
            collect_cookie_for_platform(
                platform=args.platforms[0],
                pool_file=args.file,
                storage=args.storage,
                timeout=args.timeout,
            )
        )
    else:
        asyncio.run(
            batch_collect(
                platforms=args.platforms,
                pool_file=args.file,
                storage=args.storage,
                timeout=args.timeout,
            )
        )


if __name__ == "__main__":
    main()
