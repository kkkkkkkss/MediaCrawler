# 从 `example_url.txt` 批量爬“作品转赞评数”，落地到 Excel 与 MySQL（抖音示例）

本文给出一套**测试用**操作流程：把 `example_url.txt` 里的作品链接（以抖音为例）批量爬取“作品详情字段（包含转发/点赞/评论等统计）”，并分别落地到：

- 本地 **Excel**
- **MySQL** 数据库

> 说明：`example_url.txt` 里混有多平台链接（抖音/B站/头条/快手）。本文先讲“抖音作品链接”的处理方式；其他平台可按同样思路用 `--platform` 切换。

---

## 你最终会得到什么数据

抖音“转赞评”等统计值来自抖音作品详情接口返回的 `statistics` 字段，项目会把它映射为结构化字段并存储：

- `liked_count`：点赞数
- `comment_count`：评论数
- `share_count`：分享/转发数（“转”）
- `collected_count`：收藏数（如果你也关心“赞藏评转”，它也在里面）

对应映射代码在：`store/douyin/__init__.py` 的 `update_douyin_aweme()`。

---

## 第 0 步：准备“抖音作品链接列表”

`example_url.txt` 当前内容（节选）：

- `https://www.douyin.com/video/7628682927572997561` ✅（标准格式，直接可用）
- `https://www.iesdouyin.com/share/video/7629497374305528689` ⚠️（非标准格式，需处理）

### 0.1 只保留抖音“作品 ID / 标准链接”

项目抖音解析（`media_platform/douyin/help.py` → `parse_video_info_from_url()`）**稳定支持**：

- `https://www.douyin.com/video/<aweme_id>`
- `...?modal_id=<aweme_id>`
- `https://v.douyin.com/.../`（短链，会走重定向解析）
- 纯数字：`<aweme_id>`

对 `iesdouyin.com/share/video/<aweme_id>` 这类链接，建议你直接取出 `<aweme_id>` 当作 `--specified_id` 的输入（例如上面那个就是 `7629497374305528689`）。

> 经验建议：为了省事、少踩坑，**尽量把列表统一成**“纯数字 aweme_id” 或 “`douyin.com/video/<id>`”。

---

## 第 1 步：确保能跑起来（环境/浏览器/登录）

抖音模块需要 Node.js（用于 `execjs` 执行 `libs/douyin.js` 计算 `a_bogus`）。

- Node.js：>= 16
- Python 依赖：按 README 使用 `uv sync`（推荐）或 `pip install -r requirements.txt`

运行时默认是 CDP 模式复用本机 Chrome/Edge 登录态（`config/base_config.py` 中 `ENABLE_CDP_MODE=True`）。

---

## 第 2 步：只要“转赞评数”，建议关闭评论抓取（更快）

作品的“转赞评”在 **作品详情**里就有，不依赖评论接口。

运行时建议加上：

- `--get_comment false`
- （可选）`--get_sub_comment false`

这样只会拉作品 detail，不会刷评论列表，速度和稳定性更好。

---

## 第 3A 步：落地到本地 Excel（测试用）

### 3A.1 命令行直接喂链接/ID（推荐）

本项目支持 `--specified_id` 直接覆盖配置文件里的 `DY_SPECIFIED_ID_LIST`（见 `cmd_arg/arg.py`）。

把你的抖音作品列表整理成逗号分隔（示例包含 2 条）：

```bash
uv run main.py --platform dy --type detail --lt qrcode ^
  --specified_id "7628682927572997561,7629497374305528689" ^
  --get_comment false ^
  --save_data_option excel
```

> Windows PowerShell 里也可以一行写完；如果你不想用 `^`，直接删掉换行符即可。

### 3A.2 Excel 输出在哪里

Excel 输出由 `store/excel_store_base.py` 统一管理，爬虫结束时 `main.py` 会触发 flush。

通常会落在（当 `--save_data_path` 未指定时）：

- `data/douyin/excel/`（或类似目录结构）

如果你希望明确指定目录（比如临时测试输出到 `./data_test`），可以加：

- `--save_data_path "data_test"`

例如：

```bash
uv run main.py --platform dy --type detail --lt qrcode ^
  --specified_id "7628682927572997561,7629497374305528689" ^
  --get_comment false ^
  --save_data_option excel ^
  --save_data_path "data_test"
```

---

## 第 3B 步：落地到 MySQL

### 3B.1 配置 MySQL 连接信息

数据库配置来源：`config/db_config.py`，通过环境变量读取：

- `MYSQL_DB_HOST`
- `MYSQL_DB_PORT`
- `MYSQL_DB_USER`
- `MYSQL_DB_PWD`
- `MYSQL_DB_NAME`

在 Windows PowerShell 里（当前会话生效）可以这样设置：

```powershell
$env:MYSQL_DB_HOST="127.0.0.1"
$env:MYSQL_DB_PORT="3306"
$env:MYSQL_DB_USER="root"
$env:MYSQL_DB_PWD="123456"
$env:MYSQL_DB_NAME="media_crawler"
```

也可以参考仓库提供的 `.env.example` 自行创建 `.env`，但请注意：本项目当前代码里**没有显式调用** `python-dotenv` 的 `load_dotenv()`，因此更稳妥的方式是直接在 Shell 中设置环境变量（如上）。

### 3B.2 初始化表结构

首次落库前先初始化（会按 ORM model 建表）：

```bash
uv run main.py --init_db mysql
```

### 3B.3 开始爬取并写入 MySQL

把 `--save_data_option` 设置为 `db`（MySQL 对应选项名就是 `db`）：

```bash
uv run main.py --platform dy --type detail --lt qrcode ^
  --specified_id "7628682927572997561,7629497374305528689" ^
  --get_comment false ^
  --save_data_option db
```

### 3B.4 MySQL 里写到哪些表

ORM 定义在 `database/models.py`，抖音相关表为：

- `douyin_aweme`：作品内容（包含转赞评等统计字段）
- `douyin_aweme_comment`：评论（你关闭 `--get_comment false` 时不会写入）
- `dy_creator`：创作者信息（creator 模式才会写）

---

## 常见问题（建议先看）

### 1）`example_url.txt` 里有其它平台链接怎么办？

`--specified_id` 是“平台相关”的：你运行 `--platform dy` 时，只会按抖音逻辑解析并请求抖音接口。

所以你需要：

- **要爬抖音**：只把抖音作品链接/ID 放进 `--specified_id`
- **要爬 B 站**：另起一次命令 `--platform bili`，把 B 站视频链接/ID 放到 `--specified_id`
- **要爬快手**：另起一次命令 `--platform ks`，同理

### 2）我只关心“转赞评数”，需要下载视频/图片吗？

不需要。确保：

- 不开启媒体下载：不要设置 `ENABLE_GET_MEIDAS=True`（默认就是 False）
- 只拉详情即可：保持 `--type detail`，并加 `--get_comment false`

### 3）`iesdouyin.com/share/video/<id>` 解析失败

建议直接把 `<id>` 取出来当 `--specified_id` 输入（纯数字一定能解析）。

---

## 一句话总结（推荐最小命令）

- **落 Excel（最快、测试用）**：

```bash
uv run main.py --platform dy --type detail --lt qrcode --specified_id "<逗号分隔的aweme_id或标准链接>" --get_comment false --save_data_option excel
```

- **落 MySQL**：
  - 先初始化：`uv run main.py --init_db mysql`
  - 再运行：

```bash
uv run main.py --platform dy --type detail --lt qrcode --specified_id "<逗号分隔的aweme_id或标准链接>" --get_comment false --save_data_option db
```

