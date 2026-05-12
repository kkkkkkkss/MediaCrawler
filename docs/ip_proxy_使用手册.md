# IP 代理池配置使用手册

## 功能概述

IP 代理池用于规避平台对单 IP 访问频率的限制和封禁。本项目内置了完整的 IP 代理池系统，支持：

- **多服务商**：快代理（kuaidaili）、豌豆HTTP（wandouhttp）
- **自动轮换**：IP 过期自动从代理池获取新 IP
- **可验证**：使用前自动验证 IP 是否可用
- **缓存机制**：通过 Redis 缓存未过期 IP，避免重复获取
- **Playwright 集成**：自动将代理注入浏览器上下文
- **httpx 集成**：API 请求自动通过代理发送

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
# 示例：在 DyCreator 中使用代理
async def launch_browser(self, chromium, playwright_proxy, user_agent, headless):
    # playwright_proxy 由主流程自动传入（从代理池获取）
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
        # 每次请求前自动检查并刷新代理
        await self._refresh_proxy_if_expired()
        async with httpx.AsyncClient(proxy=self.proxy) as client:
            return await client.get(url)
```

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
2. 在"个人中心 → 开放接口"获取 `app_key`（`WANDOU_APP_KEY`）
3. 豌豆HTTP 使用白名单认证（无用户名密码），格式：`http://ip:port`
4. **注意**：仅支持企业用户

---

## 隧道代理模式（扩展）

如果使用隧道代理（固定入口 IP，服务商自动切换出口），无需代理池，直接配置：

```python
# 在 base_config.py 中添加
TUNNEL_PROXY_URL = "http://用户名:密码@隧道地址:端口"
```

然后在请求代码中直接使用该 URL 作为代理，无需 Redis 和代理池逻辑。

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
- 高并发/大批量：`IP_PROXY_POOL_COUNT = 5`
- IP 用完会自动从服务商重新获取，无需设过大
