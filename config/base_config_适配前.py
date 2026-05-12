# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/config/base_config.py
# GitHub: https://github.com/NanmiCoder

# ==================== 基础配置 ====================
# 爬取目标平台，可选值：xhs(小红书)|dy(抖音)|ks(快手)|bili(B站)|wb(微博)|tieba(贴吧)|zhihu(知乎)
PLATFORM = "xhs"

# 是否使用海外版小红书 (rednote.com)
# 开启后 API 接口切换为 webapi.rednote.com，Cookie 域切换为 .rednote.com
XHS_INTERNATIONAL = False

# 关键词搜索配置，多个关键词用**英文逗号**分隔
KEYWORDS = "编程副业,编程兼职"

# 登录方式，可选值：qrcode(二维码登录)|phone(手机号登录)|cookie(直接使用Cookie登录)
LOGIN_TYPE = "qrcode"

# 登录Cookie，当 LOGIN_TYPE=cookie 时，此处填写账号有效Cookie
COOKIES = ""

# 爬取业务类型，可选值：search(关键词搜索)|detail(帖子详情)|creator(创作者主页数据)
CRAWLER_TYPE = "detail"

# ==================== IP代理配置（防封禁） ====================
# 是否启用IP代理池（应对平台IP限流/封禁）
ENABLE_IP_PROXY = False

# 代理IP池的IP数量
IP_PROXY_POOL_COUNT = 2

# 代理IP服务商，可选值：kuaidaili(快代理)|wandouhttp(豌豆HTTP)
IP_PROXY_PROVIDER_NAME = "kuaidaili"

# ==================== 基础浏览器配置 ====================
# True：无头模式（不打开浏览器窗口，后台运行）
# False：打开可视化浏览器窗口（登录触发风控时，必须设为False手动过验证）
HEADLESS = False

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
SAVE_DATA_OPTION = "jsonl"

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
CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES = 10

# 是否爬取二级评论（评论的回复），默认关闭
# 旧版数据库需手动新增字段才能存储二级评论
ENABLE_GET_SUB_COMMENTS = True

# ==================== 评论词云配置 ====================
# 是否生成评论词云图
ENABLE_GET_WORDCLOUD = True

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
URLCHECK_MAX_COMMENTS = 15

# URL检查模式的输入来源："db"(从外部MySQL读取) | "file"(从本地txt文件读取)
URLCHECK_INPUT_SOURCE = "db"

# 当 URLCHECK_INPUT_SOURCE="file" 时，指定URL文件路径（每行一个URL）
URLCHECK_INPUT_FILE = ""

# ==================== 指标提取方式配置 ====================
# 提取模式开关：ai(调AI解析完整接口JSON) | hardcode(硬编码路径直接取值)
# 默认 ai — 把完整接口内容传给AI做字段映射推断
URLCHECK_EXTRACT_MODE = "ai"

# ==================== AI字段映射配置（火山引擎豆包） ====================
# 火山引擎Doubao AI模型名称（API兼容OpenAI格式，密钥从.env文件读取）
DOUBAO_MODEL = "doubao-seed-2-0-pro-260215"

# ==================== 导入各平台专属配置 ====================
from .bilibili_config import *
from .xhs_config import *
from .dy_config import *
from .ks_config import *
from .weibo_config import *
from .tieba_config import *
from .zhihu_config import *
from .toutiao_config import *