# IP 代理池配置使用手册

## 功能概述

IP 代理池用于规避平台对单 IP 访问频率的限制和封禁。本项目内置了完整的 IP 代理池系统，支持：

- **多服务商**：快代理（kuaidaili）、豌豆HTTP（wandouhttp）
- **自动轮换**：IP 过期自动从代理池获取新 IP
- **可验证**：使用前自动验证 IP 是否可用
- **缓存机制**：通过 Redis 缓存未过期 IP，避免重复获取
- **Playwright 集成**：自动将代理注入浏览器上下文
- **httpx 集成**：API 请求自动通过代理发送
- **隧道代理**：支持固定入口、自动切换出口的隧道模式
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

# 代理池中维持的 IP 数量（建议 2~5）
IP_PROXY_POOL_COUNT = 2

# 代理服务商名称：kuaidaili | wandouhttp
IP_PROXY_PROVIDER_NAME = "kuaidaili"
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
| `IP_PROXY_POOL_COUNT`     | base_config.py    | `2`           | 代理池维持的 IP 数量                       |
| `IP_PROXY_PROVIDER_NAME`  | base_config.py    | `"kuaidaili"`  | 服务商：`kuaidaili` / `wandouhttp`        |
| `PROXY_SWITCH_THRESHOLD`  | base_config.py    | `3`           | url_check 模式下连续失败几次切换代理       |
| `DISABLE_SSL_VERIFY`      | base_config.py    | `False`       | 使用代理时可能需要禁用 SSL 验证           |

---

## 工作原理

```
请求发起
  │
  ├─ ENABLE_IP_PROXY = True ?
  │     │
  │     ├─ Yes → ProxyIpPool.get_or_refresh_proxy()
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
- API 请求通过 `ProxyRefreshMixin` 自动刷新过期代理
- 连续失败超过 `PROXY_SWITCH_THRESHOLD` 次自动切换

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

## 隧道代理详细配置

### 什么是隧道代理

隧道代理（Tunnel Proxy）是一种固定入口地址、服务商自动切换出口 IP 的代理方式。与普通代理池的区别：

| 特性 | 普通代理池 | 隧道代理 |
|------|-----------|----------|
| 入口地址 | 每次不同（从池中获取） | 固定（一个入口） |
| 出口IP | 固定（直到过期） | 每次请求/每个Session自动切换 |
| 需要Redis | 是（缓存IP） | 否 |
| 代码复杂度 | 高（池管理、验证、轮换） | 低（配一个URL即可） |
| 适用场景 | 需要IP稳定性（如保持登录会话） | 高频请求、不需要IP连续性 |

### 配置方式

在 `.env` 中添加隧道代理地址：

```bash
# 隧道代理地址（格式：协议://用户名:密码@隧道地址:端口）
TUNNEL_PROXY_URL=http://用户名:密码@tunnel.example.com:12345
```

在 `config/base_config.py` 中启用：

```python
# 使用隧道代理（优先级高于普通代理池）
# 启用后 ENABLE_IP_PROXY 的普通代理池逻辑被跳过
ENABLE_TUNNEL_PROXY = True
TUNNEL_PROXY_URL = ""  # 从 .env 读取，这里留空
```

### 主流隧道代理服务商

| 服务商 | 隧道地址格式 | 特点 | 价格参考 |
|--------|-------------|------|---------|
| 快代理隧道 | `http://user:pwd@tps.kdlapi.com:15818` | 支持Session粘滞、城市定向 | ~100元/天 |
| 芝麻代理 | `http://user:pwd@http-dynamic.xiaoxiangdaili.com:10030` | 自动切IP、支持HTTP/SOCKS5 | ~80元/天 |
| 青果代理 | `http://user:pwd@tunnel.qg.net:17010` | 支持多并发Session | ~60元/天 |
| 亮数据(Bright Data) | `http://user:pwd@brd.superproxy.io:22225` | 国际服务商，全球IP | $500+/月 |

### Session 粘滞（保持同一出口IP）

部分业务需要同一个会话内保持IP不变（如登录后的操作）。隧道代理支持通过 Session ID 实现：

```bash
# 快代理示例：在用户名中添加 session-xxx 后缀
TUNNEL_PROXY_URL=http://用户名-session-abc123:密码@tps.kdlapi.com:15818

# 芝麻代理示例：添加 session 参数
TUNNEL_PROXY_URL=http://用户名:密码@tunnel.example.com:10030?session=abc123
```

---

## 多浏览器并发场景下的 IP 分配策略

### 场景说明

启用同平台多浏览器并发（`PLATFORM_CONCURRENCY > 1`）后，多个浏览器同时运行。如果共用同一个出口 IP，仍可能触发平台的 IP 频率限制。

### 方案一：隧道代理 + Session ID 隔离（推荐）

每个浏览器使用不同的 Session ID，隧道服务商为每个 Session 分配不同的出口 IP：

```python
# 配置示例（base_config.py）
ENABLE_TUNNEL_PROXY = True

# 每个 Worker 自动生成唯一 Session ID
# Worker 1 → session-w1 → 出口 IP: 1.2.3.4
# Worker 2 → session-w2 → 出口 IP: 5.6.7.8
# Worker 3 → session-w3 → 出口 IP: 9.10.11.12
TUNNEL_SESSION_PER_WORKER = True
```

实现原理：在 `_create_worker_client` 中，为每个 Worker 生成独立的隧道 URL：

```python
# 伪代码
base_url = "http://user:pwd@tunnel.example.com:15818"
worker_url = f"http://user-session-worker{worker_id}:pwd@tunnel.example.com:15818"
```

### 方案二：普通代理池 + 每浏览器独立 IP

从代理池中为每个浏览器预分配一个独立 IP：

```python
# 配置示例
ENABLE_IP_PROXY = True
IP_PROXY_POOL_COUNT = 5  # 至少等于最大并发数

# 代理分配策略
PROXY_PER_WORKER = True  # 每个 Worker 绑定独立代理
```

### 方案三：不使用代理（当前默认）

如果暂时不考虑 IP 风控，可以不启用代理。多浏览器共用出口 IP，适用于：
- 头条等不限制 IP 频率的平台
- 并发数较低（2~3个）
- 短期批量任务

### 推荐策略

| 场景 | 推荐方案 |
|------|---------|
| 头条（无Cookie、无风控） | 方案三：不用代理，直接多开浏览器 |
| 抖音/快手（严格风控） | 方案一：隧道代理 + Session 隔离 |
| 大批量（500+条/次） | 方案一或方案二 |
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
3. 豌豆HTTP 使用白名单认证（无用户名密码），格式：`http://ip:port`
4. **注意**：仅支持企业用户

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

不建议。`ENABLE_TUNNEL_PROXY = True` 时优先使用隧道代理，普通代理池逻辑被跳过。两者选一种使用即可。

### Q: 多浏览器并发时如何确保每个浏览器 IP 不同？

使用隧道代理 + `TUNNEL_SESSION_PER_WORKER = True`，每个 Worker 自动获得不同的出口 IP。如果不用代理，所有浏览器共用服务器出口 IP。
