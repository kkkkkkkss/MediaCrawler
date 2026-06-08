# 全局基础配置
# 所有平台共享的通用参数；各平台专属配置在 config/{platform}_config.py

# ==================== 基础配置 ====================
# 爬取目标平台，可选值：xhs(小红书)|dy(抖音)|ks(快手)|bili(B站)|wb(微博)|tieba(贴吧)|zhihu(知乎)
# 注意：url_check 模式下此项仅用作 CDP user_data_dir 的默认值，
#       实际平台由 URL 自动识别，无需手动修改
PLATFORM = "xhs"

# 是否使用海外版小红书 (rednote.com)
XHS_INTERNATIONAL = False

# 关键词搜索配置（search 模式用），多个关键词用英文逗号分隔
KEYWORDS = "编程副业,编程兼职"

# 登录方式：qrcode(二维码)|phone(手机号)|cookie(Cookie字符串)|cookie_pool(Cookie池自动轮换)
LOGIN_TYPE = "cookie_pool"

# 登录Cookie，LOGIN_TYPE=cookie 时填写
COOKIES = ""

# 爬取业务类型：search(关键词搜索)|detail(帖子详情)|creator(创作者主页)
# url_check 模式通过 --type url_check 指定，此项不生效
CRAWLER_TYPE = "detail"

# ==================== IP代理配置（防封禁） ====================
# 是否启用IP代理池（应对平台IP限流/封禁）
ENABLE_IP_PROXY = True

# 代理IP池的IP数量。头条 8 worker 需要额外备用 IP，避免坏出口被丢弃后等待重新提取。
IP_PROXY_POOL_COUNT = 30

# 代理IP服务商，可选值：kuaidaili(快代理)|wandouhttp(豌豆HTTP)
# 头条批量 url_check 当前按“豌豆 API 提取短效 IP”设计，开启 ENABLE_IP_PROXY 后默认走豌豆。
IP_PROXY_PROVIDER_NAME = "wandouhttp"

# 豌豆 HTTP API 提取短效 IP 参数；真实 WANDOU_APP_KEY 只放 .env，不写入代码。
WANDOU_PROXY_XY = 1      # 1=http；如需 socks5 再改造 Playwright/httpx 协议格式
WANDOU_API_TYPE = 2      # JSON 返回
WANDOU_NR = 99
WANDOU_AREA_ID = 0
WANDOU_ISP = 0
WANDOU_DEFAULT_EXPIRE_SEC = 600

# ==================== 基础浏览器配置 ====================
# True：无头模式（不打开浏览器窗口，后台运行）
# False：打开可视化浏览器窗口（登录触发风控时，必须设为False手动过验证）
HEADLESS = True

# 是否保存登录状态（持久化Cookie，下次启动免重新登录）
SAVE_LOGIN_STATE = True

# ==================== CDP (Chrome DevTools Protocol) 反爬核心配置 ====================
# 是否启用CDP模式：调用本地**真实Chrome/Edge浏览器**爬取，反检测能力极强
ENABLE_CDP_MODE = False

# CDP远程调试端口（浏览器通信端口），端口被占用会自动尝试下一个
CDP_DEBUG_PORT = 9222

# 自定义浏览器安装路径（可选），为空则自动检测系统Chrome/Edge
# Windows示例: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
# macOS示例: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CUSTOM_BROWSER_PATH = ""

# CDP模式下是否启用无头模式（建议False，无头模式易被风控检测）
CDP_HEADLESS = False

# 浏览器启动超时时间（单位：秒）
BROWSER_LAUNCH_TIMEOUT = 60

# 是否连接用户**已打开**的浏览器（而非启动新浏览器）
# 反爬效果最优：直接复用用户真实浏览器的Cookie、插件、浏览记录
CDP_CONNECT_EXISTING = False

# 程序运行结束后，是否自动关闭浏览器
# False：保持浏览器打开，方便调试
AUTO_CLOSE_BROWSER = True

# ==================== 数据保存配置 ====================
# 数据保存格式，支持：csv/db/json/jsonl/sqlite/excel/postgres
# 推荐：db（自带数据去重功能）
SAVE_DATA_OPTION = "db"

# 数据保存路径，为空则默认保存到项目根目录的 data 文件夹
SAVE_DATA_PATH = ""

# 浏览器缓存文件目录（存储登录状态、缓存数据）
# %s 会自动替换为当前爬取的平台名，实现多平台缓存隔离
USER_DATA_DIR = "%s_user_data_dir"

# ==================== 爬取数量控制配置 ====================
# 爬取起始页码（默认从第1页开始爬取）
START_PAGE = 1

# 最大爬取帖子/视频数量
CRAWLER_MAX_NOTES_COUNT = 15

# 爬虫最大并发数（建议设为1，并发过高极易触发风控）
MAX_CONCURRENCY_NUM = 1

# ==================== 媒体/评论爬取配置 ====================
# 是否爬取媒体资源（图片/视频），默认关闭
ENABLE_GET_MEIDAS = False

# 是否爬取评论，默认关闭
ENABLE_GET_COMMENTS = False

# 单个视频/帖子 最大爬取一级评论数量
CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES = 30

# 是否爬取二级评论（评论的回复），默认关闭
# 旧版数据库需手动新增字段才能存储二级评论
ENABLE_GET_SUB_COMMENTS = False

# ==================== 评论词云配置 ====================
# 是否生成评论词云图
ENABLE_GET_WORDCLOUD = False

# 自定义分词规则：键=自定义短语，值=短语分组名（用于词云精准分词）
CUSTOM_WORDS = {
    "零几": "年份",
    "高频词": "专业术语",
}

# 停用词文件路径（词云生成时，过滤无意义词汇）
STOP_WORDS_FILE = "./docs/hit_stopwords.txt"

# 中文词云字体文件路径（解决词云中文乱码问题）
FONT_PATH = "./docs/STZHONGS.TTF"

# ==================== 爬取频率控制 ====================
# 爬取请求的最大间隔时间（单位：秒，降低请求频率防风控）
CRAWLER_MAX_SLEEP_SEC = 2

# ==================== SSL证书配置 ====================
# 是否禁用SSL证书验证
# 仅在使用企业代理、抓包工具（Burp Suite/mitmproxy）时设为True
# 警告：生产环境严禁开启，会导致流量暴露在攻击风险中
DISABLE_SSL_VERIFY = False

# ==================== URL检查模式配置 ====================
# URL检查工作模式：validity(仅检测链接有效性)|metrics(仅抓取数据指标)|both(同时检测+抓指标)
URLCHECK_MODE = "both"

# 每批从外部库读取的URL数量
URLCHECK_BATCH_SIZE = 15

# URL检查模式下，是否同时爬取评论
URLCHECK_ENABLE_COMMENTS = False

# URL检查模式下，单个作品最大爬取评论数
URLCHECK_MAX_COMMENTS = 30

# URL检查模式的输入来源："db"(从外部MySQL读取) | "file"(从本地txt文件读取)
URLCHECK_INPUT_SOURCE = "db"

# 多平台并行处理开关（默认开启）
# 开启后，一批链接中涉及多个平台时，会同时为每个平台启动独立浏览器并行处理
# 每个平台内仍逐条顺序爬取（单账号同时只有一个请求），不增加风控压力
# 关闭后回退为逐平台顺序处理
URLCHECK_PARALLEL_PLATFORMS = True

# ==================== 同平台多浏览器并发配置 ====================
# 每平台浏览器并发数（同平台多浏览器多账号并发）
# 实际并发 = min(配置值, 该平台可用Cookie数)，cookie_free 平台不受Cookie数量限制
# 推荐值参考：4GB内存→头条2/其他1，8GB→头条3~4/其他1~2，16GB+→头条4~5/其他2~3
PLATFORM_CONCURRENCY = {
    "dy": 3,        # 抖音：风控严格，默认单浏览器
    "bili": 3,      # B站：默认单浏览器
    "ks": 3,        # 快手：默认单浏览器
    "toutiao": 2,   # 头条：连续大批量会触发空白页/壳页，默认牺牲速度换稳定性
    "xhs": 1,       # 小红书：默认单浏览器
    "wb": 3,        # 微博：默认单浏览器
}

# 每平台 URL 间隔时间（秒），覆盖全局 CRAWLER_MAX_SLEEP_SEC
# 头条不需要Cookie，可以更快；严格平台建议 >=2s
PLATFORM_SLEEP_SEC = {
    "dy": 2,
    "bili": 2,
    "ks": 2,
    "toutiao": 2,   # 代理模式下每个 worker 独占 IP，可恢复较高吞吐
    "xhs": 2,
    "wb": 2,
}

# 单个Cookie/浏览器单批次最大处理URL数（软上限，防止单账号负载过高触发风控）
# 达到上限后该浏览器停止取新URL，剩余URL由其他浏览器继续处理
# 0 = 不限制（共享队列自然均分）
# 按平台区分：高风控平台(快手/抖音)限制50条，低风控平台不限制
MAX_URLS_PER_COOKIE = {
    "dy": 100,
    "ks": 100,
    "bili": 0,
    "wb": 0,
    "toutiao": 0,
    "xhs": 50,
}

# 单Cookie达到上限后的处理策略
# "cooldown" = 休息 COOKIE_COOLDOWN_SEC 秒后复用当前Cookie继续（适合Cookie少但任务多的场景）
# "strict"   = 释放该Cookie，让调度层换账号；无可用账号则停止并标记剩余为无效（适合高风控场景）
COOKIE_LIMIT_POLICY = "cooldown"

# cooldown 模式下，单Cookie达到上限后的冷却时间（秒）
COOKIE_COOLDOWN_SEC = 300  # 5分钟

# Cookie耗尽后调度层最多重分配的轮次
MAX_REDISTRIBUTE_ROUNDS = 3

# 不需要Cookie即可访问的平台列表
# 这些平台并发数不受Cookie数量限制，直接按 PLATFORM_CONCURRENCY 配置开浏览器
COOKIE_FREE_PLATFORMS = ["toutiao"]

# 链接检测详情/互动量专用策略：它和投诉举报分开，避免公开详情检测消耗账号池。
# none = 不分配 Cookie；account = 必须账号可用；public_detail = 只要求公开详情能力可用。
URLCHECK_DETAIL_COOKIE_FREE_PLATFORMS = ["bili", "toutiao"]
URLCHECK_DETAIL_COOKIE_PURPOSE = {
    "dy": "account",
    "ks": "public_detail",
    "wb": "public_detail",
}

# ==================== 拟人化反风控配置 ====================
# 多浏览器启动间隔（秒）：每个 Worker 在 [0, 该值] 之间随机延迟后再启动浏览器
# 避免同一 IP 同时打开多个浏览器触发风控，设为 0 则同时启动（不推荐）
BROWSER_STAGGER_MAX_SEC = 3.0

# 头条批量检测遇到空白页/App壳页/疑似风控时的冷却。
# 旧逻辑把同一个 60s 同时用于“单条二次确认”和“重建浏览器”，导致 App 壳页特别慢。
# 新逻辑拆分：单条二次确认短等，连续异常重建再稍长冷却。
URLCHECK_TOUTIAO_RISK_COOLDOWN_SEC = 10  # 兼容旧配置读取
URLCHECK_TOUTIAO_CONFIRM_DELAY_SEC = 2
URLCHECK_TOUTIAO_CONFIRM_ABNORMAL = False
URLCHECK_TOUTIAO_REBUILD_COOLDOWN_SEC = 3
URLCHECK_TOUTIAO_REBUILD_AFTER_RISK = 3
URLCHECK_TOUTIAO_NAV_TIMEOUT_MS = 8000
URLCHECK_TOUTIAO_AFTER_NAV_SLEEP_SEC = 1
# 头条移动端公开页比桌面端更少触发验证码；只作为桌面端异常/疑似误判时的兜底证据。
# 新逻辑相对旧逻辑的差异：不再只凭桌面端空白/“内容不存在”下结论，先用 m.toutiao.com 复核。
URLCHECK_TOUTIAO_MOBILE_FALLBACK = True
URLCHECK_TOUTIAO_MOBILE_FAST_VALIDITY = True
URLCHECK_TOUTIAO_MOBILE_TIMEOUT_SEC = 12

# 头条代理模式：每个 worker 独占一个豌豆 API 提取 IP，代理不足时等待，不回退直连。
URLCHECK_TOUTIAO_PROXY_CONCURRENCY = 8
URLCHECK_PROXY_MIN_TTL_SEC = 90
URLCHECK_PROXY_ACQUIRE_RETRY_INTERVAL_SEC = 10
URLCHECK_PROXY_ACQUIRE_MAX_RETRIES = 3
URLCHECK_PROXY_FAIL_CLOSED_PLATFORMS = ["toutiao"]
URLCHECK_PROXY_BAD_STREAK_THRESHOLD = 3
URLCHECK_PROXY_ROW_RETRY = 6
URLCHECK_COOKIE_ROW_RETRY = 1
# 旧逻辑每条 URL 先用 httpx 预检再走浏览器，代理批量时会额外消耗出口请求且不能确认 403/5xx。
# 新逻辑默认让头条代理 worker 直接用浏览器确认；需要排查 HTTP 层时可临时改 True。
URLCHECK_PROXY_WORKER_PRECHECK = False
# 代理本次主要解决头条出口风控；抖音等账号态平台默认不套豌豆代理，避免账号登录画像突变。
URLCHECK_GENERIC_PROXY_PLATFORMS = []

# url_check 多 worker 使用的临时浏览器 profile 会反复生成；任务结束后只清理 worker_*，
# 不触碰登录态目录，避免本地验证垃圾堆积。
URLCHECK_CLEAN_WORKER_PROFILE = True

# 请求间隔抖动比例：在 PLATFORM_SLEEP_SEC 基础上添加 ±该比例的随机偏移
# 例如 0.3 表示 ±30%，2 秒基础间隔实际为 1.4~2.6 秒随机
SLEEP_JITTER_RATIO = 0.3

# 视口尺寸随机偏移（像素）：每个浏览器视口在 1920x1080 基础上 ±该值随机微调
# 不同视口 = 不同浏览器指纹，降低平台关联识别概率
VIEWPORT_RANDOM_OFFSET = 50

# 当 URLCHECK_INPUT_SOURCE="file" 时，指定URL文件路径（每行一个URL）
URLCHECK_INPUT_FILE = ""

# ==================== 零互动检测优化（三层兜底）配置 ====================
# 各平台基准帖子：用于验证平台接口是否正常
# 当帖子转赞评全为 0 且 author/title 也为空时，用基准帖子二次验证
# 请定期检查并替换为确认有效的高热帖子 URL + content_id
PLATFORM_BENCHMARK_POSTS = {
    "dy": {"url": "https://www.douyin.com/video/7628682927572997561", "content_id": "7628682927572997561"},
    "ks": {"url": "https://www.kuaishou.com/short-video/3xrenpyd68isk2q", "content_id": "3xrenpyd68isk2q"},
    "bili": {"url": "https://www.bilibili.com/video/BV1godYBUE3f", "content_id": "BV1godYBUE3f"},
    "wb": {"url": "https://weibo.com/1987241375/QCIQEm6rM", "content_id": "1987241375"},
    "toutiao": {"url": "https://www.toutiao.com/video/7629519642230538787/", "content_id": "7629519642230538787"},
    "xhs": {"url": "https://www.xiaohongshu.com/explore/6a032226000000003502bad6", "content_id": "6a032226000000003502bad6"},
}

# 基准帖子检测结果缓存时间（秒），默认 30 分钟
BENCHMARK_CACHE_TTL_SECONDS = 1800

# ==================== 指标提取方式配置 ====================
# 提取模式开关：
#   - "hardcode_first" (默认推荐) → 硬编码优先，指标不足时自动调 AI 补充
#   - "ai_only"                   → 跳过硬编码，直接调 AI（失败回退硬编码）
#   - "hardcode_only"             → 仅硬编码，不调 AI
URLCHECK_EXTRACT_MODE = "hardcode_first"

# ==================== AI字段映射配置（火山引擎豆包） ====================
# 火山引擎Doubao AI模型名称（API兼容OpenAI格式，密钥从.env文件读取）
# DOUBAO_MODEL = "doubao-seed-2-0-pro-260215"

# ==================== 无人值守配置（Cookie池 + 异常自动切换） ====================
# 启用后跳过浏览器扫码登录，直接用预置 Cookie 访问各平台 API
ENABLE_COOKIE_POOL = True

# Cookie池来源："file"(本地JSON) | "db"(外部MySQL cookie_pool 表)
COOKIE_POOL_SOURCE = "db"

# 本地 Cookie 池文件（JSON 格式，按平台存储多组 Cookie）
COOKIE_POOL_FILE = "config/cookie_pool.json"

# Cookie 失效后是否自动切换到下一个
COOKIE_AUTO_SWITCH = True

# 同一 Cookie 累计致命失败（获取不到接口数据）多少次后标记为失效
# 设为 1：一次致命失败（如Cookie过期/被封）就立刻判定失效
COOKIE_MAX_FAILURES = 1

# ==================== Cookie 定时刷新配置 ====================
# 各平台 Cookie 自动刷新周期（秒），0 = 不自动刷新
# 快手风控最严格，86400秒 = 24小时刷一次；抖音259200秒 = 3天；其他平台604800秒 = 7天
COOKIE_REFRESH_INTERVAL = {
    "ks": 86400,        # 24小时
    "dy": 259200,       # 3天
    "bili": 604800,     # 7天
    "wb": 604800,       # 7天
    "toutiao": 604800,  # 7天
    "xhs": 259200,      # 3天
}

# ==================== 回调机制配置 ====================
# 全局回调开关（任务完成后自动 POST 结果到回调地址）
CALLBACK_ENABLED = False

# 全局默认回调地址（任务级 callback_url 可覆盖此配置）
CALLBACK_URL = ""

# 回调最大重试次数
CALLBACK_MAX_RETRIES = 3

# 回调重试间隔（秒），依次使用
CALLBACK_RETRY_INTERVALS = [5, 15, 30]

# ==================== IP代理增强配置 ====================
# 注意：ENABLE_IP_PROXY 在上方基础配置中已定义
# 以下为 url_check 模式补充的代理控制

# 单个 Cookie/IP 连续请求失败多少次后自动切换代理
PROXY_SWITCH_THRESHOLD = 3

# ==================== 举报投诉配置 ====================
# 举报时浏览器是否无头模式（False=有头，可看到浏览器操作过程，调试时建议关闭）
# ⚠ 部署到无桌面的 Linux 服务器时必须改为 True，否则浏览器无法启动
REPORT_HEADLESS = True

# 是否并行举报（True=同一链接的多个Cookie同时开浏览器举报，False=严格串行逐个执行）
REPORT_PARALLEL = True

# 并行模式下最大同时浏览器数（防止内存爆炸，建议 <= CPU核心数）
REPORT_MAX_CONCURRENCY = 3

# 举报操作间隔秒数范围（随机取值 + SLEEP_JITTER_RATIO 抖动，防风控）
REPORT_INTERVAL_SEC = (5, 10)

# 单次举报操作超时（秒），超时后截图并标记失败
# 抖音等平台需要等弹窗关闭+内容加载，30秒可能不够
REPORT_TIMEOUT_SEC = 60

# 举报失败后重试次数（0=不重试，1=失败后刷新页面重试一次）
REPORT_RETRY_COUNT = 1

# ==================== 导入各平台专属配置 ====================
from .bilibili_config import *
from .xhs_config import *
from .dy_config import *
from .ks_config import *
from .weibo_config import *
from .tieba_config import *
from .zhihu_config import *
from .toutiao_config import *
