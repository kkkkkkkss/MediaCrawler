# 抖音单作品链接爬取：项目逻辑与完整链路（以 `https://www.douyin.com/video/7628682927572997561` 为例）

本文基于本仓库当前实现，聚焦“爬取一条抖音作品链接（detail 模式）”时，从入口到请求、解析、存储/下载的**完整链路**，并说明“爬完数据会放到哪里”。

---

## 入口与运行方式

### 入口文件

- 统一入口：`main.py`
- 爬虫工厂：`CrawlerFactory` 会根据 `config.PLATFORM` 创建对应平台爬虫
  - 抖音平台 key：`dy`
  - 对应类：`media_platform/douyin/core.py` 中的 `DouYinCrawler`

### 以“单作品链接”运行（detail 模式）

抖音单作品属于 **detail** 类型（读取 `config/dy_config.py` 的 `DY_SPECIFIED_ID_LIST`）。

关键配置位于：

- `config/base_config.py`
  - `PLATFORM = "dy"`
  - `CRAWLER_TYPE = "detail"`
  - `SAVE_DATA_OPTION = "jsonl"`（默认）
  - `SAVE_DATA_PATH = ""`（为空则落到项目内 `data/` 目录）
  - `ENABLE_GET_COMMENTS = True`（默认抓评论）
  - `ENABLE_GET_SUB_COMMENTS = False`（默认不抓二级评论）
  - `ENABLE_GET_MEIDAS = False`（默认不下载媒体文件）
- `config/dy_config.py`
  - 将你的作品链接加入 `DY_SPECIFIED_ID_LIST`：
    - `https://www.douyin.com/video/7628682927572997561`

---

## 总体链路概览（detail 模式）

从 `main.py` 启动到落地存储，大致分为 6 段：

1. **启动与选择平台爬虫**
2. **启动浏览器上下文（CDP 或标准 Playwright）并确保登录态**
3. **创建 `DouYinClient`（拿到 UA、Cookie，并具备签名能力）**
4. **从作品链接解析出 `aweme_id`**
5. **请求作品详情与评论列表（可选子评论）**
6. **写入存储（jsonl/csv/json/db/sqlite/mongodb/excel）+（可选）下载媒体**

下面逐段展开。

---

## 1）启动与平台选择

入口：`main.py`

- `cmd_arg.parse_cmd()` 解析命令行参数后会落到 `config`（本仓库习惯用 `config/base_config.py` 作为默认配置源）
- `CrawlerFactory.create_crawler(platform=config.PLATFORM)` 选择抖音爬虫：`DouYinCrawler`
- `await crawler.start()` 进入平台爬虫主流程

---

## 2）浏览器启动与登录态（反爬关键）

实现：`media_platform/douyin/core.py` → `DouYinCrawler.start()`

### 2.1 浏览器启动模式

由 `config.ENABLE_CDP_MODE` 决定：

- **CDP 模式（默认开启）**：`launch_browser_with_cdp()`
  - 连接/启动本机 Chrome/Edge，通过 CDP 控制，复用真实浏览器环境（Cookie、扩展、历史等），**更抗风控**
- **标准 Playwright 模式**：`launch_browser()`
  - 会注入 `libs/stealth.min.js` 增强反检测

启动后：

- `self.context_page = await self.browser_context.new_page()`
- `await self.context_page.goto("https://www.douyin.com")`

### 2.2 登录态判断与登录

`DouYinClient.pong()` 用于判断是否已登录（本地存储或 Cookie 的 `LOGIN_STATUS`）。

未登录则创建 `DouYinLogin` 走二维码/手机号/cookie 登录（取决于 `config.LOGIN_TYPE`），随后调用：

- `DouYinClient.update_cookies(...)` 将浏览器上下文中的 Cookie 同步回 API Client 的请求头

---

## 3）`DouYinClient`：请求参数补齐 + `a_bogus` 签名

实现：`media_platform/douyin/client.py`

### 3.1 请求公共参数

每次 `get()` / `post()` 前会进入 `__process_req_params()`，自动注入常见参数（示例）：

- `aid=6383`、`device_platform=webapp`、`channel=channel_pc_web`
- `webid = get_web_id()`
- `msToken = window.localStorage["xmst"]`

### 3.2 `a_bogus` 计算方式

除搜索接口以外（代码中排除了 `"/v1/web/general/search"` 相关 URI），会计算并附加：

- `a_bogus = get_a_bogus(...)`

其实现位于 `media_platform/douyin/help.py`：

- 通过 `execjs` 加载 `libs/douyin.js`
- 调用 JS 方法生成签名：`sign_datail` / `sign_reply`

这也是 README 中“无需 JS 逆向算法、通过 JS 表达式获取签名参数”的核心体现。

---

## 4）作品链接解析为 `aweme_id`

入口：`DouYinCrawler.get_specified_awemes()`

配置来源：`config.DY_SPECIFIED_ID_LIST`

解析函数：`media_platform/douyin/help.py` → `parse_video_info_from_url(url)`

支持格式（简化总结）：

- 纯作品链接：`https://www.douyin.com/video/<aweme_id>`
- 带 `modal_id` 的链接：`...?modal_id=<aweme_id>`
- 短链：`https://v.douyin.com/.../`（需要先 resolve 重定向）
- 纯数字 id：`<aweme_id>`

对短链会调用 `DouYinClient.resolve_short_url()` 获取跳转后的完整 URL，再重新解析 `aweme_id`。

> 你的示例链接 `https://www.douyin.com/video/7628682927572997561` 属于标准作品链接，可直接解析出 `aweme_id = 7628682927572997561`。

---

## 5）请求作品详情与评论

### 5.1 拉取作品详情（aweme detail）

并发控制：`asyncio.Semaphore(config.MAX_CONCURRENCY_NUM)`

接口调用：`DouYinClient.get_video_by_id(aweme_id)`：

- `GET https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=<id>&...&a_bogus=...`
- 返回后取 `res["aweme_detail"]`

拿到 `aweme_detail` 后会做两件事：

1) **落地“作品内容数据”**：`store/douyin/__init__.py` → `update_douyin_aweme(aweme_item)`

2) **可选下载媒体**：`DouYinCrawler.get_aweme_media(aweme_item)`
   - `ENABLE_GET_MEIDAS=False` 时跳过
   - 否则自动判断“图文 or 视频”

### 5.2 拉取评论（comment list / reply list）

在 detail 流程末尾会调用：

- `DouYinCrawler.batch_get_note_comments(aweme_id_list)`

其内部会根据配置开关决定是否抓取：

- `config.ENABLE_GET_COMMENTS = True` 才会执行
- 每个作品会进入 `DouYinCrawler.get_comments(aweme_id, semaphore)`
  - 调用 `DouYinClient.get_aweme_all_comments(...)`
  - 回调函数为 `store/douyin/__init__.py` → `batch_update_dy_aweme_comments`

对应接口（由 `media_platform/douyin/client.py` 发起）：

- 一级评论：
  - `GET https://www.douyin.com/aweme/v1/web/comment/list/?aweme_id=<id>&cursor=<cursor>&count=20&item_type=0&...&a_bogus=...`
- 二级评论（仅在 `ENABLE_GET_SUB_COMMENTS=True` 时触发）：
  - `GET https://www.douyin.com/aweme/v1/web/comment/list/reply/?item_id=<aweme_id>&comment_id=<cid>&cursor=<cursor>&count=20&item_type=0&...&a_bogus=...`

> 备注：代码里会根据 `max_count=config.CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES` 控制单作品最多抓多少条评论（包含子评论时会把子评论也计入结果数组）。

---

## 6）数据写到哪里（保存选项与落地路径）

本项目对“内容/评论/创作者”三类数据采用同一套存储抽象：

- 抖音 store 工厂：`store/douyin/__init__.py` → `DouyinStoreFactory.create_store()`
- 由 `config.SAVE_DATA_OPTION` 决定具体实现（csv/json/jsonl/db/sqlite/mongodb/excel…）

### 6.1 默认（`SAVE_DATA_OPTION="jsonl"`）的落地位置

写文件实现：`tools/async_file_writer.py` → `AsyncFileWriter`

- **根目录**：
  - 若 `SAVE_DATA_PATH` 为空：落到项目内 `data/`
  - 若 `SAVE_DATA_PATH` 非空：落到 `<SAVE_DATA_PATH>/`
- **平台目录**：抖音为 `douyin`
- **按文件类型分目录**：`jsonl/`、`json/`、`csv/` 等
- **文件命名**：
  - `"{crawler_type}_{item_type}_{YYYY-MM-DD}.{file_type}"`

以 detail 模式、jsonl 保存为例（`SAVE_DATA_PATH` 为空）：

- 作品内容（contents）：
  - `data/douyin/jsonl/detail_contents_<当天日期>.jsonl`
- 评论（comments）：
  - `data/douyin/jsonl/detail_comments_<当天日期>.jsonl`
- 创作者（creators，仅 creator 模式会写）：
  - `data/douyin/jsonl/detail_creators_<当天日期>.jsonl`（如果在 detail 模式不抓 creator，这个文件可能不会生成）

> JSONL 是“每行一个 JSON 对象”的追加写入格式，适合大量数据持续写入。

### 6.2 内容与评论写入字段来源（抖音）

内容（作品）字段映射：`store/douyin/__init__.py` → `update_douyin_aweme(aweme_item)`

- 会把 `aweme_detail` 中的作者信息、统计信息、封面、下载链接等抽成结构化字段，例如：
  - `aweme_id`、`title/desc`、`create_time`
  - `user_id/sec_uid/nickname/avatar`
  - `liked_count/comment_count/share_count/...`
  - `aweme_url`（形如 `https://www.douyin.com/video/<aweme_id>`）
  - `cover_url`、`video_download_url`
  - `note_download_url`（图文多图 URL 用 `,` 拼接）

评论字段映射：`store/douyin/__init__.py` → `update_dy_aweme_comment(aweme_id, comment_item)`

- 包含：
  - `comment_id`、`aweme_id`、`content`
  - `create_time`、`ip_location`
  - 评论作者信息（`user_id/sec_uid/nickname/avatar`）
  - `sub_comment_count`、`like_count`
  - `parent_comment_id`
  - `pictures`（评论图片 URL，用 `,` 拼接）

### 6.3 可选：媒体文件（视频/图片）下载后放哪里

触发条件：`config.ENABLE_GET_MEIDAS = True`

实现入口：`DouYinCrawler.get_aweme_media()` → `store/douyin/douyin_store_media.py`

若 `SAVE_DATA_PATH` 为空，默认落地到项目内：

- **视频**：
  - `data/douyin/videos/<aweme_id>/video.mp4`
- **图文图片**（按 3 位序号命名）：
  - `data/douyin/images/<aweme_id>/000.jpeg`
  - `data/douyin/images/<aweme_id>/001.jpeg`
  - ...

若 `SAVE_DATA_PATH` 非空，则变为：

- `"<SAVE_DATA_PATH>/douyin/videos/<aweme_id>/video.mp4"`
- `"<SAVE_DATA_PATH>/douyin/images/<aweme_id>/<nnn>.jpeg"`

### 6.4 其他保存方式（简述）

抖音对应实现位于：`store/douyin/_store_impl.py`

- `SAVE_DATA_OPTION="csv"`：写到 `data/douyin/csv/`（或 `SAVE_DATA_PATH/douyin/csv/`）
- `SAVE_DATA_OPTION="json"`：写到 `data/douyin/json/`（单文件内是数组，会反复读写，适合小数据量）
- `SAVE_DATA_OPTION="db"|"postgres"|"sqlite"`：写入关系型数据库（字段结构参考 `database/models.py` 里的 `DouyinAweme`、`DouyinAwemeComment`、`DyCreator`）
- `SAVE_DATA_OPTION="mongodb"`：写入 MongoDB（collection 前缀 `douyin_*`，后缀分 `contents/comments/creators`）
- `SAVE_DATA_OPTION="excel"`：写到 Excel（由 `store/excel_store_base.py` 统一管理，爬虫结束时 `main.py` 会触发 flush）

更完整的存储方式说明可参考：`docs/data_storage_guide.md`。

---

## 7）把示例链接跑通时，你会看到什么产物

以作品链接 `https://www.douyin.com/video/7628682927572997561` 为例，假设：

- `PLATFORM="dy"`
- `CRAWLER_TYPE="detail"`
- `SAVE_DATA_OPTION="jsonl"`
- `SAVE_DATA_PATH=""`
- `ENABLE_GET_COMMENTS=True`
- `ENABLE_GET_MEIDAS=False`

那么通常会得到：

- `data/douyin/jsonl/detail_contents_<当天日期>.jsonl`
  - 至少包含 1 行该作品的结构化详情
- `data/douyin/jsonl/detail_comments_<当天日期>.jsonl`
  - 包含该作品的评论（数量受 `CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES` 限制）

如果你把 `ENABLE_GET_MEIDAS=True`，还会额外产生：

- `data/douyin/videos/7628682927572997561/video.mp4`（视频类作品）
  - 或 `data/douyin/images/7628682927572997561/000.jpeg ...`（图文类作品）


