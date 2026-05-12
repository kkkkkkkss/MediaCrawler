# MediaCrawler API 接口文档

**基础地址**: `http://<服务器IP>:8888/api/v1`

**Swagger 文档**: `http://<服务器IP>:8888/docs`

---

## 目录

1. [健康检查](#1-健康检查)
2. [链接检测](#2-链接检测)
   - [单条检测](#21-单条检测)
   - [批量检测](#22-批量检测)
   - [文件上传检测](#23-文件上传检测)
   - [MySQL 数据源检测](#24-mysql-数据源检测)
3. [任务管理](#3-任务管理)
   - [查询任务进度](#31-查询任务进度)
   - [终止任务](#32-终止任务)
   - [删除任务](#33-删除任务)
   - [批量删除任务](#34-批量删除任务)
4. [结果获取](#4-结果获取)
   - [下载结果文件](#41-下载结果文件exseljson)
   - [获取 JSON 结果](#42-获取-json-结果)
   - [获取评论 JSON](#43-获取评论-json)
   - [下载评论文件](#44-下载评论文件)
   - [获取单条检测结果](#45-获取单条检测结果)
5. [回调配置](#5-回调配置)
   - [查看回调配置](#51-查看回调配置)
   - [更新回调配置](#52-更新回调配置)
6. [Cookie 管理](#6-cookie-管理)
7. [回调 Payload 规范](#7-回调-payload-规范)
8. [错误码表](#8-错误码表)
9. [完整调用流程示例](#9-完整调用流程示例)

---

## 1. 健康检查

### `GET /health`

检查服务是否在线。

**响应示例**:

```json
{
  "status": "ok",
  "version": "1.0.0"
}
```

---

## 2. 链接检测

### 2.1 单条检测

### `POST /check/url`

提交单条 URL 检测。返回任务 ID，后续通过轮询获取进度和结果。

**请求体**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| url | string | 是 | 要检测的 URL |
| mode | string | 否 | 检测模式：`validity`/`metrics`/`both`(默认) |
| enable_comments | bool | 否 | 是否抓取评论(默认 false) |
| callback_url | string | 否 | 回调地址（不传则用全局配置） |

**请求示例**:

```json
{
  "url": "https://www.douyin.com/video/7628682927572997561",
  "mode": "both",
  "enable_comments": true,
  "callback_url": "https://your-server.com/api/callback"
}
```

**响应示例**:

```json
{
  "task_id": "single-abc123",
  "status": "pending",
  "message": "单条检测任务已提交"
}
```

### 2.2 批量检测

### `POST /check/batch`

批量提交多个 URL。

**请求体**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| urls | string[] | 是 | URL 列表（至少1个，最多500个） |
| mode | string | 否 | 检测模式(默认 both) |
| enable_comments | bool | 否 | 是否抓取评论 |
| callback_url | string | 否 | 回调地址 |

**请求示例**:

```json
{
  "urls": [
    "https://www.douyin.com/video/7628682927572997561",
    "https://weibo.com/1806503894/QEoCgiKVs"
  ],
  "mode": "both",
  "enable_comments": false
}
```

**响应示例**:

```json
{
  "task_id": "batch-def456",
  "status": "pending",
  "message": "任务已提交"
}
```

### 2.3 文件上传检测

### `POST /check/upload`

上传 `.xlsx`/`.csv`/`.txt` 文件进行批量检测。

**参数（multipart/form-data）**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| file | file | 是 | 上传文件 |
| url_column | string | 否 | URL 所在列名(默认 "url") |
| mode | string | 否 | 检测模式(默认 both) |
| enable_comments | bool | 否 | 是否抓取评论 |
| callback_url | string | 否 | 回调地址 |

**cURL 示例**:

```bash
curl -X POST "http://localhost:8888/api/v1/check/upload" \
  -F "file=@test.xlsx" \
  -F "url_column=url" \
  -F "mode=both" \
  -F "enable_comments=false"
```

**响应示例**:

```json
{
  "task_id": "file-test-ghi789",
  "status": "pending",
  "message": "文件已上传，任务已提交"
}
```

### 2.4 MySQL 数据源检测

### `POST /check/mysql`

从 MySQL 数据库读取 URL 进行批量检测。

**请求体**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| host | string | 是 | 数据库主机 |
| port | int | 否 | 端口(默认 3306) |
| user | string | 否 | 用户名(默认 root) |
| password | string | 是 | 密码 |
| database | string | 是 | 数据库名 |
| table | string | 是 | 表名 |
| url_column | string | 否 | URL 列名(默认 url) |
| mode | string | 否 | 检测模式(默认 both) |
| batch_size | int | 否 | 每批处理数量(默认 50) |
| enable_comments | bool | 否 | 是否抓取评论 |
| callback_url | string | 否 | 回调地址 |

---

## 3. 任务管理

### 3.1 查询任务进度

### `GET /task/{task_id}`

查询任务执行状态和日志。

**查询参数**:

| 参数 | 类型 | 说明 |
|------|------|------|
| log_offset | int | 日志偏移量，增量获取（从上次返回的 log_total 开始） |

**响应示例**:

```json
{
  "task_id": "batch-def456",
  "status": "running",
  "progress": 60.0,
  "total": 10,
  "processed": 6,
  "message": "",
  "result_file": null,
  "error": null,
  "logs": ["[15:30:01] 开始处理...", "[15:30:05] 第1个URL处理完成"],
  "log_total": 12
}
```

**status 状态值**:

| 值 | 说明 |
|----|------|
| pending | 排队中 |
| running | 执行中 |
| completed | 已完成 |
| failed | 失败 |
| cancelled | 已取消 |

### 3.2 终止任务

### `POST /task/{task_id}/cancel`

终止正在运行的任务。

### 3.3 删除任务

### `POST /task/{task_id}/delete`

删除已完成/失败/取消的任务记录。

### 3.4 批量删除任务

### `POST /tasks/delete/batch`

批量删除任务记录。

**请求体**: `{"task_ids": ["batch-001", "batch-002"]}`

---

## 4. 结果获取

### 4.1 下载结果文件（Excel/JSON）

### `GET /task/{task_id}/result`

下载任务结果文件。

**查询参数**:

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| format | string | excel | 文件格式：`excel` 或 `json` |

**示例**:

```
GET /task/batch-def456/result?format=excel  → 下载 .xlsx 文件
GET /task/batch-def456/result?format=json   → 下载 .json 文件
```

### 4.2 获取 JSON 结果

### `GET /task/{task_id}/result/json`

以 JSON 响应体直接返回结果（无需下载文件），适合程序化调用。

**响应示例**:

```json
{
  "task_id": "batch-def456",
  "total": 3,
  "completed_at": "2026-05-12T15:30:00",
  "results": [
    {
      "id": 1,
      "url": "https://www.douyin.com/video/7628682927572997561",
      "platform": "dy",
      "platform_name": "抖音",
      "content_type": "视频",
      "is_valid": true,
      "author": "xxx",
      "title": "xxx",
      "praise_count": 100,
      "reply_count": 50,
      "visit_count": 1000,
      "share_count": 20
    }
  ]
}
```

### 4.3 获取评论 JSON

### `GET /task/{task_id}/comments`

获取评论数据（JSON 响应体）。仅当提交时 `enable_comments=true` 才有数据。

**响应示例**:

```json
{
  "task_id": "single-abc123",
  "total_comments": 30,
  "results": [
    {
      "content_url": "https://www.douyin.com/note/7637386017688071330",
      "content_id": "7637386017688071330",
      "platform": "dy",
      "comments": [
        {
          "comment_id": "xxx",
          "author_name": "用户A",
          "comment_text": "很不错！",
          "comment_like_count": 5,
          "comment_reply_count": 2,
          "comment_time": "2026-05-10 12:00:00"
        }
      ]
    }
  ]
}
```

### 4.4 下载评论文件

### `GET /task/{task_id}/comments/download`

下载评论数据文件。

**查询参数**:

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| format | string | json | 文件格式：`json` 或 `excel` |

### 4.5 获取单条检测结果

### `GET /check/url/result/{task_id}`

获取单条 URL 检测的详细结果（含实时日志）。仅适用于 `POST /check/url` 提交的任务。

---

## 5. 回调配置

### 5.1 查看回调配置

### `GET /callback/config`

查看当前全局回调配置。

**响应示例**:

```json
{
  "enabled": false,
  "url": "",
  "max_retries": 3,
  "retry_intervals": [5, 15, 30]
}
```

### 5.2 更新回调配置

### `POST /callback/config`

动态更新全局回调配置（运行时生效，重启后恢复默认）。

**请求体**（只传需要修改的字段）:

```json
{
  "enabled": true,
  "url": "https://your-agent-hub.com/api/callback"
}
```

**回调地址优先级**：

任务提交时传的 `callback_url` > 全局配置的 `url`

---

## 6. Cookie 管理

| 端点 | 方法 | 说明 |
|------|------|------|
| `/cookies` | GET | 查看 Cookie 池 |
| `/cookies/add` | POST | 手动添加 Cookie |
| `/cookies/remove` | POST | 移除 Cookie |
| `/cookies/remove/batch` | POST | 批量移除 |
| `/cookies/reload` | POST | 重新加载 Cookie 池 |
| `/cookies/scan/start` | POST | 开始扫码登录 |
| `/cookies/scan/qrcode/{session_id}` | GET | 获取二维码 |
| `/cookies/scan/status/{session_id}` | GET | 查询扫码状态 |
| `/cookies/scan/cancel/{session_id}` | POST | 取消扫码 |

---

## 7. 回调 Payload 规范

任务完成后，系统会向回调地址发送 HTTP POST 请求。如果开启了评论抓取，会分两次发送。

### 主结果回调

```json
{
  "event": "task_completed",
  "task_id": "batch-abc123",
  "status": "completed",
  "timestamp": "2026-05-12T15:30:00+00:00",
  "data": {
    "task_id": "batch-abc123",
    "total": 3,
    "completed_at": "2026-05-12T15:30:00",
    "results": [
      {
        "id": 1,
        "url": "https://...",
        "platform": "dy",
        "platform_name": "抖音",
        "content_type": "视频",
        "is_valid": true,
        "author": "xxx",
        "title": "xxx",
        "praise_count": 100,
        "reply_count": 50,
        "visit_count": 1000,
        "share_count": 20
      }
    ]
  }
}
```

### 评论回调

```json
{
  "event": "comments_ready",
  "task_id": "batch-abc123",
  "status": "completed",
  "timestamp": "2026-05-12T15:30:05+00:00",
  "data": {
    "task_id": "batch-abc123",
    "total_comments": 30,
    "exported_at": "2026-05-12T15:30:05",
    "results": [
      {
        "content_url": "https://...",
        "content_id": "xxx",
        "platform": "dy",
        "comments": [
          {
            "comment_id": "xxx",
            "author_name": "用户A",
            "comment_text": "好看！",
            "comment_like_count": 5,
            "comment_reply_count": 2,
            "comment_time": "2026-05-10 12:00:00"
          }
        ]
      }
    ]
  }
}
```

### 回调重试机制

- 最大重试次数：3 次（可配置）
- 重试间隔：5 → 15 → 30 秒（指数退避）
- 失败记录到服务端日志

---

## 8. 错误码表

| HTTP 状态码 | 说明 |
|-------------|------|
| 200 | 成功 |
| 400 | 请求参数错误 / 任务尚未完成 |
| 404 | 任务不存在 / 结果文件不存在 |
| 422 | 请求体校验失败（Pydantic 验证错误） |
| 500 | 服务器内部错误 |

---

## 9. 完整调用流程示例

### 流程一：批量检测 + JSON 结果 + 回调

```
第1步：提交批量检测
POST /check/batch
{
  "urls": ["https://weibo.com/xxx/yyy", "https://www.douyin.com/video/zzz"],
  "mode": "both",
  "enable_comments": true,
  "callback_url": "https://my-agent.com/api/result"
}
→ 返回 {"task_id": "batch-abc123", ...}

第2步：轮询任务进度（可选，如使用回调可跳过）
GET /task/batch-abc123?log_offset=0
→ 返回 {"status": "running", "progress": 50.0, ...}

第3步：任务完成后获取结果
GET /task/batch-abc123/result/json        → JSON 响应体
GET /task/batch-abc123/result?format=json  → 下载 .json 文件
GET /task/batch-abc123/result?format=excel → 下载 .xlsx 文件

第4步：获取评论
GET /task/batch-abc123/comments            → JSON 响应体
GET /task/batch-abc123/comments/download?format=json  → 下载评论 JSON
GET /task/batch-abc123/comments/download?format=excel → 下载评论 Excel

第5步（自动）：系统 POST 到 https://my-agent.com/api/result
  → event=task_completed  → 主结果
  → event=comments_ready  → 评论数据
```

### 流程二：第三方直接调用（无前端）

```python
import httpx, time

BASE = "http://your-server:8888/api/v1"

# 1. 提交任务
resp = httpx.post(f"{BASE}/check/batch", json={
    "urls": ["https://weibo.com/xxx"],
    "mode": "both",
    "callback_url": "https://my-service.com/callback",
})
task_id = resp.json()["task_id"]

# 2. 轮询等待完成
while True:
    progress = httpx.get(f"{BASE}/task/{task_id}").json()
    if progress["status"] in ("completed", "failed"):
        break
    time.sleep(5)

# 3. 获取 JSON 结果
result = httpx.get(f"{BASE}/task/{task_id}/result/json").json()
print(result["total"], "条结果")
for item in result["results"]:
    print(f"  {item['url']} → 有效={item['is_valid']} 点赞={item['praise_count']}")
```
