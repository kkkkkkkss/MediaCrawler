# url_check 模式逻辑链路

## 完整流程图

```
┌─────────────────────────────────────────────────────────────────────┐
│                       main.py --type url_check                       │
│                               │                                      │
│                    CrawlerFactory.create_crawler("url_check")        │
│                               │                                      │
│                    UrlCheckCrawler.start()                            │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
                    ┌───────────▼──────────────┐
                    │ 1. ExternalDB             │
                    │    fetch_pending_urls()   │
                    │    从 bigscreen_data_test │
                    │    读取待处理 URL          │
                    └───────────┬──────────────┘
                                │
                    ┌───────────▼──────────────┐
                    │ 2. url_detector           │
                    │    group_urls_by_platform │
                    │    自动识别+按平台分组     │
                    │    dy=3, bili=2, ks=1...  │
                    └───────────┬──────────────┘
                                │
              ┌─────────────────┼──────────────────┐
              │                 │                   │
    ┌─────────▼────┐  ┌────────▼─────┐  ┌─────────▼────┐
    │ 平台: 抖音    │  │ 平台: B站    │  │ 平台: 快手    │  ...
    │              │  │              │  │              │
    │ ①启动浏览器  │  │ ①启动浏览器  │  │ ①启动浏览器  │
    │ ②登录获取CK  │  │ ②登录获取CK  │  │ ②登录获取CK  │
    │ ③创建Client  │  │ ③创建Client  │  │ ③创建Client  │
    └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
           │                 │                  │
     ┌─────▼─────────────────▼──────────────────▼─────┐
     │            4. 逐 URL 处理循环                    │
     │                                                  │
     │   ┌──────────────────────────────────────────┐  │
     │   │ a) Client.get_video_by_id(id, raw=True)  │  │
     │   │    获取作品详情原始 JSON                   │  │
     │   └─────────────────┬────────────────────────┘  │
     │                     │                            │
     │   ┌─────────────────▼────────────────────────┐  │
     │   │ b) AIFieldMapper.extract_metrics()        │  │
     │   │    - Doubao AI 解析 JSON → 提取4个指标    │  │
     │   │    - 校验返回值（类型、范围）             │  │
     │   │    - 失败 → fallback_field_map 硬编码     │  │
     │   └─────────────────┬────────────────────────┘  │
     │                     │                            │
     │   ┌─────────────────▼────────────────────────┐  │
     │   │ c) ExternalDB.update_metrics()            │  │
     │   │    写回 is_valid + 4个指标到业务表        │  │
     │   └─────────────────┬────────────────────────┘  │
     │                     │                            │
     │   ┌─────────────────▼────────────────────────┐  │
     │   │ d) [可选] 评论抓取                        │  │
     │   │    Client.get_aweme_all_comments()        │  │
     │   │    → url_check_comment_store 转换格式     │  │
     │   │    → ExternalDB.batch_insert_comments()   │  │
     │   └──────────────────────────────────────────┘  │
     │                                                  │
     │          sleep(CRAWLER_MAX_SLEEP_SEC)            │
     │          → 处理下一个 URL                        │
     └──────────────────────────────────────────────────┘

              │
              ▼
     ┌────────────────────────────┐
     │ 5. 输出 AI 统计信息        │
     │    总调用次数 / token 消耗  │
     └────────────────────────────┘
```

## 关键模块职责

| 模块 | 路径 | 职责 |
|------|------|------|
| UrlCheckCrawler | `media_platform/url_check/core.py` | 总调度：读URL → 分组 → 逐平台处理 |
| ExternalDB | `database/external_db.py` | 外部业务库读写（与MediaCrawler自身库隔离） |
| URLPlatformDetector | `tools/url_detector.py` | URL域名→平台代码识别 + 作品ID提取 |
| AIFieldMapper | `tools/ai_field_mapper.py` | 调 Doubao AI 从 JSON 提取指标 |
| FallbackFieldMap | `tools/fallback_field_map.py` | AI 失败时的硬编码回退映射 |
| CommentStore | `store/url_check_comment_store.py` | 各平台评论格式统一转换 + 入库 |
| ToutiaoClient | `media_platform/toutiao/client.py` | 头条/西瓜API请求（SSR数据提取） |

## 数据流向

```
bigscreen_data_test (读)
    ↓ fetch_pending_urls
url_detector (识别)
    ↓ group_urls_by_platform
Platform Client (接口调用)
    ↓ raw JSON
AIFieldMapper (AI解析)
    ↓ {praise_count, reply_count, visit_count, share_count}
bigscreen_data_test (写回)

[可选] Platform Client (评论接口)
    ↓ raw comments
url_check_comment_store (格式转换)
    ↓ 标准评论格式
bigscreen_content_comments (写入)
```

## AI 调用链路

```
原始 JSON (截断到 8000 字符)
    ↓
Doubao API (OpenAI 兼容格式)
    model: doubao-seed-2.0-pro
    temperature: 0.1
    max_tokens: 256
    ↓
AI 返回 JSON: {praise_count: 1234, reply_count: 56, ...}
    ↓
校验: 类型检查 + 范围检查（负数→null）
    ↓ 失败
fallback_field_map: 按已知 JSONPath 硬编码提取
```
