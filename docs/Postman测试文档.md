# MediaCrawler API 接口使用手册（Postman 保姆级教程）

> 本文档面向**非程序员用户**，手把手教你如何使用 Postman 调用 API 接口完成链接检测、Cookie管理等操作。

---

## 一、接口总览


| 序号  | 接口                                 | 方法   | 用途                      | 数据源            |
| --- | ---------------------------------- | ---- | ----------------------- | -------------- |
| 1   | `/api/v1/health`                   | GET  | 检查服务是否正常                | 无              |
| 2   | `/api/v1/check/url`                | POST | 检测**单条**链接（同步，等待返回结果）   | 手动填URL         |
| 3   | `/api/v1/check/batch`              | POST | 检测**多条**链接（异步，立即返回任务ID） | 手动填URL数组       |
| 4   | `/api/v1/check/upload`             | POST | 上传**文件**批量检测（异步）        | 上传xlsx/csv/txt |
| 5   | `/api/v1/check/mysql`              | POST | 从**数据库**读取URL检测（异步）     | 填数据库连接信息       |
| 6   | `/api/v1/task/{task_id}`           | GET  | 查询异步任务进度                | 无              |
| 7   | `/api/v1/task/{task_id}/result`    | GET  | 下载结果Excel               | 无              |
| 8   | `/api/v1/cookies`                  | GET  | 查看Cookie池状态             | 无              |
| 9   | `/api/v1/cookies/add`              | POST | 手动添加Cookie              | 手动填Cookie      |
| 10  | `/api/v1/cookies/remove`           | POST | 删除Cookie                | 填cookie_id     |
| 11  | `/api/v1/cookies/reload`           | POST | 重新加载Cookie池             | 无              |
| 12  | `/api/v1/cookies/scan/start`       | POST | 启动扫码登录                  | 无              |
| 13  | `/api/v1/cookies/scan/qrcode/{id}` | GET  | 获取扫码二维码截图               | 无              |
| 14  | `/api/v1/cookies/scan/status/{id}` | GET  | 轮询扫码状态                  | 无              |


---

## 二、三种检测方式对比

> **核心问题**：我想检测链接，应该用哪个接口？


| 场景                    | 推荐接口                 | 怎么用          |
| --------------------- | -------------------- | ------------ |
| 只想测1条链接，马上看结果         | `POST /check/url`    | Body里填url    |
| 有几条到几百条URL，想批量测       | `POST /check/batch`  | Body里填urls数组 |
| URL存在Excel/CSV/TXT文件里 | `POST /check/upload` | Body选文件上传    |
| URL存在MySQL数据库表里       | `POST /check/mysql`  | Body填数据库连接信息 |


**Tip**：以上所有参数都可以在 Postman 的 Body 面板中直接修改

---

## 三、快速上手

### 步骤1：导入 Collection

1. 打开 Postman
2. 点击左上角 **Import**
3. 选择项目根目录下的 `MediaCrawler_API.postman_collection.json` 文件导入
4. 导入后左侧会出现 "MediaCrawler API" 文件夹

### 步骤2：设置环境变量

1. 右上角齿轮图标 → **Manage Environments** → **Add**
2. 名称填 `MediaCrawler`
3. 添加变量：


| Variable     | Initial Value           | 说明                 |
| ------------ | ----------------------- | ------------------ |
| `base_url`   | `http://localhost:8888` | API地址。服务器部署改为服务器IP |
| `task_id`    | （留空）                    | 自动填充               |
| `session_id` | （留空）                    | 自动填充               |


1. 右上角选择该环境

### 步骤3：启动后端服务

在终端执行：

```
uv run uvicorn api.app:app --host 0.0.0.0 --port 8888
```

---

## 四、每个接口详细使用说明

---

### 4.1 健康检查 — `GET /api/v1/health`

**用途**：确认服务是否正常运行

**Postman操作**：直接点 Send，无需任何参数

**成功响应**：

```json
{"status": "ok", "version": "1.0.0"}
```

---

### 4.2 单链接检测 — `POST /api/v1/check/url`

**用途**：检测1条链接的有效性和指标数据，立即返回结果

**Postman操作**：

1. 选择该请求
2. 点击 **Body** → **raw** → 右侧下拉选 **JSON**
3. 修改 `url` 字段为你想检测的链接

**请求参数**：


| 字段                | 类型      | 必填  | 默认值      | 说明                                          |
| ----------------- | ------- | --- | -------- | ------------------------------------------- |
| `url`             | string  | 是   | —        | 要检测的URL                                     |
| `mode`            | string  | 否   | `"both"` | `validity`=仅查有效性, `metrics`=仅取指标, `both`=都做 |
| `enable_comments` | boolean | 否   | `false`  | 是否抓评论（会变慢）                                  |


**请求示例**：

```json
{
  "url": "https://www.douyin.com/video/7628682927572997561",
  "mode": "both",
  "enable_comments": false
}
```

**成功响应示例**：

```json
{
  "result": {
    "id": 1,
    "url": "https://www.douyin.com/video/7628682927572997561",
    "platform": "dy",
    "author": "某用户",
    "praise_count": 75,
    "reply_count": 22,
    "visit_count": 1200,
    "share_count": 1,
    "is_valid": 1
  },
  "message": "ok"
}
```

**结果说明**：

- `is_valid=1` → 链接有效
- `is_valid=2` → 链接无效/已删除

---

### 4.3 批量URL检测 — `POST /api/v1/check/batch`

**用途**：一次检测多条链接（2-500条），异步执行

**如何自定义检测的网址**：直接在 Body 的 `urls` 数组中添加、删除、替换网址即可！

**请求参数**：


| 字段                | 类型       | 必填  | 默认值      | 说明                 |
| ----------------- | -------- | --- | -------- | ------------------ |
| `urls`            | string[] | 是   | —        | URL数组，每个元素是一个完整的网址 |
| `mode`            | string   | 否   | `"both"` | 检测模式               |
| `enable_comments` | boolean  | 否   | `false`  | 是否抓评论              |


**请求示例**（想换网址？直接改数组里的内容）：

```json
{
  "urls": [
    "https://www.douyin.com/video/7628682927572997561",
    "https://www.kuaishou.com/short-video/3xrenpyd68isk2q",
    "https://www.bilibili.com/video/BV1godYBUE3f",
    "https://weibo.com/1987241375/QCIQEm6rM"
  ],
  "mode": "both",
  "enable_comments": false
}
```

**响应**（立即返回）：

```json
{
  "task_id": "abc12345",
  "status": "pending",
  "message": "任务已提交"
}
```

**后续操作**：

1. 记住 `task_id`（如果导入了 Collection，会自动保存到环境变量）
2. 用 `GET /task/{task_id}` 查看进度
3. 完成后用 `GET /task/{task_id}/result` 下载Excel

---

### 4.4 上传文件检测 — `POST /api/v1/check/upload`

**用途**：URL存在本地文件中时使用

**Postman操作**：

1. Body 选择 **form-data**（不是 raw！）
2. 第一行：Key 填 `file`，右侧类型选 **File**，Value 点击选择本地文件
3. 后续行填其他参数

**请求参数**：


| 字段                | 类型     | 必填  | 默认值       | 说明                         |
| ----------------- | ------ | --- | --------- | -------------------------- |
| `file`            | file   | 是   | —         | 上传的文件（.xlsx / .csv / .txt） |
| `url_column`      | string | 否   | `"url"`   | Excel/CSV 中URL所在列的列名       |
| `mode`            | string | 否   | `"both"`  | 检测模式                       |
| `enable_comments` | string | 否   | `"false"` | 是否抓评论                      |


**支持的文件格式**：

- `.txt`：每行一个URL
- `.csv`：有列名的逗号分隔文件，通过 `url_column` 指定URL列
- `.xlsx`：Excel文件，通过 `url_column` 指定URL列

---

### 4.5 MySQL数据源检测 — `POST /api/v1/check/mysql`

**用途**：URL存储在MySQL数据库表中时使用

**如何连接你自己的数据库**：直接修改 Body 中的连接参数即可！

**请求参数**：


| 字段                | 类型      | 必填  | 默认值      | 说明            |
| ----------------- | ------- | --- | -------- | ------------- |
| `host`            | string  | 是   | —        | 数据库地址         |
| `port`            | integer | 否   | `3306`   | 数据库端口         |
| `user`            | string  | 是   | —        | 用户名           |
| `password`        | string  | 是   | —        | 密码            |
| `database`        | string  | 是   | —        | 数据库名          |
| `table`           | string  | 是   | —        | 表名（存放URL的表）   |
| `url_column`      | string  | 否   | `"url"`  | URL所在的列名      |
| `mode`            | string  | 否   | `"both"` | 检测模式          |
| `enable_comments` | boolean | 否   | `false`  | 是否抓评论         |
| `batch_size`      | integer | 否   | `50`     | 每批处理数量(1-500) |


**请求示例**（换数据库？改这些字段即可）：

```json
{
  "host": "123.158.253.65",
  "port": 30148,
  "user": "root",
  "password": "your_password",
  "database": "db_sdga_report",
  "table": "bigscreen_data_test",
  "url_column": "url",
  "mode": "both",
  "batch_size": 50
}
```

---

### 4.6 查询任务进度 — `GET /api/v1/task/{task_id}`

**用途**：批量检测/文件上传/MySQL检测提交后，用此接口查看进度

**Postman操作**：将URL中的 `{task_id}` 替换为实际的任务ID

**响应示例**：

```json
{
  "task_id": "abc12345",
  "status": "running",
  "progress": 60.0,
  "total": 10,
  "processed": 6,
  "message": "",
  "result_file": null
}
```

**status 含义**：

- `pending` — 排队中
- `running` — 正在执行
- `completed` — 已完成，可下载结果
- `failed` — 失败

---

### 4.7 下载结果Excel — `GET /api/v1/task/{task_id}/result`

**用途**：任务完成后下载检测结果的Excel报表

**Postman操作**：

1. 确认任务 status=completed
2. 发送请求后点击 Response 区域的 **Save Response → Save to a file**

---

### 4.8 查看Cookie池 — `GET /api/v1/cookies`

**用途**：查看当前系统中存储的所有Cookie及其状态

**Postman操作**：

- 查全部：`GET /api/v1/cookies`
- 查某平台：`GET /api/v1/cookies?platform=dy`

**platform 可选值**：`dy`(抖音) / `ks`(快手) / `bili`(B站) / `wb`(微博) / `toutiao`(头条)

---

### 4.9 手动添加Cookie — `POST /api/v1/cookies/add`

**用途**：手动添加一条Cookie到池中

**操作方法**：

1. 在浏览器中登录目标平台
2. 按 F12 → Network → 刷新页面 → 找到任意请求 → 复制 Cookie 头
3. 在 Postman Body 中填入

**请求参数**：


| 字段         | 类型     | 必填  | 说明                         |
| ---------- | ------ | --- | -------------------------- |
| `platform` | string | 是   | 平台标识：dy/ks/bili/wb/toutiao |
| `cookie`   | string | 是   | 从浏览器复制的Cookie字符串           |
| `note`     | string | 否   | 备注（如"主号"、"备用号"）            |


---

### 4.10 删除Cookie — `POST /api/v1/cookies/remove`

**请求参数**：


| 字段          | 类型     | 必填  | 说明                                |
| ----------- | ------ | --- | --------------------------------- |
| `platform`  | string | 是   | 平台标识                              |
| `cookie_id` | string | 是   | 要删除的Cookie ID（先用 GET /cookies 查看） |


---

### 4.11 扫码登录 — `POST /api/v1/cookies/scan/start`

**用途**：自动打开浏览器，扫码登录后Cookie自动存入系统

**请求参数（URL Query）**：


| 参数         | 必填  | 默认值     | 说明                               |
| ---------- | --- | ------- | -------------------------------- |
| `platform` | 否   | `"all"` | `all`=依次扫码所有平台；`dy`/`ks`等=只扫单个平台 |
| `note`     | 否   | `""`    | 备注信息                             |


**单平台示例**：

```
POST /api/v1/cookies/scan/start?platform=dy&note=主号
```

**全平台示例（不传platform默认all）**：

```
POST /api/v1/cookies/scan/start?note=批量扫码
```

**全平台模式流程**：

1. 调用接口 → 返回 session_id
2. 服务端依次为每个平台打开浏览器
3. 客户端用 `/scan/qrcode/{session_id}` 获取当前平台的二维码截图
4. 扫码完成后自动进入下一个平台
5. 用 `/scan/status/{session_id}` 查看整体进度

**状态响应示例**：

```json
{
  "status": "waiting",
  "current_platform": "ks",
  "current_platform_name": "快手",
  "platforms_queue": ["dy", "ks", "bili", "wb", "toutiao"],
  "completed": {"dy": "dy_03"},
  "skipped": [],
  "message": "等待 快手 扫码..."
}
```

---

## 五、常见问题（FAQ）

### Q1：我想检测自己的链接怎么办？

**方法1（少量链接）**：用 `POST /check/batch`，在 Body 的 `urls` 数组中直接填入你的链接。

**方法2（大量链接在文件里）**：用 `POST /check/upload`，上传你的文件。

**方法3（链接在数据库中）**：用 `POST /check/mysql`，填入你的数据库连接信息。

以上操作全部在 Postman 的 Body 面板中完成，**不需要修改任何代码**。

---

### Q2：想换数据库/换表/换列怎么办？

修改 `POST /check/mysql` 的 Body 参数即可：

- 换数据库 → 改 `host`、`port`、`user`、`password`、`database`
- 换表 → 改 `table`
- URL在别的列 → 改 `url_column`

---

### Q3：异步任务怎么拿结果？

1. 提交任务后获得 `task_id`
2. 反复调用 `GET /task/{task_id}` 直到 `status=completed`
3. 调用 `GET /task/{task_id}/result` 下载Excel文件

---

### Q4：mode 参数的三种模式有什么区别？


| mode       | 做什么       | 速度     | 适用场景         |
| ---------- | --------- | ------ | ------------ |
| `validity` | 只检测链接是否有效 | 最快     | 只需筛选出失效链接    |
| `metrics`  | 只提取指标数据   | 中等     | 已知链接有效，只想取数据 |
| `both`     | 有效性+指标都做  | 最慢（推荐） | 完整检测         |


---

### Q5：Cookie是什么？为什么需要它？

Cookie 相当于网站的登录凭证。部分平台（如快手）需要登录后才能获取视频数据。
系统通过 Cookie 池管理多个账号的登录态，自动轮换使用，避免单账号被风控。

---

### Q6：扫码接口打开了浏览器但很快就关闭了？

可能原因：

1. 该平台已有登录态（之前扫过码），系统检测到已登录就直接获取Cookie并关闭
2. 如果状态显示 `success`，说明Cookie获取成功，是正常行为

---

### Q7：Swagger 在线文档在哪？

启动服务后访问：`http://localhost:8888/docs`

页面上可以直接看到所有接口的参数说明和示例，也可以在线测试。

---

## 六、导入 Collection JSON

将项目根目录下的 `MediaCrawler_API.postman_collection.json` 文件导入 Postman 即可获得所有预设请求。

导入后：

- 每个请求都有预填的示例参数
- 部分请求配置了自动脚本（自动保存 task_id/session_id 到环境变量）
- 你只需修改 Body 中的参数值，然后点 Send

---

## 七、接口调用流程图

```
┌─────────────┐
│ 1. 健康检查  │ GET /health → 确认服务正常
└──────┬──────┘
       │
       ▼
┌─────────────────────────────────────────────┐
│ 2. 确保有Cookie                              │
│   └─ GET /cookies 查看                       │
│   └─ 没有？→ POST /scan/start 扫码添加       │
│   └─ 或 POST /cookies/add 手动添加           │
└──────┬──────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────┐
│ 3. 选择检测方式                              │
│   ├─ 单条：POST /check/url → 直接返回结果    │
│   ├─ 批量：POST /check/batch → task_id      │
│   ├─ 文件：POST /check/upload → task_id     │
│   └─ 数据库：POST /check/mysql → task_id    │
└──────┬──────────────────────────────────────┘
       │（异步任务）
       ▼
┌─────────────────────────────────────────────┐
│ 4. 轮询进度                                  │
│   └─ GET /task/{task_id} → 等 completed      │
└──────┬──────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────┐
│ 5. 下载结果                                  │
│   └─ GET /task/{task_id}/result → Excel文件  │
└─────────────────────────────────────────────┘
```

