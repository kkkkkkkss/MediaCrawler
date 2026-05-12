# url_check 模式使用手册

## 功能概述

`url_check` 是在 MediaCrawler 基础上二次开发的新爬取模式。它从外部 MySQL 数据库（`db_sdga_report.bigscreen_data_test`）读取作品 URL，自动识别所属平台，调用对应平台接口获取作品详情，通过 AI（Doubao）解析 JSON 提取指标，最终写回数据库。

## 前置依赖

### 1. Python 环境

- Python >= 3.11
- uv 包管理器（已安装）
- 安装项目依赖：`uv sync`

### 2. Node.js

- 版本 >= 16.0.0（抖音/知乎签名需要）

### 3. 浏览器

- 推荐使用 CDP 模式（连接已有的 Chrome 浏览器）
- Chrome 版本 >= 144
- 在 Chrome 地址栏输入 `chrome://inspect/#remote-debugging` 开启远程调试

### 4. 外部数据库

- 确保 `db_sdga_report.bigscreen_data_test` 表可访问
- 如需评论功能，执行 `schema/create_comments_table.sql` 创建评论表

## 配置说明

### .env 文件配置

将 `.env.example` 复制为 `.env` 并修改以下关键配置：

```bash
# 外部业务库连接（url_check 模式专用）
EXT_MYSQL_HOST=123.158.253.65
EXT_MYSQL_PORT=30148
EXT_MYSQL_USER=root
EXT_MYSQL_PWD=你的密码
EXT_MYSQL_DB=db_sdga_report

# Doubao AI 配置
DOUBAO_API_KEY=你的API Key
DOUBAO_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
```

### config/base_config.py 配置项

```python
# url_check 模式配置
URLCHECK_MODE = "both"           # validity(仅检测有效性) | metrics(仅抓指标) | both(同时)
URLCHECK_BATCH_SIZE = 15         # 每批处理 URL 数量
URLCHECK_ENABLE_COMMENTS = False # 是否同时抓评论
URLCHECK_MAX_COMMENTS = 10     # 单作品评论上限

# 浏览器模式（推荐 CDP）
ENABLE_CDP_MODE = True
CDP_CONNECT_EXISTING = True
```

## 运行方式

### 基本命令

```bash
# 检测有效性 + 抓取指标（默认模式）
uv run main.py --type url_check

# 仅检测有效性
uv run main.py --type url_check --urlcheck_mode validity

# 抓取指标 + 评论
uv run main.py --type url_check --urlcheck_mode both --urlcheck_comments true

# 默认 AI 解析（完整JSON传AI）
uv run main.py --type url_check --urlcheck_mode both

# 手动切硬编码（不调AI，零成本）
uv run main.py --type url_check --urlcheck_mode both --extract_mode hardcode

```

### 运行流程

1. 程序从 `bigscreen_data_test` 表读取 `is_valid IS NULL OR is_valid = 0` 的 URL
2. 自动识别每个 URL 所属平台（抖音/B站/快手/头条等）
3. 按平台分组，依次处理每个平台的 URL
4. 对每个 URL 先做 **HTTP 预检**（跟随重定向看是否跳转到错误页），大部分失效链接在此层秒判
5. HTTP 不确定的才启动浏览器 → 检测页面 DOM 关键词 → 调接口获取 JSON → AI 解析指标 → 写回
6. 完成后关闭浏览器，处理下一个平台

## 支持的平台


| 平台    | URL 示例                        | 指标抓取 | 评论抓取 |
| ----- | ----------------------------- | ---- | ---- |
| 抖音    | `douyin.com/video/xxx`        | 支持   | 支持   |
| B站    | `bilibili.com/video/BVxxx`    | 支持   | 支持   |
| 快手    | `kuaishou.com/f/xxx`          | 支持   | 支持   |
| 头条/西瓜 | `toutiao.com/ixxx`            | 支持   | 支持   |
| 小红书   | `xiaohongshu.com/explore/xxx` | 需扩展  | 需扩展  |
| 微博    | `weibo.com/xxx`               | 支持   | 支持   |


## 写回字段说明

### bigscreen_data_test 表


| 字段           | 含义                          | 来源                     |
| ------------ | --------------------------- | ---------------------- |
| is_valid     | 作品有效性（1=有效, 2=无效/已删除/私密/违规） | HTTP预检+DOM关键词+接口字段三层检测 |
| praise_count | 点赞数                         | AI 从接口 JSON 中提取        |
| reply_count  | 评论数                         | AI 从接口 JSON 中提取        |
| visit_count  | 播放/浏览数                      | AI 从接口 JSON 中提取        |
| share_count  | 分享数                         | AI 从接口 JSON 中提取        |


### AI 字段提取机制

1. 调用平台接口获取完整的 JSON 响应
2. 将 JSON 传给 Doubao AI，让 AI 识别并提取四个指标字段
3. 对 AI 返回值进行类型和范围校验
4. 如果 AI 提取失败，自动回退到硬编码映射（`tools/fallback_field_map.py`）

## 常见问题

### Q: 运行后提示"没有待处理的 URL"？

A: 检查 `bigscreen_data_test` 表中是否有 `is_valid IS NULL OR is_valid = 0` 的记录。

### Q: 某个平台始终获取失败？

A: 检查该平台的登录状态。建议先用 MediaCrawler 原生模式手动测试一次登录。

### Q: AI 提取结果不准确？

A: 可以查看日志中 AI 的提取结果和 token 消耗。如果某平台 AI 准确率低，可以在 `tools/fallback_field_map.py` 中更新硬编码映射。

### Q: 如何只处理特定平台的 URL？

A: 目前 url_check 模式会自动识别并处理所有平台。你可以在数据库中通过 URL 筛选来控制。

### Q: 头条/西瓜视频获取详情失败？

A: 头条的 SSR 数据提取方式可能因页面结构变化而需要更新。如果持续失败，可以手动访问该 URL 确认页面是否正常加载。