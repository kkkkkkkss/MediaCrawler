# MediaCrawler Linux（云图桌面版）部署指南

> 本文档用于将 MediaCrawler 项目部署到 Linux 桌面环境（如云图桌面版、Ubuntu Desktop 等）。
> 云图桌面版自带图形界面，扫码登录等需要浏览器的功能可正常使用。

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
10. [注册为 systemd 服务（后台常驻）](#十注册为-systemd-服务后台常驻)
11. [防火墙配置](#十一防火墙配置)
12. [常见问题排查](#十二常见问题排查)
13. [项目目录结构](#十三项目目录结构)
14. [API 接口一览](#十四api-接口一览)
15. [扫码登录在 Linux 上的注意事项](#十五扫码登录在-linux-上的注意事项)

---

## 一、系统要求与依赖版本

### 系统环境

| 项目       | 要求                       |
|----------|---------------------------|
| 操作系统   | Ubuntu 20.04+ / Debian 11+ / 云图桌面版 |
| 桌面环境   | 需要有 X11 或 Wayland（扫码登录需要） |
| Python   | **≥ 3.11**（推荐 3.11.x 或 3.12.x） |
| Node.js  | **≥ 18.x**（前端构建需要，推荐 20.x LTS）|
| Git      | 任意版本                    |

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

### 2.1 系统依赖（必须先执行）

```bash
sudo apt update
sudo apt install -y \
    python3.11 python3.11-venv python3.11-dev \
    build-essential curl wget git \
    libatk1.0-0 libatk-bridge2.0-0 libcups2 libdbus-1-3 \
    libdrm2 libgbm1 libgtk-3-0 libnspr4 libnss3 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libxkbcommon0 libpango-1.0-0 libcairo2 libasound2 \
    fonts-wqy-zenhei fonts-noto-cjk
```

> 上面的库是 Playwright Chromium 的系统依赖，必须安装。
> `fonts-wqy-zenhei` 和 `fonts-noto-cjk` 是中文字体，确保浏览器截图中中文正常显示。

如果系统自带 Python 版本低于 3.11：

```bash
# Ubuntu 22.04 或更低版本可能需要添加 PPA
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3.11-dev
```

验证：

```bash
python3.11 --version
# 或者如果 python3 已经是 3.11+:
python3 --version
```

### 2.2 安装 uv（Python 包管理器）

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

安装完成后重新加载 shell：

```bash
source ~/.bashrc
# 或
source ~/.profile

uv --version
# 预期: uv 0.x.x
```

### 2.3 安装 Node.js

```bash
# 使用 NodeSource 仓库安装 Node.js 20 LTS
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs

node --version   # 预期: v20.x.x
npm --version    # 预期: 10.x.x
```

### 2.4 安装 Git

```bash
sudo apt install -y git
git --version
```

---

## 三、项目部署

### 3.1 复制项目

**方式 A — SCP/SFTP 上传**：

```bash
# 从你的开发机上传（在开发机执行）
scp -r MediaCrawler/ user@云图IP:/opt/MediaCrawler
```

**方式 B — Git 克隆**：

```bash
cd /opt
git clone <YOUR_REPO_URL> MediaCrawler
cd MediaCrawler
```

**方式 C — 直接 U 盘/网盘复制**：

将 `MediaCrawler` 文件夹复制到 `/opt/MediaCrawler`（或你喜欢的路径）。

### 3.2 安装 Python 依赖

```bash
cd /opt/MediaCrawler
uv sync
```

> `uv sync` 会自动读取 `pyproject.toml` 并安装所有依赖到 `.venv` 虚拟环境中。
> 镜像源已配置为清华源（`pyproject.toml` 中的 `[[tool.uv.index]]`），国内下载速度快。

### 3.3 安装 Playwright 浏览器及系统依赖

```bash
uv run playwright install chromium
uv run playwright install-deps
```

> `install-deps` 会自动安装 Chromium 需要的所有系统库（需要 sudo 权限）。
> 如果步骤 2.1 中已安装了所有库，此步可能不额外安装东西。

### 3.4 安装前端依赖

```bash
cd /opt/MediaCrawler/frontend
npm install
cd ..
```

---

## 四、配置文件

### 4.1 `.env` 文件（项目根目录）

```bash
cd /opt/MediaCrawler
cat > .env << 'EOF'
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
EOF
```

> 根据实际数据库地址和密钥修改。

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

修改代理目标为本机或后端地址：

```javascript
proxy: {
  '/api': {
    target: 'http://localhost:8888',  // ← 本机后端
    changeOrigin: true,
  },
},
```

生产环境构建时创建 `frontend/.env.production`：

```bash
echo "VITE_API_BASE=http://云图IP:8888" > frontend/.env.production
```

---

## 五、数据库建表

连接 MySQL 执行以下 SQL（仅首次部署需要）：

```bash
mysql -h 123.158.253.65 -P 30148 -u root -p'syyq12WER45!@#!' db_sdga_report << 'EOF'
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
EOF
```

> 如果表已存在（从其他主机共享同一数据库），则跳过此步。

---

## 六、启动后端服务

### 6.1 快速启动（前台运行）

```bash
cd /opt/MediaCrawler
uv run uvicorn api.app:app --host 0.0.0.0 --port 8888
```

> Linux 不需要设置 `PYTHONUTF8=1`（Linux 默认 UTF-8）。

启动成功标志：

```
INFO:     Uvicorn running on http://0.0.0.0:8888 (Press CTRL+C to quit)
[FastAPI] Cookie池已加载: {...}
[FastAPI] 服务已就绪
```

### 6.2 后台运行（简易方式）

```bash
# 使用 nohup
cd /opt/MediaCrawler
nohup uv run uvicorn api.app:app --host 0.0.0.0 --port 8888 > logs/api.log 2>&1 &

# 查看日志
tail -f logs/api.log

# 查看进程
ps aux | grep uvicorn

# 停止
kill $(pgrep -f "uvicorn api.app")
```

### 6.3 验证服务

```bash
curl -s http://localhost:8888/api/v1/health | python3 -m json.tool
# 预期: {"status": "ok", "version": "1.0.0"}
```

### 6.4 API 文档

浏览器访问：

```
http://localhost:8888/docs       # Swagger UI
http://localhost:8888/redoc      # ReDoc
```

---

## 七、前端构建与部署

### 7.1 开发模式（热重载）

```bash
cd /opt/MediaCrawler/frontend
npm run dev
```

浏览器访问 `http://localhost:5173`，自动代理 API 请求到后端。

### 7.2 生产构建

```bash
cd /opt/MediaCrawler/frontend
npm run build
```

构建产物在 `frontend/dist/` 目录。

### 7.3 用 Nginx 托管前端（推荐生产方式）

```bash
sudo apt install -y nginx
```

创建 Nginx 配置：

```bash
sudo tee /etc/nginx/sites-available/mediacrawler << 'EOF'
server {
    listen 80;
    server_name _;

    # 前端静态文件
    root /opt/MediaCrawler/frontend/dist;
    index index.html;

    # SPA 路由回退
    location / {
        try_files $uri $uri/ /index.html;
    }

    # API 反向代理
    location /api/ {
        proxy_pass http://127.0.0.1:8888;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 300s;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/mediacrawler /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

> 这样前端和后端共用 80 端口，前端直接通过 `/api/` 路径访问后端，无需配置 `VITE_API_BASE`。

### 7.4 前端页面路由

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

1. 浏览器访问前端 → 「扫码登录」页面
2. 选择平台、选择「强制扫码」模式
3. 点击「开始扫码」→ 服务器上会弹出 Chromium 浏览器窗口
4. 前端显示二维码截图 → 手机扫码
5. 登录成功后 Cookie 自动写入数据库

> 注意：扫码登录需要 **在有桌面环境的终端** 上启动后端服务（云图桌面版满足此条件）。

### 方式 2：命令行扫码工具

在云图桌面的终端中执行：

```bash
cd /opt/MediaCrawler
uv run python tools/cookie_collector.py --storage db --platforms dy ks bili wb
```

> 会弹出浏览器窗口，逐平台扫码。

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

```bash
curl -s http://localhost:8888/api/v1/health | python3 -m json.tool
```

### 9.2 查看 Cookie 池

```bash
curl -s http://localhost:8888/api/v1/cookies | python3 -m json.tool
```

### 9.3 单链接检测

```bash
curl -s -X POST http://localhost:8888/api/v1/check/url \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.iesdouyin.com/share/video/7628682927572997561","mode":"both","enable_comments":false}' \
  | python3 -m json.tool
```

### 9.4 批量检测

```bash
curl -s -X POST http://localhost:8888/api/v1/check/batch \
  -H "Content-Type: application/json" \
  -d '{"urls":["https://www.iesdouyin.com/share/video/7628682927572997561"],"mode":"both","enable_comments":false}' \
  | python3 -m json.tool
# 记录返回的 task_id
```

### 9.5 查询任务进度

```bash
TASK_ID="替换为上一步的task_id"
sleep 15
curl -s "http://localhost:8888/api/v1/task/$TASK_ID" | python3 -m json.tool
```

### 9.6 下载结果

```bash
curl -o result.xlsx "http://localhost:8888/api/v1/task/$TASK_ID/result"
ls -la result.xlsx
```

### 9.7 扫码登录测试

```bash
# 启动扫码（抖音，强制扫码模式）
curl -s -X POST "http://localhost:8888/api/v1/cookies/scan/start?platform=dy&scan_mode=force_new" \
  | python3 -m json.tool
# 记录 cookie_id 作为 SESSION_ID

# 获取二维码截图
SESSION_ID="替换为上一步的ID"
curl -s "http://localhost:8888/api/v1/cookies/scan/qrcode/$SESSION_ID" | python3 -m json.tool

# 轮询状态
curl -s "http://localhost:8888/api/v1/cookies/scan/status/$SESSION_ID" | python3 -m json.tool

# 终止扫码
curl -s -X POST "http://localhost:8888/api/v1/cookies/scan/cancel/$SESSION_ID" | python3 -m json.tool
```

---

## 十、注册为 systemd 服务（后台常驻）

### 10.1 创建 service 文件

```bash
sudo tee /etc/systemd/system/mediacrawler-api.service << 'EOF'
[Unit]
Description=MediaCrawler API Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/MediaCrawler
Environment=DISPLAY=:0
ExecStart=/root/.local/bin/uv run uvicorn api.app:app --host 0.0.0.0 --port 8888
Restart=always
RestartSec=5
StandardOutput=append:/opt/MediaCrawler/logs/api.log
StandardError=append:/opt/MediaCrawler/logs/api_error.log

[Install]
WantedBy=multi-user.target
EOF
```

> `Environment=DISPLAY=:0` 确保服务可以打开浏览器窗口（扫码登录需要）。
> 如果使用非 root 用户，把 `User=root` 和路径改为对应用户。

### 10.2 创建日志目录

```bash
mkdir -p /opt/MediaCrawler/logs
```

### 10.3 启用并启动服务

```bash
sudo systemctl daemon-reload
sudo systemctl enable mediacrawler-api
sudo systemctl start mediacrawler-api
```

### 10.4 管理命令

```bash
sudo systemctl status mediacrawler-api    # 查看状态
sudo systemctl restart mediacrawler-api   # 重启
sudo systemctl stop mediacrawler-api      # 停止
sudo journalctl -u mediacrawler-api -f    # 实时查看日志
```

---

## 十一、防火墙配置

### 使用 ufw

```bash
# 开放后端 8888 端口
sudo ufw allow 8888/tcp

# 开放 HTTP 80 端口（Nginx 托管前端时）
sudo ufw allow 80/tcp

# 开放前端开发端口（可选）
sudo ufw allow 5173/tcp

# 查看规则
sudo ufw status
```

### 使用 iptables（如果没有 ufw）

```bash
sudo iptables -A INPUT -p tcp --dport 8888 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 80 -j ACCEPT
```

### 云服务商安全组

如果是云主机，还需在云控制台的安全组中放行对应端口。

---

## 十二、常见问题排查

### Q1: `uv sync` 报错 — Python 版本不够

```bash
python3 --version
# 如果低于 3.11，安装新版本:
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt update && sudo apt install python3.11 python3.11-venv python3.11-dev

# 让 uv 使用指定版本
uv python pin 3.11
uv sync
```

### Q2: Playwright 浏览器启动失败

```bash
# 安装系统依赖
uv run playwright install-deps

# 如果仍报错，手动安装缺失库
sudo apt install -y libgbm1 libatk-bridge2.0-0 libxkbcommon0

# 测试浏览器能否正常运行
uv run python -c "from playwright.sync_api import sync_playwright; p = sync_playwright().start(); b = p.chromium.launch(headless=True); b.close(); p.stop(); print('OK')"
```

### Q3: 扫码登录时浏览器打不开

原因：没有 DISPLAY 环境变量或无桌面环境。

```bash
# 检查是否有图形环境
echo $DISPLAY
# 如果为空，设置:
export DISPLAY=:0

# 确认 X11 正在运行
xdpyinfo | head -5
```

> 云图桌面版应该默认就有 DISPLAY=:0。如果通过 SSH 连接，需要使用 `ssh -X` 或 `ssh -Y` 转发。

### Q4: npm install 失败

```bash
# 清理缓存重试
cd /opt/MediaCrawler/frontend
rm -rf node_modules package-lock.json
npm install

# 如果网络慢，使用淘宝镜像
npm config set registry https://registry.npmmirror.com
npm install
```

### Q5: 端口被占用

```bash
# 查看占用 8888 端口的进程
ss -tlnp | grep :8888
# 或
lsof -i :8888

# 终止进程
kill -9 <PID>
```

### Q6: 数据库连接失败

```bash
# 检查网络连通性
nc -zv 123.158.253.65 30148
# 或
timeout 3 bash -c 'cat < /dev/null > /dev/tcp/123.158.253.65/30148' && echo "OK" || echo "FAIL"

# 检查 .env 配置
cat /opt/MediaCrawler/.env | grep EXT_MYSQL
```

### Q7: 权限不足

```bash
# 确保项目目录可写
sudo chown -R $(whoami):$(whoami) /opt/MediaCrawler

# 确保 browser_data 和 data 目录可写
chmod -R 755 /opt/MediaCrawler/browser_data
chmod -R 755 /opt/MediaCrawler/data
```

### Q8: 浏览器截图中文字体方块

```bash
# 安装中文字体
sudo apt install -y fonts-wqy-zenhei fonts-noto-cjk

# 刷新字体缓存
fc-cache -fv
```

---

## 十三、项目目录结构

```
/opt/MediaCrawler/
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
├── logs/                   # 日志目录
├── docs/                   # 文档
├── .env                    # 环境变量（不提交到 Git）
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

## 十五、扫码登录在 Linux 上的注意事项

### 为什么需要桌面环境？

扫码登录功能使用 Playwright 以 **`headless=False`（有头模式）** 启动 Chromium 浏览器，打开各平台登录页面并截图发送给前端。这个过程需要一个 X11/Wayland 显示服务。

### 云图桌面版的优势

云图桌面版自带完整的 Linux 图形环境（通常是 XFCE、GNOME 或 KDE），因此：
- 扫码登录 ✅ 正常
- 浏览器截图 ✅ 正常
- 链接检测（headless 模式）✅ 正常
- 所有功能与 Windows 版本完全一致

### 如果没有桌面环境怎么办？

对于纯终端服务器（无 GUI），可以使用虚拟显示：

```bash
# 安装 xvfb（虚拟帧缓冲）
sudo apt install -y xvfb

# 启动虚拟显示
Xvfb :99 -screen 0 1280x800x24 &
export DISPLAY=:99

# 然后正常启动后端
uv run uvicorn api.app:app --host 0.0.0.0 --port 8888
```

> 使用 xvfb 时浏览器窗口不会在屏幕上显示，但截图功能正常工作。
> 不过建议优先使用有桌面的环境，调试更方便。

### systemd 中配置 DISPLAY

如果后端以 systemd 服务运行，确保 service 文件中设置了：

```ini
Environment=DISPLAY=:0
```

或使用 xvfb：

```ini
ExecStartPre=/usr/bin/Xvfb :99 -screen 0 1280x800x24
Environment=DISPLAY=:99
```

---

## 快速启动备忘

```bash
# === 一键启动后端 ===
cd /opt/MediaCrawler
uv run uvicorn api.app:app --host 0.0.0.0 --port 8888

# === 一键启动前端（开发模式，另一个终端） ===
cd /opt/MediaCrawler/frontend
npm run dev

# === 或者用 systemd 管理 ===
sudo systemctl start mediacrawler-api
sudo systemctl status mediacrawler-api

# === 访问地址 ===
# 前端（开发模式）: http://localhost:5173
# 前端（Nginx）:    http://localhost
# 后端 API 文档:    http://localhost:8888/docs
# 健康检查:         http://localhost:8888/api/v1/health

# === 常用操作 ===
# 查看日志
tail -f /opt/MediaCrawler/logs/api.log

# 重启服务
sudo systemctl restart mediacrawler-api

# 扫码添加 Cookie（命令行）
cd /opt/MediaCrawler
uv run python tools/cookie_collector.py --storage db --platforms dy --single
```
