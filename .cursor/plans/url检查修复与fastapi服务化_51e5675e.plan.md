---
name: URL检查修复与FastAPI服务化
overview: 修复快手链接识别、头条指标抓取、微博/头条评论抓取、词云生成等BUG，新增txt文件输入源和Excel输出，然后将url_check功能封装为FastAPI服务并编写部署/使用文档。
todos:
  - id: fix-ks-link
    content: 修复快手链接识别：url_detector 增加 notice/detail 支持 + core.py 新增短链重定向解析
    status: completed
  - id: fix-toutiao-metrics
    content: 头条转赞评 DOM 提取方案：ToutiaoClient 新增 DOM 提取方法 + core.py 回退逻辑
    status: completed
  - id: fix-wb-tt-comments
    content: 微博和头条评论抓取：core.py _fetch_and_store_comments 补充 toutiao/wb case + 微博评论转换器
    status: completed
  - id: fix-wordcloud
    content: 词云生成修复：url_check 模式下收集评论文本并生成词云
    status: completed
  - id: add-txt-input
    content: 新增 txt 文件输入源：config/cmd_arg 新增参数 + core.py 读取逻辑
    status: completed
  - id: add-excel-output
    content: Excel 报表输出：新建 url_check_excel_store.py + AI/硬编码增加 author 字段
    status: completed
  - id: test-cdp
    content: 用 test_url.txt 在 CDP 模式下测试，验证修复效果并生成 Excel
    status: completed
  - id: test-playwright
    content: 用 example_url.txt 在 Playwright 无头模式下测试，生成 Excel
    status: completed
  - id: fastapi-core
    content: FastAPI 服务化：api/ 目录结构、路由、任务管理、浏览器池
    status: completed
  - id: docs-deploy
    content: 编写部署指南（Docker/systemd/K8s）
    status: completed
  - id: docs-usage
    content: 编写使用指南（CLI + API 调用示例）
    status: completed
  - id: docs-logic
    content: 编写功能逻辑指南（架构图 + 实例演示）
    status: completed
isProject: false
---

# URL检查修复与FastAPI服务化改造计划

## 阶段一：BUG修复（第1-4项）

### 1. 快手链接识别修复

**问题根因**：

- `/f/xxx` 短链提取的 ID 不是真实 `photo_id`，需要 HTTP 重定向解析
- `m.kuaishou.com/notice/detail?id=xxx` 格式未处理
- 快手短链 302 跳转后才能拿到真实 `short-video/xxx` 路径

**修改文件**：

- [tools/url_detector.py](tools/url_detector.py)：`_extract_content_id` 增加 `notice/detail?id=xxx` 的 query 参数提取
- [media_platform/url_check/core.py](media_platform/url_check/core.py)：新增 `_resolve_kuaishou_share_url` 方法（类似已有的 `_resolve_douyin_share_url`），在 `_fetch_detail` 中对快手 `/f/` 短链做重定向解析

**关键逻辑**：

```python
# url_detector.py 新增 notice/detail 处理
if platform == "ks":
    m = re.search(r"/(?:short-video|f)/([\w-]+)", path)
    if m:
        return m.group(1)
    # notice/detail?id=xxx
    m = re.search(r"[?&]id=([\w-]+)", query)
    if m:
        return m.group(1)
```

```python
# core.py 新增快手短链解析
async def _resolve_kuaishou_share_url(self, url: str) -> Optional[str]:
    # /f/ 短链 302 → /short-video/REAL_ID
    async with httpx.AsyncClient(follow_redirects=True) as client:
        resp = await client.get(url)
        m = re.search(r"/short-video/([\w-]+)", str(resp.url))
        return m.group(1) if m else None
```

### 2. 头条转赞评数 DOM 提取

**问题根因**：头条 SSR 数据中不一定包含标准指标字段，`get_article_info` 返回的 JSON 缺少 `digg_count` 等。

**方案**：用 Playwright 打开头条文章页面后，从 DOM 中提取页面可见的点赞数、评论数等。

**修改文件**：

- [media_platform/toutiao/client.py](media_platform/toutiao/client.py)：新增 `get_article_metrics_from_dom(page, item_id)` 方法
- [media_platform/url_check/core.py](media_platform/url_check/core.py)：在 `_process_single_url` 中，对头条平台在 API 提取指标为空时回退到 DOM 提取

**DOM 提取思路**：

- 头条页面上的交互按钮（点赞、评论、收藏等）旁边通常有数字文本
- 使用 `page.evaluate()` 遍历页面上包含数字的关键 DOM 元素
- 匹配 `点赞`/`评论`/`播放`/`转发` 等关键词附近的数字

### 3. 微博与头条评论抓取

**问题根因**：[core.py](media_platform/url_check/core.py) 的 `_fetch_and_store_comments` 方法只处理了 `dy`、`bili`、`ks` 三个平台，缺少 `toutiao` 和 `wb`。

**修改文件**：[media_platform/url_check/core.py](media_platform/url_check/core.py)

**新增代码**（在 `_fetch_and_store_comments` 中追加）：

```python
elif platform == "toutiao":
    comments = await client.get_all_comments(
        item_id=content_id,
        crawl_interval=config.CRAWLER_MAX_SLEEP_SEC,
        max_count=max_comments,
    )
    await store_comments_to_external_db(platform, content_id, content_url, comments)

elif platform == "wb":
    comments = await client.get_note_all_comments(
        note_id=content_id,
        crawl_interval=config.CRAWLER_MAX_SLEEP_SEC,
        max_count=max_comments,
    )
    await store_comments_to_external_db(platform, content_id, content_url, comments)
```

同时在 [store/url_check_comment_store.py](store/url_check_comment_store.py) 中确认 `_PLATFORM_CONVERTERS` 已包含 `wb` 转换器（当前缺少，需新增）。

### 4. 词云生成修复

**问题根因**（三个叠加问题）：

1. [main.py](main.py) 中 `_generate_wordcloud_if_needed` 检查的是 `config.ENABLE_GET_COMMENTS`，但 url_check 模式使用 `URLCHECK_ENABLE_COMMENTS` 控制评论
2. `AsyncFileWriter` 使用 `config.PLATFORM`（url_check 模式不设置单一平台）
3. url_check 的评论写入外部 DB 或内存收集，不走 jsonl 文件存储

**修改文件**：

- [main.py](main.py)：`_generate_wordcloud_if_needed` 增加 url_check 模式的特殊处理分支
- [media_platform/url_check/core.py](media_platform/url_check/core.py)：在 UrlCheckCrawler 中收集评论文本，处理完成后统一生成词云

**方案**：在 `UrlCheckCrawler.start()` 完成后，收集本次抓取到的所有评论文本（从内存/外部DB查询），调用 `AsyncWordCloudGenerator` 生成词云。

---

## 阶段二：新增 txt 输入源 + Excel 输出（第5项）

### 5a. 新增 txt 文件输入源

**新增模块**：在 `UrlCheckCrawler` 中支持从 txt 文件读取 URL（每行一个URL），与外部 DB 输入源并列。

**修改文件**：

- [config/base_config.py](config/base_config.py)：新增 `URLCHECK_INPUT_SOURCE` 配置（"db" / "file"）和 `URLCHECK_INPUT_FILE` 配置
- [cmd_arg/arg.py](cmd_arg/arg.py)：新增 `--urlcheck_source` 和 `--urlcheck_file` 命令行参数
- [media_platform/url_check/core.py](media_platform/url_check/core.py)：`start()` 方法根据 input_source 选择从文件或DB读取URL

**txt 输入流程**：

1. 逐行读取 URL，自动分配递增 ID
2. 复用 `group_urls_by_platform` 分组
3. 处理结果写入 Excel（不写回DB）

### 5b. Excel 报表输出

**输出字段（中文列名）**：


| 英文字段         | 中文列名   | 说明             |
| ------------ | ------ | -------------- |
| id           | 序号     | 自增或DB行ID       |
| type         | 内容类型   | 视频/图文/微博/文章    |
| web_name     | 平台名称   | 抖音/快手/B站/微博/头条 |
| author       | 作者     | 从API/AI提取      |
| praise_count | 点赞数    | -              |
| reply_count  | 评论数    | -              |
| visit_count  | 播放/浏览数 | -              |
| share_count  | 分享数    | -              |
| is_valid     | 链接有效性  | 1=有效, 2=无效     |
| url          | 原始链接   | -              |


**新增文件**：`store/url_check_excel_store.py` -- 专用于 url_check 模式的 Excel 输出。

**修改文件**：

- [media_platform/url_check/core.py](media_platform/url_check/core.py)：处理完成后调用 Excel 输出
- AI 字段映射增加 `author` 字段提取
- [tools/ai_field_mapper.py](tools/ai_field_mapper.py)：`TARGET_FIELDS` 增加 `"author"`
- [tools/fallback_field_map.py](tools/fallback_field_map.py)：各平台增加 `author` 硬编码路径

**内容类型判断逻辑**：

- 抖音/快手/B站 → "视频"
- 头条 → "视频"或"文章"（从返回数据的 `type` 字段判断）
- 微博 → "微博"
- 小红书 → "笔记"

---

## 阶段三：Playwright 无头模式测试（第6项）

用 `ENABLE_CDP_MODE=False` + `HEADLESS=True` 运行 `example_url.txt` 的全量测试，确认：

- 快手 `/f/` 短链和 `notice/detail` 链接正常解析
- 头条 DOM 指标提取正常
- 微博和头条评论抓取正常
- Excel 正常输出
- 词云正常生成

如有 Playwright 特有问题（如反爬检测）需针对性修复。

---

## 阶段四：FastAPI 服务化改造（第7项）

**新增文件**：`api/` 目录

```
api/
  __init__.py
  app.py          # FastAPI 应用主入口
  routes.py       # 路由定义
  schemas.py      # Pydantic 请求/响应模型
  task_manager.py # asyncio 后台任务管理
  dependencies.py # 依赖注入
```

### API 端点设计

- `POST /api/v1/check/url` -- 单链接检测，返回即时结果
- `POST /api/v1/check/batch` -- 批量URL检测（JSON body 传 URL列表），返回 task_id
- `POST /api/v1/check/upload` -- 上传 Excel 文件，指定链接列名，返回 task_id
- `POST /api/v1/check/mysql` -- 指定 MySQL 连接信息和表名/列名，返回 task_id
- `GET /api/v1/task/{task_id}` -- 查询任务进度和状态
- `GET /api/v1/task/{task_id}/result` -- 下载结果 Excel
- `GET /api/v1/health` -- 健康检查

### 任务管理

- 使用 `asyncio.Queue` + 后台 worker 协程
- 任务状态存储在内存 dict 中（`task_id -> TaskStatus`）
- `TaskStatus` 包含：状态（pending/running/completed/failed）、进度百分比、结果文件路径、错误信息

### 浏览器池

- FastAPI 启动时初始化 Playwright（无头模式）
- 使用浏览器上下文池，避免每次请求都启动新浏览器
- 通过 `app.lifespan` 管理浏览器生命周期

---

## 阶段五：文档编写（第8项）

### 文档清单

1. `docs/部署指南.md` -- 包含：
  - Docker 部署方案（Dockerfile + docker-compose.yml）
  - systemd 直接部署方案
  - K8s 部署要点（Deployment YAML 示例、Service、ConfigMap）
  - 环境变量说明（.env 配置项）
  - Playwright 浏览器依赖安装
2. `docs/使用指南.md` -- 包含：
  - 命令行模式完整参数说明
  - API 接口调用示例（curl / Python requests）
  - 输入格式说明（txt/Excel/MySQL）
  - 输出说明（Excel 字段解释）
3. `docs/功能逻辑指南.md` -- 包含：
  - 整体架构图（Mermaid）
  - URL 检测三层机制详解（HTTP预检 → DOM检测 → API字段检查）
  - 各平台适配说明（快手短链解析、头条DOM提取等）
  - FastAPI 任务处理流程
  - 实例演示：从输入一个抖音短链到最终 Excel 输出的完整链路

