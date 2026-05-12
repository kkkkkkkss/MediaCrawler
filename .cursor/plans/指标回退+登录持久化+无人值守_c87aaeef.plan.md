---
name: 指标回退+登录持久化+无人值守
overview: 修复AI指标提取回退逻辑（全平台适用），修复CDP模式登录持久化问题，实现Cookie池+IP代理集成+异常自动切换的无人值守方案，清理base_config.py
todos:
  - id: fix-ai-fallback
    content: 修改 core.py 中 AI 指标回退逻辑：全平台适用，praise+reply 都为空/0时回退硬编码或DOM
    status: completed
  - id: fix-ks-reply-count
    content: 快手评论数回填：抓到评论后将实际条数写入 reply_count
    status: completed
  - id: test-metrics-fallback
    content: 用 test_url.txt 测试指标回退 + 快手评论数回填
    status: completed
  - id: fix-cdp-login
    content: 修复 CDP 模式登录持久化：临时设置 config.PLATFORM 确保 user_data_dir 按平台隔离
    status: completed
  - id: test-login-persist
    content: 用 test_url_long.txt 测试登录持久化
    status: completed
  - id: doc-server-login
    content: 编写 docs/服务器登录方案.md
    status: completed
  - id: cleanup-config
    content: 清理 base_config.py：加注释 + 新增无人值守配置项
    status: completed
  - id: impl-cookie-pool
    content: 实现 proxy/cookie_pool.py Cookie池模块 + cookie_pool.example.json
    status: completed
  - id: integrate-cookie-pool
    content: 将 Cookie池集成到 core.py _init_platform_client
    status: completed
  - id: integrate-ip-proxy
    content: 将 IP代理集成到 url_check 的浏览器启动和 httpx 请求中
    status: completed
  - id: test-full
    content: 联调测试：Cookie池开关 + IP代理开关 + 常规模式均可正常运行
    status: completed
isProject: false
---

# 指标回退修复 + 登录持久化 + 无人值守方案

## 问题1：AI 指标提取回退逻辑改进

### 当前问题

- [core.py](media_platform/url_check/core.py) 第264-275行：DOM 回退只针对 `platform == "toutiao"`，且条件是"四个指标全为 None"
- 头条 AI 实际返回 `reply_count=0` 其余 `None`，不满足"全 None"条件，DOM 回退永远不触发
- 快手接口只含 `likeCount`/`viewCount`，`reply_count`/`share_count` 取不到

### 修改方案

**A. 修改 [core.py](media_platform/url_check/core.py) `_process_single_url` 中的回退判断：**

- 将回退条件从 `platform == "toutiao" and 全null` 改为：**所有平台通用**，当 `praise_count` 和 `reply_count` 都为 None 或 0 时触发
- 回退目标：
  - 头条 → DOM 提取（`get_article_metrics_from_dom`）
  - 其他平台 → 硬编码字段映射（`fallback_extract`）
- 回退后用硬编码结果补充 AI 缺失字段（已有值不覆盖）

关键代码变更位置（当前第264-275行）:

```python
# 改前：仅头条，条件太严格
if platform == "toutiao" and all(metrics.get(f) is None for f in (...)):

# 改后：所有平台，条件放宽
praise = metrics.get("praise_count")
reply = metrics.get("reply_count")
if (praise is None or praise == 0) and (reply is None or reply == 0):
    if platform == "toutiao":
        dom_metrics = await client.get_article_metrics_from_dom(content_id)
        # 用 DOM 结果补充
    else:
        fb = fallback_extract(platform, raw_json)
        # 用硬编码结果补充
```

**B. 快手评论数回填：**

修改 [core.py](media_platform/url_check/core.py) `_fetch_and_store_comments` 末尾，在抓取到评论后，将实际评论条数回填到 `row["_metrics"]["reply_count"]`（仅当原值为 None 或 0 时）。同时也需要在 `_save_result` 调用中更新数据库中的 `reply_count`。

**C. 用 test_url.txt 测试验证，如有问题需再次测试验证。**

---

## 问题2：登录持久化修复

### 当前问题根因

[core.py](media_platform/url_check/core.py) 第582-603行 `_create_platform_client`：

- **CDP 模式**（第583-591行）：调用 `CDPBrowserManager`，其内部 [cdp_browser.py](tools/cdp_browser.py) 第245行使用 `config.PLATFORM`（默认 `"xhs"`）作为 user_data_dir 的平台名 → 所有平台 Cookie 混在同一个目录
- **Playwright 模式**（第593-604行）：使用 `config.USER_DATA_DIR % platform`，平台名正确，但每次处理完一个平台会调用 `_cleanup_browser` 关闭 context → 如果**没有先在独立模式下登录过该平台**，persistent_context 中没有 Cookie，失败就会弹出扫码

### CDP 模式修复

修改 `_create_platform_client` 中 CDP 模式分支，在调用 `CDPBrowserManager` 之前**临时设置 `config.PLATFORM = platform`**，确保 cdp_browser.py 使用正确的平台名创建 user_data_dir。处理完成后恢复原值。

### Playwright 模式

Playwright 模式下 `launch_persistent_context` 本身就按平台存储了 Cookie（`browser_data/{platform}_user_data_dir`）。**首次**必须有界面扫码一次，之后 Cookie 会自动持久化到磁盘。

### 服务器无界面扫码方案文档

新建 [docs/服务器登录方案.md](docs/服务器登录方案.md)，内容包含：

1. **方案A：本地扫码 → 上传Cookie目录**
  - 本地 `HEADLESS=False` 各平台跑一次登录
  - 将 `browser_data/` 打包上传到服务器
  - 服务器设置 `HEADLESS=True` + `SAVE_LOGIN_STATE=True`
2. **方案B：Cookie 直接注入**
  - 浏览器F12导出Cookie字符串
  - `config.LOGIN_TYPE = "cookie"` + `config.COOKIES = "..."` 
  - 适合单平台临时使用
3. **方案C：VNC远程桌面**
  - Docker 中加 VNC 服务，通过远程桌面扫码
  - 适合K8s/Docker环境首次登录
4. **方案D：Cookie池（配合问题3的实现）**
5. **提示：是否可以借助Redis？（不太懂）**

### 用 test_url_long.txt 测试验证登录持久化

---

## 问题3：无人值守完整方案（Cookie池（是否可以用redis？） + IP代理 + 异常切换）

### 3a. 清理 base_config.py

修改 [config/base_config.py](config/base_config.py)：

- `PLATFORM = "xhs"` → 加注释说明 url_check 模式下此项不生效，无需修改
- `LOGIN_TYPE = "qrcode"` → 新增 `"cookie_pool"` 可选值
- `CRAWLER_TYPE = "detail"` → 加注释说明 url_check 模式用 `--type url_check` 覆盖
- 新增一个 `url_check 模式专属配置` 区块，包含：

```python
# ==================== 无人值守配置（Cookie池/IP代理） ====================
# 是否启用Cookie池（从文件/数据库加载多平台Cookie，自动轮换）
ENABLE_COOKIE_POOL = False

# Cookie池来源："file"(本地JSON文件) | "db"(外部MySQL)
COOKIE_POOL_SOURCE = "file"

# Cookie池文件路径（JSON格式，按平台存储多组Cookie）
COOKIE_POOL_FILE = "config/cookie_pool.json"

# Cookie失效后是否自动切换下一个（True=自动切换，False=报错停止）
COOKIE_AUTO_SWITCH = True

# 同一Cookie连续失败多少次后标记为失效并切换
COOKIE_MAX_FAILURES = 3
```

### 3b. Cookie池模块

新建 [proxy/cookie_pool.py](proxy/cookie_pool.py)：

- `CookiePool` 类：
  - `load()` — 从文件/DB加载多平台Cookie
  - `get_cookie(platform)` — 获取指定平台的当前有效Cookie
  - `rotate(platform)` — 切换到下一个Cookie
  - `mark_invalid(platform, cookie_id)` — 标记Cookie失效
  - `get_stats()` — 统计各平台可用Cookie数量
- Cookie文件格式 (`config/cookie_pool.json`)：

```json
{
  "dy": [
    {"id": "dy_01", "cookie": "sessionid=xxx;...", "note": "账号1"},
    {"id": "dy_02", "cookie": "sessionid=yyy;...", "note": "账号2"}
  ],
  "bili": [...],
  "ks": [...],
  "wb": [...],
  "toutiao": [...]
}
```

### 3c. 集成到 url_check 流程

修改 [core.py](media_platform/url_check/core.py) `_init_platform_client`：

- 当 `ENABLE_COOKIE_POOL=True` 时：
  - 跳过 `pong()` + 扫码登录流程
  - 直接从 CookiePool 获取 Cookie 注入到 Client headers 中
  - 如果 API 调用失败（401/403/Cookie失效）→ 调用 `rotate()` 切换下一个 Cookie → 重试
  - 连续失败 N 次 → `mark_invalid()` 并切换

### 3d. IP代理集成

项目已有 [proxy/proxy_ip_pool.py](proxy/proxy_ip_pool.py)，当前通过 `config.ENABLE_IP_PROXY` 控制。url_check 模式需要：

- 修改 `_create_platform_client`，当 `ENABLE_IP_PROXY=True` 时传入 proxy 参数
- 对 `launch_persistent_context` 和 `CDPBrowserManager` 传入 `playwright_proxy`
- httpx 请求也需要走代理

### 3e. 生成 cookie_pool.json 模板

新建 [config/cookie_pool.example.json](config/cookie_pool.example.json) 供参考。

---

## 测试计划

1. 修改完问题1后，用 `test_url.txt` 验证指标回退 + 快手评论数回填
2. 修改完问题2后，用 `test_url_long.txt` 验证登录持久化（无需重新扫码）
3. 如有问题则修复后重测，直到通过

