# MediaCrawler Windows 部署指南

> 本文档用于将 MediaCrawler 项目完整部署到一台新的 Windows 主机上。
> 包含环境安装、依赖配置、服务启动、前端构建的全部步骤。

---

## 目录

1. [系统要求与依赖版本](#一系统要求与依赖版本)
2. [环境安装](#二环境安装)
3. [项目部署](#三项目部署)
4. [配置文件](#四配置文件)
5. [数据库建表](#五数据库建表)
6. [启动后端服务](#六启动后端服务)
7. [前端构建与部署](#七前端构建与部署)
8. [Cookie 准备](#八cookie-准备)
9. [功能验证](#九功能验证)
10. [注册为 Windows 服务（可选）](#十注册为-windows-服务可选)
11. [防火墙配置](#十一防火墙配置)
12. [常见问题排查](#十二常见问题排查)
13. [项目目录结构](#十三项目目录结构)
14. [API 接口一览](#十四api-接口一览)

---

## 一、系统要求与依赖版本

### 系统环境

| 项目       | 要求               |
|----------|--------------------|
| 操作系统   | Windows 10/11 或 Windows Server 2019+ |
| Python   | **≥ 3.11**（推荐 3.11.x 或 3.12.x）  |
| Node.js  | **≥ 18.x**（前端构建需要，推荐 20.x LTS） |
| Git      | 任意版本             |

### Python 依赖（核心）

| 包               | 版本      | 用途              |
|------------------|-----------|-------------------|
| fastapi          | 0.110.2   | Web API 框架       |
| uvicorn          | 0.29.0    | ASGI 服务器        |
| playwright       | 1.45.0    | 浏览器自动化        |
| aiomysql         | 0.2.0     | MySQL 异步驱动      |
| pandas           | 2.2.3     | 数据处理           |
| openpyxl         | ≥3.1.2    | Excel 读写         |
| python-dotenv    | 1.0.1     | 环境变量管理        |
| httpx            | 0.28.1    | HTTP 客户端        |
| openai           | ≥1.0.0    | AI 字段映射（豆包）  |
| redis            | ~4.6.0    | Redis 缓存         |

> 完整依赖见 `pyproject.toml`，使用 `uv sync` 一键安装。

### 前端依赖

| 包               | 版本      | 用途              |
|------------------|-----------|-------------------|
| vue              | ^3.5.13   | 前端框架           |
| vue-router       | ^4.5.0    | 路由               |
| element-plus     | ^2.9.7    | UI 组件库          |
| axios            | ^1.9.0    | HTTP 请求          |
| vite             | ^6.3.4    | 构建工具           |

---

## 二、环境安装

### 2.1 安装 Python

从 [python.org](https://www.python.org/downloads/) 下载 Python 3.11+ 安装包。

安装时务必勾选：
- ✅ Add Python to PATH
- ✅ Install for all users（建议）

验证：

```powershell
python --version
# 预期输出: Python 3.11.x 或 3.12.x
```

### 2.2 安装 uv（Python 包管理器）

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

安装完成后重开终端，验证：

```powershell
uv --version
# 预期输出: uv 0.x.x
```

### 2.3 安装 Node.js

从 [nodejs.org](https://nodejs.org/) 下载 LTS 版本安装。

验证：

```powershell
node --version   # 预期: v20.x.x
npm --version    # 预期: 10.x.x
```

### 2.4 安装 Git

从 [git-scm.com](https://git-scm.com/download/win) 下载安装。

---

## 三、项目部署

### 3.1 复制项目

**方式 A — 直接复制文件夹**：将整个 `MediaCrawler` 文件夹复制到目标主机，如 `C:\hzww\Code\MediaCrawler`。

**方式 B — Git 克隆**：

```powershell
cd C:\hzww\Code
git clone <YOUR_REPO_URL> MediaCrawler
```

### 3.2 安装 Python 依赖

```powershell
cd C:\hzww\Code\MediaCrawler
uv sync
```

> `uv sync` 会自动读取 `pyproject.toml` 并安装所有依赖到 `.venv` 虚拟环境中。
> 镜像源已配置为清华源（`pyproject.toml` 中的 `[[tool.uv.index]]`）。

### 3.3 安装 Playwright 浏览器

```powershell
uv run playwright install chromium
```

> 这一步会下载 Chromium 浏览器（约 150MB），是扫码登录和链接检测的核心依赖。

### 3.4 安装前端依赖

```powershell
cd frontend
npm install
cd ..
```

---

## 四、配置文件

### 4.1 `.env` 文件（项目根目录）

如果是复制的项目，`.env` 应已存在。否则手动创建：

```powershell
# 在项目根目录创建 .env
```

内容如下（按实际修改数据库地址和密钥）：

```ini
# MySQL Configuration（主数据库，Cookie池/检测结果回写）
EXT_MYSQL_HOST=123.158.253.65
EXT_MYSQL_PORT=30148
EXT_MYSQL_USER=root
EXT_MYSQL_PWD=syyq12WER45!@#!
EXT_MYSQL_DB=db_sdga_report

# Redis（默认本地无密码，可选）
REDIS_DB_HOST=127.0.0.1
REDIS_DB_PWD=
REDIS_DB_PORT=6379
REDIS_DB_NUM=0

# 火山引擎豆包 AI（用于 AI 字段映射，可选）
DOUBAO_API_KEY=your_doubao_api_key
DOUBAO_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
```

### 4.2 `config/base_config.py` 关键配置

确认以下配置正确（通常无需修改）：

```python
ENABLE_COOKIE_POOL = True           # 启用 Cookie 池
COOKIE_POOL_SOURCE = "db"           # Cookie 来源：数据库
COOKIE_MAX_FAILURES = 2             # Cookie 致命失败阈值
LOGIN_TYPE = "cookie_pool"          # 登录方式：Cookie 池
URLCHECK_EXTRACT_MODE = "hardcode_first"  # 指标提取模式
```

### 4.3 前端 API 代理配置

文件：`frontend/vite.config.js`

如果后端部署在同一台机器，默认配置即可。否则修改代理目标：

```javascript
proxy: {
  '/api': {
    target: 'http://后端IP:8888',  // ← 改为后端实际地址
    changeOrigin: true,
  },
},
```

生产环境（构建后直接访问后端）可在前端 `.env.production` 中设置：

```ini
VITE_API_BASE=http://后端IP:8888
```

---

## 五、数据库建表

连接 MySQL 执行以下 SQL（仅首次部署需要）：

```sql
CREATE TABLE IF NOT EXISTS cookie_pool (
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

---

## 六、启动后端服务

### 6.1 快速启动（前台运行）

```powershell
cd C:\hzww\Code\MediaCrawler
$env:PYTHONUTF8=1
uv run uvicorn api.app:app --host 0.0.0.0 --port 8888
```

> ⚠️ **`$env:PYTHONUTF8=1` 是必须的**，否则 Windows 终端会出现中文乱码。

启动成功标志：

```
INFO:     Uvicorn running on http://0.0.0.0:8888 (Press CTRL+C to quit)
[FastAPI] Cookie池已加载: {...}
[FastAPI] 服务已就绪
```

### 6.2 验证服务

浏览器访问或 PowerShell 执行：

```powershell
Invoke-RestMethod -Uri "http://localhost:8888/api/v1/health"
# 预期返回: @{status=ok; version=1.0.0}
```

### 6.3 API 文档

启动后访问自动生成的交互式 API 文档：

```
http://localhost:8888/docs       # Swagger UI
http://localhost:8888/redoc      # ReDoc
```

---

## 七、前端构建与部署

### 7.1 开发模式（热重载）

```powershell
cd C:\hzww\Code\MediaCrawler\frontend
npm run dev
```

访问 `http://localhost:5173`，自动代理 API 请求到后端。

### 7.2 生产构建

```powershell
cd C:\hzww\Code\MediaCrawler\frontend
npm run build
```

构建产物在 `frontend/dist/` 目录。可用任何静态文件服务器托管：

```powershell
# 用 npm 的 serve 包快速预览
npx serve dist -l 5173
```

### 7.3 前端页面路由

| 路径        | 页面         | 功能                 |
|-------------|-------------|---------------------|
| `/`         | 概览         | 系统状态、后端连接检测 |
| `/url-check`| 链接检测     | 单链/批量/文件/MySQL 检测 |
| `/tasks`    | 任务管理     | 异步任务进度、下载结果 |
| `/cookies`  | Cookie 管理  | 查看/添加/删除/批量管理 |
| `/scan`     | 扫码登录     | 浏览器扫码获取 Cookie  |

---

## 八、Cookie 准备

服务启动后首次需要为各平台准备 Cookie。有三种方式：

### 方式 1：前端扫码登录（推荐）

1. 访问前端 → 「扫码登录」页面
2. 选择平台、选择「强制扫码」模式
3. 点击「开始扫码」→ 浏览器弹出，手机扫码
4. 登录成功后 Cookie 自动写入数据库

### 方式 2：命令行扫码工具

```powershell
$env:PYTHONUTF8=1
uv run python tools/cookie_collector.py --storage db --platforms dy ks bili wb
```

### 方式 3：手动添加

通过 API 或前端「Cookie 管理」→「添加 Cookie」手动粘贴。

### 各平台 Cookie 说明

| 平台     | 代号     | 是否必须登录 | 关键 Cookie       |
|----------|----------|-------------|-------------------|
| 抖音     | dy       | ✅ 是        | sessionid          |
| B站      | bili     | ✅ 是        | SESSDATA           |
| 快手     | ks       | ✅ 是        | userId             |
| 微博     | wb       | ✅ 是        | SSOLoginState      |
| 今日头条 | toutiao  | ❌ 否        | ttwid（虚拟即可）   |

> 头条不需要登录即可检测，使用前端「虚拟Cookie」模式自动生成。

---

## 九、功能验证

### 9.1 健康检查

```powershell
Invoke-RestMethod -Uri "http://localhost:8888/api/v1/health"
```

### 9.2 查看 Cookie 池

```powershell
Invoke-RestMethod -Uri "http://localhost:8888/api/v1/cookies" | ConvertTo-Json -Depth 5
```

### 9.3 单链接检测

```powershell
$body = '{"url":"https://www.iesdouyin.com/share/video/7628682927572997561","mode":"both","enable_comments":false}'
Invoke-RestMethod -Uri "http://localhost:8888/api/v1/check/url" -Method POST -Body $body -ContentType "application/json" | ConvertTo-Json -Depth 5
```

### 9.4 批量检测

```powershell
$body = '{"urls":["https://www.iesdouyin.com/share/video/7628682927572997561"],"mode":"both","enable_comments":false}'
$res = Invoke-RestMethod -Uri "http://localhost:8888/api/v1/check/batch" -Method POST -Body $body -ContentType "application/json"
$res  # 获取 task_id
```

### 9.5 查询任务进度

```powershell
Invoke-RestMethod -Uri "http://localhost:8888/api/v1/task/$($res.task_id)"
```

---

## 十、注册为 Windows 服务（可选）

### 使用 NSSM（推荐）

下载 [NSSM](https://nssm.cc/download)，将 `nssm.exe` 放入 PATH。

```powershell
# 注册后端 API 服务
nssm install MediaCrawlerAPI "C:\Users\<你的用户名>\.local\bin\uv.exe" "run uvicorn api.app:app --host 0.0.0.0 --port 8888"
nssm set MediaCrawlerAPI AppDirectory "C:\hzww\Code\MediaCrawler"
nssm set MediaCrawlerAPI AppEnvironmentExtra "PYTHONUTF8=1"
nssm set MediaCrawlerAPI DisplayName "MediaCrawler API Service"
nssm set MediaCrawlerAPI Description "数媒链接检测后端服务"

# 配置日志输出
nssm set MediaCrawlerAPI AppStdout "C:\hzww\Code\MediaCrawler\logs\service_stdout.log"
nssm set MediaCrawlerAPI AppStderr "C:\hzww\Code\MediaCrawler\logs\service_stderr.log"

# 启动服务
nssm start MediaCrawlerAPI
```

### 管理命令

```powershell
nssm status MediaCrawlerAPI     # 查看状态
nssm restart MediaCrawlerAPI    # 重启
nssm stop MediaCrawlerAPI       # 停止
nssm remove MediaCrawlerAPI     # 卸载
```

---

## 十一、防火墙配置

如果需要局域网其他主机访问：

```powershell
# 开放后端 8888 端口
netsh advfirewall firewall add rule name="MediaCrawler API" dir=in action=allow protocol=TCP localport=8888

# 开放前端 5173 端口（开发模式）
netsh advfirewall firewall add rule name="MediaCrawler Frontend" dir=in action=allow protocol=TCP localport=5173
```

---

## 十二、常见问题排查

### Q1: `uv sync` 报错

```
确认 Python 版本 ≥ 3.11:
  python --version

确认 uv 已安装:
  uv --version

尝试清理缓存重装:
  uv cache clean
  uv sync
```

### Q2: Playwright 浏览器安装失败

```powershell
# 手动安装（需要管理员权限）
uv run playwright install chromium --with-deps
```

### Q3: 中文乱码

每次启动前必须设置 UTF-8：

```powershell
$env:PYTHONUTF8=1
```

或写入系统环境变量：控制面板 → 系统 → 高级系统设置 → 环境变量 → 新建系统变量 `PYTHONUTF8`，值为 `1`。

### Q4: 端口被占用

```powershell
# 查看占用 8888 端口的进程
netstat -ano | findstr :8888

# 终止进程
taskkill /PID <进程ID> /F
```

### Q5: 数据库连接失败

```
检查 .env 中的数据库配置:
  EXT_MYSQL_HOST / EXT_MYSQL_PORT / EXT_MYSQL_USER / EXT_MYSQL_PWD

确认网络可达:
  Test-NetConnection -ComputerName 123.158.253.65 -Port 30148
```

### Q6: Cookie 池为空

```
1. 前端 → 扫码登录 → 选择平台 → 强制扫码
2. 或命令行: uv run python tools/cookie_collector.py --storage db --platforms dy --single
3. 头条: 前端 → 扫码登录 → 虚拟Cookie模式
```

### Q7: 前端无法连接后端

```
1. 确认后端已启动: Invoke-RestMethod http://localhost:8888/api/v1/health
2. 确认 vite.config.js 代理地址正确
3. 检查防火墙是否放行
```

---

## 十三、项目目录结构

```
MediaCrawler/
├── api/                    # FastAPI 后端
│   ├── app.py              #   应用入口
│   ├── routes.py           #   所有路由定义
│   ├── service.py          #   业务逻辑
│   ├── task_manager.py     #   异步任务管理
│   └── schemas.py          #   Pydantic 模型
├── config/                 # 配置文件
│   ├── base_config.py      #   主配置
│   └── cookie_pool.json    #   本地 Cookie 池（file模式）
├── database/               # 数据库工具
│   └── external_db.py      #   外部 MySQL 连接池
├── frontend/               # Vue3 前端
│   ├── src/                #   源代码
│   │   ├── api/index.js    #     API 封装
│   │   ├── views/          #     页面组件
│   │   └── router/         #     路由配置
│   ├── dist/               #   构建产物
│   ├── package.json        #   前端依赖
│   └── vite.config.js      #   Vite 配置
├── proxy/                  # Cookie 池管理
│   └── cookie_pool.py      #   Cookie 池核心逻辑
├── tools/                  # 工具脚本
│   ├── cookie_collector.py #   扫码 Cookie 收集器
│   ├── url_detector.py     #   URL 平台识别
│   └── utils.py            #   通用工具
├── data/                   # 运行数据
│   └── url_check/excel/    #   检测结果 Excel
├── browser_data/           # Playwright 浏览器数据
├── docs/                   # 文档
├── .env                    # 环境变量（不要提交到 Git）
├── pyproject.toml          # Python 依赖声明
└── main.py                 # 命令行入口
```

---

## 十四、API 接口一览

基础路径：`http://<IP>:8888/api/v1`

### 系统

| 方法 | 路径              | 功能     |
|------|-------------------|----------|
| GET  | `/health`         | 健康检查 |

### 链接检测

| 方法  | 路径              | 功能                    |
|-------|-------------------|------------------------|
| POST  | `/check/url`      | 单链接同步检测          |
| POST  | `/check/batch`    | 批量 URL 异步检测       |
| POST  | `/check/upload`   | 上传文件异步检测        |
| POST  | `/check/mysql`    | MySQL 数据源异步检测    |

### 任务管理

| 方法  | 路径                      | 功能         |
|-------|---------------------------|-------------|
| GET   | `/task/{task_id}`         | 查询任务进度 |
| POST  | `/task/{task_id}/cancel`  | 终止任务     |
| GET   | `/task/{task_id}/result`  | 下载结果 Excel |
| POST  | `/task/{task_id}/delete`  | 删除任务记录 |
| POST  | `/tasks/delete/batch`     | 批量删除任务 |

### Cookie 管理

| 方法  | 路径                      | 功能             |
|-------|---------------------------|-----------------|
| GET   | `/cookies`                | 查看 Cookie 池   |
| POST  | `/cookies/add`            | 添加 Cookie      |
| POST  | `/cookies/remove`         | 删除 Cookie      |
| POST  | `/cookies/remove/batch`   | 批量删除 Cookie  |
| POST  | `/cookies/reload`         | 重新加载池       |

### 扫码登录

| 方法  | 路径                              | 功能           |
|-------|-----------------------------------|---------------|
| POST  | `/cookies/scan/start`             | 启动扫码会话   |
| GET   | `/cookies/scan/qrcode/{id}`       | 获取二维码截图 |
| GET   | `/cookies/scan/status/{id}`       | 轮询扫码状态   |
| POST  | `/cookies/scan/cancel/{id}`       | 终止扫码会话   |

---

## 快速启动备忘

```powershell
# 一键启动后端（新终端）
cd C:\hzww\Code\MediaCrawler
$env:PYTHONUTF8=1
uv run uvicorn api.app:app --host 0.0.0.0 --port 8888

# 一键启动前端开发模式（另一个终端）
cd C:\hzww\Code\MediaCrawler\frontend
npm run dev

# 访问地址
# 前端: http://localhost:5173
# 后端API文档: http://localhost:8888/docs
# 健康检查: http://localhost:8888/api/v1/health
```
