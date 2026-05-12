## 目标（迁移到 MediaCrawler 二开）

把当前“多平台链接有效性 + 指标抓取 + MySQL 增量写回”的能力，迁移到别人 GitHub 拉来的爬虫项目 **MediaCrawler** 中进行二次开发，并在后续逐步增加：

- **评论抓取**：
- **AI 组件**：优先用于“接口 JSON → 字段映射/JSONPath 推断（带置信度、可回退）”，后续可扩展到评论分析与作品报告

> 集成方式（作为 MediaCrawler 新模块 / 子项目 / 适配层）？还是其他更好的方法套用MediaCrawler 项目的接口请求和签名？**根据 MediaCrawler 项目而定**，先把需求、接口契约、数据结构、测试流程写清楚，方便后续在 MediaCrawler 内部对齐实现路径。

## 数据源与写回（既有库表）

- **库表**：`db_sdga_report.bigscreen_data_test`
- **输入字段**：`url`
- **有效性写回**：`is_valid`（`0/NULL=未知`，`1=有效`，`2=无效`）
- **指标写回**（抓不到则写 `NULL`）：
  - `praise_count`（int）
  - `reply_count`（int）
  - `visit_count`（int）
  - `share_count`（int）
- **增量处理范围**：只处理 `is_valid IS NULL OR is_valid = 0` 的行
- **开关**：提供 mode 开关：`validity | metrics | both`

表结构参考：`bigscreen_data_test表结构.md`（其中 `forward_count` 是字符串，其它指标是 int）。

## 浏览器策略（不确定 MediaCrawler 项目是否自带开关）

### 运行时开关：同时支持 CDP 与 Playwright 自带 Chromium

- **CDP 模式**（连接本机 Chrome）
  - 优点：更贴近真实用户环境，通常 **更抗风控**、登录态复用更自然
  - 缺点：需要本机/服务器额外管理 Chrome 与 9222 端口；部署复杂度更高
- **Playwright 自带 Chromium**
  - 优点：部署更自洽；适合无 GUI/容器环境
  - 缺点：更像自动化浏览器，风控概率更高；部分平台指标依赖提前登录 profile

推荐：**默认本地用 CDP**，并提供 CLI/配置切换开关。

## AI 组件（火山引擎 Doubao）

### 目标（第一阶段：字段映射/JSONPath 推断）

输入：
- 平台名（douyin/weibo/xhs/...）
- 本次捕获的接口响应 JSON（或关键片段）
- 目标字段集合：`praise_count/reply_count/visit_count/share_count`

输出（严格结构化，必须可回退）：
- 每个字段的候选 JSONPath（可多个）
- 置信度（0-1）
- 解释（用来写日志/排错）

执行策略：
- AI 输出必须经过校验（值类型、范围、是否明显异常）
- 失败则回退到“配置驱动/硬规则”提取方式

### API 信息

- 模型：`Doubao-Seed-2.0-pro`
- API Key：名称:api-key-20260422143619-autourl
API Key:ark-7ee4d42b-005b-42a8-8f26-ded98ae2e6ae-28fd4


### Prompt 需求

需要两类提示词：

1) **字段映射推断 prompt**：从 JSON 里推断字段路径 + 置信度（必须输出 JSON）
2) **评论分析/作品报告 prompt（第二阶段）**：对评论做主题/情绪/风险点总结（可选，不阻塞第一阶段）

## 评论抓取

### 抓取范围

- 平台：优先覆盖主平台（抖音/快手/今日头条/B站/小红书/微博），但允许分阶段上线

### 先落地结构化表

建议最终落库采用“结构化字段”，便于查询分析；如果担心各平台字段差异，建议加 `raw_json` 兜底（可选）。

需要你后续确认两点（先在 MediaCrawler 内对齐）：
- comment_id 是否能稳定拿到（每个平台不同）
- 是否需要二级评论（MediaCrawler 支持二级评论能力）

### 建表 SQL（草案，后续可按 MediaCrawler 命名规范调整）

> 目标：支持多平台、去重、按作品查询、按时间分页；
>
> 落库位置：**放在你现有的 `db_sdga_report` 库里**（与 `bigscreen_data_test` 同库，方便联查与权限管理）。

```sql
CREATE TABLE IF NOT EXISTS db_sdga_report.bigscreen_content_comments (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  source_platform VARCHAR(32) NOT NULL COMMENT 'douyin/weibo/bilibili/kuaishou/toutiao_or_ixigua/xhs',
  content_url VARCHAR(500) NOT NULL COMMENT '作品原始URL（或规范化URL）',
  content_id VARCHAR(128) NULL COMMENT '平台侧作品ID（能拿到就填，用于关联/去重）',

  comment_id VARCHAR(128) NOT NULL COMMENT '平台侧评论ID（用于去重）',
  parent_comment_id VARCHAR(128) NULL COMMENT '父评论ID（二级评论时使用）',
  root_comment_id VARCHAR(128) NULL COMMENT '根评论ID（多级评论时使用）',

  author_id VARCHAR(256) NULL,
  author_name VARCHAR(256) NULL,
  author_home_url VARCHAR(500) NULL,

  comment_text TEXT NULL,
  comment_like_count INT NULL,
  comment_reply_count INT NULL,
  comment_time DATETIME NULL,

  crawl_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  raw_json JSON NULL,

  UNIQUE KEY uk_platform_comment (source_platform, comment_id),
  KEY idx_content (source_platform, content_id),
  KEY idx_url (source_platform, content_url(191)),
  KEY idx_time (comment_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='多平台作品评论（结构化）';
```

说明：
- `uk_platform_comment`：避免重复写入（重跑/断点续跑也安全）
- `idx_url`：以 URL 为主键关联时能查得快（MySQL 对长 varchar 建索引用前缀）
- `raw_json`：可选但推荐，便于未来字段扩展与 AI 分析
 - 建议给 `root@%` 或实际运行账号授予 `db_sdga_report` 下该表的 `SELECT/INSERT/UPDATE` 权限（若评论仅追加写入，可只给 `SELECT/INSERT`）

## 测试与交付（两轮）

统一用 `example_url.txt`中的 URL 做测试：

### 第一轮：只测“点赞量/评论数/访问/分享数”是否能抓到

- 先跑 file 模式，输出到本地 CSV/JSON 预览
- 指标命中 OK 后，再开 DB 写回（dry-run → 真写）

### 第二轮：再加评论抓取

- 先落本地文件（CSV/JSONL）验证抓取质量与去重
- 再落库（用新建评论表）

## 需要补齐的文档（最终交付物）

1) **中文使用手册**：安装/配置/登录态/运行命令/常见问题
2) **逻辑链路文档**：核心文件与流程图（validity vs metrics vs comments）
3) **风控优化说明**：降低风控的策略与开关（CDP、并发、节流、缓存、重试）
4) **并发与风控关系说明**：影响“能开多少窗口/上下文”的因素与推荐配置

## 需要输出的 Prompt（第一阶段：字段映射推断）

### 输入/输出约束

输入建议包含：
- `platform`: string
- `target_fields`: array[string]
- `response_samples`: array[{url,status,json_fragment}]
- `notes`: string（例如“这些字段在 statistics 里”这样的提示）

每个字段至少给一个候选路径：

```json
{
  "mappings": {
    "praise_count": [
      {"path": "data.aweme_detail.statistics.digg_count", "confidence": 0.86, "reason": "field name digg_count matches likes"}
    ],
    "reply_count": [
      {"path": "data.aweme_detail.statistics.comment_count", "confidence": 0.83, "reason": "comment_count matches replies"}
    ]
  },
  "warnings": ["..."]
}
```

### 推荐的使用方式（落地要点）

- AI 只负责“建议路径”，最终仍由程序执行 `_deep_get` 去取值并做类型校验
- 若 AI 给出的路径取不到值（None）或值明显异常，则忽略该建议并回退到现有配置/规则

## 安全与配置（必须写进迁移方案）

- API Key、DB 密码、Cookie 等敏感信息 **只能通过环境变量/配置注入**，严禁写进仓库
- 所有 AI 调用必须有：超时、重试上限、最大输入长度截断、以及成本监控（每次调用记录 token 估算）