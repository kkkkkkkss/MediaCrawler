# API 部署与接口文档

## 一、部署指南

### 1. 环境要求

- Python 3.10+
- Redis（IP 代理池缓存用，不启用代理可不装）
- MySQL（DB 模式 Cookie 池 + 外部业务表）
- 浏览器：Chrome 或 Edge（Playwright 会自动管理）

### 2. 安装依赖

```bash
# 推荐用 uv（快速包管理）
uv sync

# 或传统 pip
pip install -r requirements.txt

# 安装 Playwright 浏览器
playwright install chromium
```

### 3. 配置文件

#### .env（必须）

```bash
# Cookie 池 DB 模式
EXT_MYSQL_HOST=123.158.253.65
EXT_MYSQL_PORT=30148
EXT_MYSQL_USER=root
EXT_MYSQL_PWD=syyq12WER45!@#!
EXT_MYSQL_DB=db_sdga_report

# AI 提取（指标不足时自动调用）
DOUBAO_API_KEY=your_doubao_api_key
DOUBAO_BASE_URL=https://ark.cn-beijing.volces.com/api/v3

# IP 代理（可选）
WANDOU_APP_KEY=your_wandou_app_key
# 或
KDL_SECERT_ID=xxx
KDL_SIGNATURE=xxx
KDL_USER_NAME=xxx
KDL_USER_PWD=xxx
```

#### config/base_config.py（关键项）

```python
# Cookie 池（推荐开启，DB 模式）
ENABLE_COOKIE_POOL = True
COOKIE_POOL_SOURCE = "db"               # "file" 或 "db"
COOKIE_POOL_FILE = "config/cookie_pool.json"  # file 模式路径
COOKIE_MAX_FAILURES = 2                  # Cookie 致命失败阈值
LOGIN_TYPE = "cookie_pool"

# 指标提取（推荐默认）
URLCHECK_EXTRACT_MODE = "hardcode_first"

# IP 代理（可选）
ENABLE_IP_PROXY = False
```

### 4. 准备 Cookie

三种方式任选：

```bash
# 方式1：扫码脚本存入数据库（推荐）
uv run python tools/cookie_collector.py --platforms dy --storage db --single

# 方式2：API 接口添加
curl -X POST http://localhost:8888/api/v1/cookies/add \
  -H "Content-Type: application/json" \
  -d '{"platform":"dy","cookie":"sessionid=xxx;","note":"账号1"}'

# 方式3：API 扫码登录（Windows 服务远程部署用）
curl -X POST "http://localhost:8888/api/v1/cookies/scan/start?platform=dy&note=远程扫码"
```

### 5. 启动服务

```bash
# 方式1：直接运行
uv run python -m api.app

# 方式2：uvicorn 启动（推荐）
uv run uvicorn api.app:app --host 0.0.0.0 --port 8888

# 方式3：带 worker 数量（多进程）
uv run uvicorn api.app:app --host 0.0.0.0 --port 8888 --workers 2

# 方式4：后台运行（Linux/macOS）
nohup uv run uvicorn api.app:app --host 0.0.0.0 --port 8888 > api.log 2>&1 &

# 一键杀死所有占用 8888 的进程
taskkill /F /PID (netstat -ano | findstr ":8888" | ForEach-Object { $_.Split()[-1] })

# 找到占用8888的进程ID
netstat -ano | findstr ":8888"

# 杀掉它（把最后面的数字替换成你查到的 PID）
taskkill /F /PID 数字
```

### 6. Windows 服务化部署（推荐）

使用 NSSM 将 API 注册为 Windows 服务：

```powershell
# 下载 NSSM: https://nssm.cc/download
nssm install MediaCrawlerAPI "C:\path\to\python.exe" "-m uvicorn api.app:app --host 0.0.0.0 --port 8888"
nssm set MediaCrawlerAPI AppDirectory "C:\hzww\Code\MediaCrawler"
nssm start MediaCrawlerAPI
```

或直接用 PowerShell 后台运行：

```powershell
Start-Process -NoNewWindow -FilePath "uv" -ArgumentList "run uvicorn api.app:app --host 0.0.0.0 --port 8888" -WorkingDirectory "C:\hzww\Code\MediaCrawler"
```

### 7. 验证部署

```powershell
Invoke-RestMethod -Uri "http://localhost:8888/api/v1/health"
# 返回: {"status":"ok","version":"1.0.0"}
```

---

## 二、API 接口说明

**基础路径：** `http://<host>:8888/api/v1`

### 健康检查

```
GET /api/v1/health
```

响应：

```json
{"status": "ok", "version": "1.0.0"}
```

---

### Cookie 池管理

#### 查看 Cookie 池

```
GET /api/v1/cookies?platform=dy
```

参数：`platform`（可选，不传返回所有平台）

响应：

```json
{
  "pool": {
    "dy": [
      {"id": "dy_01", "cookie": "...", "note": "扫码登录 2026-05-06", "valid": true, "fatal_count": 0},
      {"id": "dy_02", "cookie": "...", "note": "账号2", "valid": true, "fatal_count": 1}
    ]
  },
  "stats": {
    "dy": {"total": 2, "valid": 2, "invalid": 0, "last_used_id": "dy_01"}
  }
}
```

**Cookie 选择策略：** 所有 `valid=true` 的 Cookie 参与纯随机选择（含 `fatal_count>0` 但未达阈值的），只有 `valid=false` 的才被排除。

#### 添加 Cookie

```
POST /api/v1/cookies/add
Content-Type: application/json

{
  "platform": "dy",
  "cookie": "sessionid=xxx; passport_csrf_token=yyy;",
  "note": "抖音-小明的账号"
}
```

支持的平台：`dy`(抖音) / `bili`(B站) / `ks`(快手) / `wb`(微博) / `toutiao`(头条)

响应：

```json
{"success": true, "message": "Cookie 添加成功", "cookie_id": "dy_03"}
```

#### 删除 Cookie

```
POST /api/v1/cookies/remove
Content-Type: application/json

{"platform": "dy", "cookie_id": "dy_02"}
```

#### 重新加载 Cookie 池

```
POST /api/v1/cookies/reload
```

从数据库重新加载，常用于手动修改 DB 数据后刷新内存。

---

### 扫码登录 API（远程部署用）

适用于 Windows 服务器等无法直接操作浏览器的场景，通过 API 触发扫码登录流程。

#### 启动扫码会话

```
POST /api/v1/cookies/scan/start?platform=dy&note=远程扫码
```

参数：

- `platform`：平台标识（dy/bili/ks/wb/toutiao）
- `note`：备注（可选）

响应：

```json
{"success": true, "message": "扫码会话已启动", "cookie_id": "a1b2c3d4"}
```

返回的 `cookie_id` 即 `session_id`，用于后续轮询。

#### 获取二维码截图

```
GET /api/v1/cookies/scan/qrcode/{session_id}
```

响应：

```json
{
  "status": "waiting",
  "message": "请使用手机App扫描页面中的二维码",
  "qrcode_base64": "iVBORw0KGgo..."
}
```

`qrcode_base64` 是 PNG 格式的页面截图（含二维码），前端可用 `<img src="data:image/png;base64,{qrcode_base64}">` 展示。

#### 轮询登录状态

```
GET /api/v1/cookies/scan/status/{session_id}
```

响应：

```json
{
  "status": "success",
  "platform": "dy",
  "cookie_id": "dy_03",
  "message": "登录成功! Cookie已保存: dy_03"
}
```

`status` 可选值：

- `starting`：浏览器启动中
- `waiting`：等待扫码
- `success`：登录成功，Cookie 已自动入库
- `timeout`：3分钟内未扫码，超时
- `failed`：登录失败

#### 扫码登录完整流程

```
1. POST /cookies/scan/start?platform=dy     → 拿到 session_id
2. GET  /cookies/scan/qrcode/{session_id}   → 拿到截图展示给用户
3. GET  /cookies/scan/status/{session_id}    → 每2秒轮询一次
4. 当 status="success" → Cookie 已自动保存到 DB
```

**PowerShell 测试示例：**

```powershell
# 启动扫码
$start = Invoke-RestMethod -Uri "http://localhost:8888/api/v1/cookies/scan/start?platform=dy&note=测试" -Method POST
$sid = $start.cookie_id
Write-Host "Session: $sid"

# 获取截图
$qr = Invoke-RestMethod -Uri "http://localhost:8888/api/v1/cookies/scan/qrcode/$sid" -Method GET
$qr.status  # waiting / success

# 轮询状态
do {
    Start-Sleep -Seconds 3
    $st = Invoke-RestMethod -Uri "http://localhost:8888/api/v1/cookies/scan/status/$sid" -Method GET
    Write-Host "状态: $($st.status) - $($st.message)"
} while ($st.status -eq "waiting")
```

---

### URL 检测

#### 单链接检测（同步）

```
POST /api/v1/check/url
Content-Type: application/json

{
  "url": "https://www.bilibili.com/video/BV1godYBUE3f",
  "mode": "both",
  "enable_comments": false
}
```

`mode` 可选值：

- `validity` — 仅检测链接是否有效
- `metrics` — 仅抓取转赞评指标
- `both` — 同时检测+抓指标

响应：

```json
{
  "result": {
    "id": 1,
    "url": "https://www.bilibili.com/video/BV1godYBUE3f",
    "platform": "bili",
    "content_type": "视频",
    "author": "花叔v",
    "praise_count": 618,
    "reply_count": 297,
    "visit_count": 38939,
    "share_count": 117,
    "is_valid": 1
  },
  "message": "ok"
}
```

#### 批量URL检测（异步）

```
POST /api/v1/check/batch
Content-Type: application/json

{
  "urls": [
    "https://www.bilibili.com/video/BV1godYBUE3f",
    "https://www.douyin.com/video/7628682927572997561",
    "https://weibo.com/1987241375/QCIQEm6rM"
  ],
  "mode": "both",
  "enable_comments": false
}
```

响应（立即返回 task_id）：

```json
{"task_id": "a1b2c3d4", "status": "pending", "message": "任务已提交"}
```

#### 上传文件检测

```
POST /api/v1/check/upload
Content-Type: multipart/form-data

file: <Excel/CSV/TXT 文件>
url_column: url（Excel/CSV中的列名）
mode: both
enable_comments: false
```

#### MySQL 数据源检测

从 MySQL 表中批量读取 URL 并检测。

```
POST /api/v1/check/mysql
Content-Type: application/json

{
  "host": "123.158.253.65",
  "port": 30148,
  "user": "root",
  "password": "syyq12WER45!@#!",
  "database": "db_sdga_report",
  "table": "bigscreen_data_test",
  "url_column": "url",
  "mode": "both",
  "batch_size": 50
}
```

**PowerShell 测试：**

```powershell
$body = @{
    host = "123.158.253.65"
    port = 30148
    user = "root"
    password = "syyq12WER45!@#!"
    database = "db_sdga_report"
    table = "bigscreen_data_test"
    url_column = "url"
    mode = "both"
    batch_size = 50
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8888/api/v1/check/mysql" -Method POST -ContentType "application/json" -Body $body
```

---

### 任务管理

#### 查询任务进度

```
GET /api/v1/task/{task_id}
```

响应：

```json
{
  "task_id": "a1b2c3d4",
  "status": "running",
  "progress": 60.0,
  "total": 10,
  "processed": 6,
  "message": "处理中...",
  "result_file": null
}
```

`status` 可选值：`pending` / `running` / `completed` / `failed`

#### 下载结果 Excel

```
GET /api/v1/task/{task_id}/result
```

任务完成后返回 Excel 文件下载。

---

## 三、客户使用流程

### 初次部署后

1. **添加 Cookie**：通过扫码 API 或手动复制 Cookie 调用 `/cookies/add`
2. **验证健康**：调用 `/health` 确认服务正常
3. **提交检测**：调用 `/check/batch` 或 `/check/mysql` 提交任务
4. **查询进度**：调用 `/task/{id}` 轮询进度
5. **获取结果**：调用 `/task/{id}/result` 下载 Excel

### Cookie 获取方法

**方式1：API 扫码登录（推荐）**

1. 调用 `POST /cookies/scan/start?platform=dy` 启动会话
2. 获取二维码截图展示给用户
3. 用户用手机 App 扫码
4. 登录成功后 Cookie 自动入库

**方式2：手动复制 Cookie**

1. 用 Chrome 打开对应平台网站并登录
2. 按 F12 → Application → Cookies
3. 复制所有 Cookie 为字符串格式（`name=value; name2=value2;...`）
4. 调用 `POST /cookies/add` 提交

**方式3：命令行扫码脚本**

```bash
uv run python tools/cookie_collector.py --platforms dy bili ks wb toutiao --storage db
```

---

## 四、Swagger 文档

服务启动后自动生成交互式 API 文档：

- Swagger UI: `http://<host>:8888/docs`
- ReDoc: `http://<host>:8888/redoc`

可直接在浏览器中测试所有接口。

---

## 五、数据库表结构（DB 模式）

连接信息（.env 中配置）：

```
Host: 123.158.253.65
Port: 30148
User: root
Password: syyq12WER45!@#!
Database: db_sdga_report
```

### cookie_pool 表

```sql
CREATE TABLE cookie_pool (
    id INT AUTO_INCREMENT PRIMARY KEY,
    platform VARCHAR(20) NOT NULL COMMENT '平台: dy/bili/ks/wb/toutiao',
    cookie_id VARCHAR(50) NOT NULL COMMENT 'Cookie唯一ID',
    cookie_str TEXT NOT NULL COMMENT 'Cookie字符串',
    note VARCHAR(200) DEFAULT '' COMMENT '备注',
    is_valid TINYINT DEFAULT 1 COMMENT '1=有效 0=失效',
    fatal_count INT DEFAULT 0 COMMENT '致命失败累计次数',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_platform_cookie_id (platform, cookie_id),
    INDEX idx_platform_valid (platform, is_valid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Cookie池';
```

**Cookie 失败机制：**

- `fatal_count`：每次 Cookie 鉴权失败 +1，达到阈值（默认2）时 `is_valid` 置为 0
- 链接无效/视频删除/404 等不归咎 Cookie，不计入 `fatal_count`
- 所有 `is_valid=1` 的 Cookie 纯随机参与选择

### bigscreen_data_test 表（URL 检测数据源）

```sql
-- 程序从此表读取 URL 并回写检测结果
SELECT id, url, is_valid, praise_count, reply_count,
       visit_count, share_count, forward_count
FROM bigscreen_data_test
WHERE url IS NOT NULL AND url != ''
  AND (is_valid IS NULL OR is_valid = 0)
ORDER BY id ASC LIMIT 50;
```

---

## 六、常见问题

### Q: API 启动报错 `No module named 'xxx'`

确保用 `uv sync` 安装了所有依赖，或检查 `pyproject.toml` 中是否包含该包。

### Q: 单链接接口响应很慢（30s+）

单链接检测需要启动浏览器，首次请求较慢（浏览器冷启动），后续会快一些。批量模式共享同一浏览器实例效率更高。

### Q: 如何让客户无感知添加 Cookie？

推荐使用扫码登录 API：

1. 前端展示 `/cookies/scan/qrcode/{id}` 返回的截图
2. 用户扫码后自动入库，无需手动复制粘贴
3. Cookie 到期后系统自动标记失效，通过 `/cookies` 接口可看到状态

### Q: 如何配合 IP 代理使用？

```python
ENABLE_IP_PROXY = True
IP_PROXY_PROVIDER_NAME = "wandouhttp"  # 或 "kuaidaili"
```

代理会自动注入到浏览器和 HTTP 请求中。

### Q: 防火墙需要放行哪些端口？

- `8888`（API 服务端口）
- 出站：各平台域名（douyin.com, bilibili.com 等）
- 出站：`123.158.253.65:30148`（MySQL 数据库）

### Q: Cookie 什么时候会被标记失效？

仅当平台返回 "account blocked"、空响应等**明确的鉴权失败**时，才计入 `fatal_count`。以下情况**不会**归咎 Cookie：

- 视频/文章已删除（aweme_detail 为 null）
- 链接 404
- 网络超时
- 内容不存在

