# IP 代理池配置使用手册

## 功能概述

IP 代理池用于规避平台对单 IP 访问频率的限制和封禁。本项目内置了完整的 IP 代理池系统，支持：

- **多服务商**：快代理（kuaidaili）、豌豆HTTP（wandouhttp）
- **自动轮换**：IP 过期自动从代理池获取新 IP
- **可验证**：使用前自动验证 IP 是否可用
- **缓存机制**：通过 Redis 缓存未过期 IP，避免重复获取
- **Playwright 集成**：自动将代理注入浏览器上下文
- **httpx 集成**：API 请求自动通过代理发送
- **头条批量检测**：支持每个 url_check worker 绑定独立短效 IP，HTTP 预检和浏览器使用同一出口
- **多浏览器并发**：支持每个浏览器绑定独立 IP 出口

---

## 快速配置

### 第一步：选择代理服务商并注册

| 服务商   | 配置名         | 特点                             | 注册链接                         |
|----------|---------------|----------------------------------|----------------------------------|
| 快代理   | `kuaidaili`   | 国内老牌，IP 质量稳定，需用户名密码认证 | https://www.kuaidaili.com       |
| 豌豆HTTP | `wandouhttp`  | 仅需 app_key，无需用户名密码，仅企业用户 | https://h.wandouip.com          |

### 第二步：配置环境变量

在项目根目录 `.env` 文件中填写对应服务商的密钥：

```bash
# ===== 快代理 =====
KDL_SECERT_ID=你的secret_id
KDL_SIGNATURE=你的签名
KDL_USER_NAME=你的用户名
KDL_USER_PWD=你的密码

# ===== 豌豆HTTP =====
WANDOU_APP_KEY=你的app_key
```

### 第三步：修改配置文件

编辑 `config/base_config.py`：

```python
# 启用 IP 代理
ENABLE_IP_PROXY = True

# 代理池中维持的 IP 数量（服务器 8 worker 建议 30，给坏出口切换留余量）
IP_PROXY_POOL_COUNT = 30

# 代理服务商名称：kuaidaili | wandouhttp
IP_PROXY_PROVIDER_NAME = "wandouhttp"
```

### 第四步：确保 Redis 可用

IP 代理池使用 Redis 缓存 IP 信息（管理过期时间），确保 `.env` 中 Redis 配置正确：

```bash
REDIS_DB_HOST=127.0.0.1
REDIS_DB_PWD=
REDIS_DB_PORT=6379
REDIS_DB_NUM=0
```

---

## 配置参数详解

| 参数                      | 位置              | 默认值        | 说明                                      |
|---------------------------|-------------------|---------------|-------------------------------------------|
| `ENABLE_IP_PROXY`         | base_config.py    | `False`       | IP 代理总开关                              |
| `IP_PROXY_POOL_COUNT`     | base_config.py    | `30`          | 代理池维持的 IP 数量，头条代理并发时建议不小于 worker 数并预留坏出口切换余量 |
| `IP_PROXY_PROVIDER_NAME`  | base_config.py    | `"wandouhttp"` | 服务商：`kuaidaili` / `wandouhttp`        |
| `PROXY_SWITCH_THRESHOLD`  | base_config.py    | `3`           | url_check 模式下连续失败几次切换代理       |
| `URLCHECK_TOUTIAO_PROXY_CONCURRENCY` | base_config.py | `8` | 头条代理模式 worker 并发数，每个 worker 独占一个 IP |
| `URLCHECK_PROXY_MIN_TTL_SEC` | base_config.py | `90` | IP 剩余有效期低于该值时停止取新 URL 并换 IP |
| `URLCHECK_PROXY_ROW_RETRY` | base_config.py | `6` | 同一头条链接遇到疑似代理出口异常时，换 IP 后重试次数 |
| `URLCHECK_GENERIC_PROXY_PLATFORMS` | base_config.py | `[]` | 全局代理可套用的平台白名单；默认不套抖音等账号态平台，避免 Cookie 与出口同时变化 |
| `URLCHECK_TOUTIAO_MOBILE_FALLBACK` | base_config.py | `True` | 头条桌面端异常/疑似误判时，用移动端公开页复核有效性 |
| `URLCHECK_TOUTIAO_MOBILE_FAST_VALIDITY` | base_config.py | `True` | 头条优先用移动端公开接口做快速确认，`both` 模式有指标时直接短路 |
| `DISABLE_SSL_VERIFY`      | base_config.py    | `False`       | 使用代理时可能需要禁用 SSL 验证           |

---

## 工作原理

```
请求发起
  │
  ├─ ENABLE_IP_PROXY = True ?
  │     │
  │     ├─ Yes → ProxyIpPool.get_or_refresh_proxy()/checkout_proxy()
  │     │         │
  │     │         ├─ 当前代理未过期 → 直接使用
  │     │         └─ 当前代理已过期/不存在
  │     │               │
  │     │               ├─ Redis 缓存中有可用 IP → 从缓存取
  │     │               └─ 缓存不足 → 调用服务商 API 获取新 IP
  │     │                              │
  │     │                              ├─ enable_validate_ip → 验证 IP 可用性
  │     │                              └─ 缓存到 Redis（自动过期）
  │     │
  │     └─ No → 直连（不使用代理）
  │
  └─ 使用代理 IP 发送请求
       │
       ├─ 成功 → 处理响应
       └─ 失败 → 标记 IP 无效 → 获取下一个
```

---

## 各模式下的代理使用

### 1. url_check 模式

`url_check` 模式会自动集成代理池。在 `core.py` 中：

- 浏览器启动时自动将代理注入 Playwright context
- 头条批量检测会使用 `checkout_proxy()` 为每个 worker 独占一个豌豆 API 提取 IP
- HTTP 预检和 Playwright 浏览器使用同一个代理出口，避免“预检直连、浏览器代理”的判断割裂
- IP 剩余有效期低于 `URLCHECK_PROXY_MIN_TTL_SEC` 时关闭当前浏览器上下文并换 IP
- 同一代理连续空白页、App 壳页、验证码或加载异常时废弃当前 IP，换新 IP 后继续
- 代理获取失败时直接写 `4=检测异常` 并熔断剩余队列，不回退服务器直连
- 头条移动端公开接口可确认内容时，会优先用于有效性和指标提取，减少桌面端登录/验证码页对 `both` 模式的影响
- 全局 `ENABLE_IP_PROXY=True` 不会默认影响抖音等账号态平台；如确需让非头条平台使用通用代理，需要显式加入 `URLCHECK_GENERIC_PROXY_PLATFORMS`

### 2. 标准爬虫模式（dy/bili/ks 等）

各平台 Crawler 的 `launch_browser` 方法中已预留代理参数接口：

```python
async def launch_browser(self, chromium, playwright_proxy, user_agent, headless):
    self.browser_context = await chromium.launch_persistent_context(
        proxy=playwright_proxy,
        ...
    )
```

### 3. httpx 请求代理

对于直接使用 httpx 发送的 API 请求，通过 `ProxyRefreshMixin` 自动管理：

```python
from proxy.proxy_mixin import ProxyRefreshMixin

class MyClient(ProxyRefreshMixin):
    def __init__(self, proxy_ip_pool):
        self.proxy = None
        self.init_proxy_pool(proxy_ip_pool)

    async def request(self, url):
        await self._refresh_proxy_if_expired()
        async with httpx.AsyncClient(proxy=self.proxy) as client:
            return await client.get(url)
```

---

## 隧道代理说明

隧道代理（Tunnel Proxy）是一种固定入口地址、服务商自动切换出口 IP 的代理方式。与普通代理池的区别：

| 特性 | 普通代理池 | 隧道代理 |
|------|-----------|----------|
| 入口地址 | 每次不同（从池中获取） | 固定（一个入口） |
| 出口IP | 固定（直到过期） | 每次请求/每个Session自动切换 |
| 需要Redis | 是（缓存IP） | 否 |
| 代码复杂度 | 高（池管理、验证、轮换） | 低（配一个URL即可） |
| 适用场景 | 需要 IP 稳定性，浏览器会话能保持同一出口 | 高频接口请求、不要求浏览器会话 IP 稳定 |

### Session 粘滞（保持同一出口IP）

头条 Playwright 批量检测不建议使用“固定入口但每次请求随机出口”的隧道代理。浏览器页面、静态资源、XHR、接口请求如果落到不同出口，平台侧画像会不稳定，容易出现空白页、验证页或请求异常。

只有隧道服务商明确支持 Session 粘滞，并且能保证一个 worker 的浏览器上下文在有效期内固定出口 IP，才适合接入浏览器检测。当前代码已经落地的是普通短效代理池方案：豌豆 HTTP API 提取 `ip:port`，每个头条 worker 独占一个 IP，过期或连续异常后重建浏览器换 IP。

---

## 多浏览器并发场景下的 IP 分配策略

### 场景说明

启用同平台多浏览器并发（`PLATFORM_CONCURRENCY > 1`）后，多个浏览器同时运行。如果共用同一个出口 IP，仍可能触发平台的 IP 频率限制。

### 方案一：普通代理池 + 每浏览器独立 IP（头条推荐）

从代理池中为每个浏览器预分配一个独立 IP：

```python
# 配置示例
ENABLE_IP_PROXY = True
IP_PROXY_POOL_COUNT = 30  # 至少等于最大并发数，并预留坏出口切换余量
IP_PROXY_PROVIDER_NAME = "wandouhttp"

# 头条代理模式
URLCHECK_TOUTIAO_PROXY_CONCURRENCY = 8
URLCHECK_PROXY_MIN_TTL_SEC = 90
URLCHECK_PROXY_ROW_RETRY = 6
```

### 方案二：支持 Session 粘滞的隧道代理

只有服务商能保证一个浏览器 worker 在整个 session 内固定出口 IP 时，才适合用于 Playwright。固定入口但每次请求随机出口的隧道不适合头条批量检测。

### 方案三：不使用代理（当前总开关默认关闭）

如果暂时不考虑 IP 风控，可以不启用代理。多浏览器共用服务器出口 IP，适用于：
- 并发数较低（2~3个）
- 短期批量任务

### 推荐策略

| 场景 | 推荐方案 |
|------|---------|
| 头条 500+ 条批量检测 | 方案一：豌豆 API 提取短效 IP，每 worker 独占 |
| 头条少量临时检测 | 方案三：不启用代理，低并发慢速跑 |
| 需要浏览器会话稳定的其他平台 | 方案一，或明确支持 Session 粘滞的方案二 |
| 固定入口、每请求随机出口的隧道 | 不建议用于 Playwright 批量检测 |
| 临时少量测试 | 方案三 |

---

## 服务商配置详情

### 快代理（kuaidaili）

1. 注册并购买套餐：https://www.kuaidaili.com
2. 在控制台获取：
   - Secret ID（`KDL_SECERT_ID`）
   - 签名（`KDL_SIGNATURE`）
   - 用户名（`KDL_USER_NAME`）— 用于 IP 认证
   - 密码（`KDL_USER_PWD`）— 用于 IP 认证
3. 快代理提取的 IP 带用户名密码认证，格式：`http://user:pwd@ip:port`

### 豌豆HTTP（wandouhttp）

1. 注册并认证：https://h.wandouip.com
2. 在"个人中心 - 开放接口"获取 `app_key`（`WANDOU_APP_KEY`）
3. 使用 API 提取短效 IP，返回 `ip`、`port`、`expire_time` 后，代码会按 `http://ip:port` 注入 Playwright 和 httpx
4. 推荐参数：`xy=1`、`type=2`、`nr=99`、`area_id=0`、`isp=0`
5. **注意**：头条批量检测不要使用豌豆固定隧道入口的随机出口模式，除非服务商支持 Session 粘滞

---

## 常见问题

### Q: 启用代理后请求报 SSL 错误？

设置 `DISABLE_SSL_VERIFY = True`（仅在使用代理时启用，生产环境注意安全风险）。

### Q: 代理 IP 总是验证失败？

- 检查代理服务商账号是否有余额/有效套餐
- 检查 Redis 是否正常运行
- 豌豆HTTP 需要先在控制台添加服务器 IP 到白名单
- 快代理检查用户名密码是否正确

### Q: 如何测试代理是否生效？

```bash
# 使用 curl 测试
curl -x http://代理IP:端口 https://echo.apifox.cn/

# 或启动程序后观察日志
# 正常会看到类似：
# [ProxyIpPool] testing 1.2.3.4 is it valid
# [UrlCheckCrawler] 使用IP代理: 1.2.3.4:8080
```

### Q: 代理池和 Cookie 池可以同时使用吗？

可以，推荐同时启用：
- Cookie 池解决登录问题
- 代理池解决 IP 限流问题

```python
ENABLE_COOKIE_POOL = True
ENABLE_IP_PROXY = True
```

### Q: 代理池中 IP 数量建议设多少？

- 普通使用：`IP_PROXY_POOL_COUNT = 2`
- 高并发/大批量：`IP_PROXY_POOL_COUNT = 5`（至少 >= 最大浏览器并发数）
- IP 用完会自动从服务商重新获取，无需设过大

### Q: 隧道代理和普通代理池能同时启用吗？

当前头条 url_check 落地的是普通短效代理池，不走隧道代理。若后续要接支持 Session 粘滞的隧道，应单独实现每 worker 独立 session，并验证 Playwright 和 httpx 的出口一致。

### Q: 多浏览器并发时如何确保每个浏览器 IP 不同？

启用普通代理池后，头条 worker 通过 `checkout_proxy()` 独占一个短效 IP。不要使用 `get_or_refresh_proxy()` 共享 `current_proxy` 做头条并发，否则多个 worker 仍可能共用同一出口。

### Q: 代理获取失败时会不会自动直连？

头条代理模式不会自动直连。代理拿不到、连续多个代理异常或 IP 快过期无法替换时，剩余链接会写 `4=检测异常` 并保留检测说明，目的是保护服务器原出口 IP 不继续触发风控。
