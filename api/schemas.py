# -*- coding: utf-8 -*-
# Pydantic 请求/响应数据模型
# 为 FastAPI Swagger 自动文档提供结构化的请求体/响应体定义

from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class CheckMode(str, Enum):
    """检测模式枚举"""
    VALIDITY = "validity"   # 仅检测有效性（链接是否可访问）
    METRICS = "metrics"     # 仅提取指标（点赞、评论、播放等）
    BOTH = "both"           # 同时检测有效性+提取指标（推荐）


class ResultFormat(str, Enum):
    """结果返回格式枚举"""
    EXCEL = "excel"  # Excel 文件（默认）
    JSON = "json"    # JSON 格式


class SingleUrlRequest(BaseModel):
    """单链接检测请求"""
    url: str = Field(
        ...,
        description="要检测的URL（支持抖音/快手/B站/微博/头条）",
        json_schema_extra={"example": "https://www.douyin.com/video/7628682927572997561"}
    )
    mode: CheckMode = Field(
        default=CheckMode.BOTH,
        description="检测模式：validity=仅有效性, metrics=仅指标, both=两者都做"
    )
    enable_comments: bool = Field(
        default=False,
        description="是否同时抓取评论数据（会增加耗时）"
    )
    callback_url: Optional[str] = Field(
        default=None,
        description="任务完成后的回调地址（可选，不传则使用全局配置或不回调）"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [{
                "url": "https://www.douyin.com/video/7628682927572997561",
                "mode": "both",
                "enable_comments": False
            }]
        }
    }


class BatchUrlRequest(BaseModel):
    """批量URL检测请求 — 直接传入URL列表"""
    urls: List[str] = Field(
        ...,
        min_length=1,
        description="URL列表，支持多平台混合。客户可在此数组中自由添加/删除/修改网址"
    )
    mode: CheckMode = Field(
        default=CheckMode.BOTH,
        description="检测模式：validity=仅有效性, metrics=仅指标, both=两者都做"
    )
    enable_comments: bool = Field(
        default=False,
        description="是否同时抓取评论数据"
    )
    callback_url: Optional[str] = Field(
        default=None,
        description="任务完成后的回调地址（可选）"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [{
                "urls": [
                    "https://www.douyin.com/video/7628682927572997561",
                    "https://www.kuaishou.com/short-video/3xrenpyd68isk2q",
                    "https://www.bilibili.com/video/BV1godYBUE3f"
                ],
                "mode": "both",
                "enable_comments": False
            }]
        }
    }


class MysqlSourceRequest(BaseModel):
    """MySQL数据源检测请求 — 从指定数据库表读取URL进行批量检测"""
    host: str = Field(..., description="MySQL服务器地址", json_schema_extra={"example": "123.158.253.65"})
    port: int = Field(default=3306, description="MySQL端口", json_schema_extra={"example": 30148})
    user: str = Field(..., description="数据库用户名", json_schema_extra={"example": "root"})
    password: str = Field(..., description="数据库密码")
    database: str = Field(..., description="数据库名", json_schema_extra={"example": "db_sdga_report"})
    table: str = Field(..., description="表名（包含URL的表）", json_schema_extra={"example": "bigscreen_data_test"})
    url_column: str = Field(
        default="url",
        description="存放URL的列名，默认为'url'",
        json_schema_extra={"example": "url"}
    )
    mode: CheckMode = Field(
        default=CheckMode.BOTH,
        description="检测模式：validity/metrics/both"
    )
    enable_comments: bool = Field(
        default=False,
        description="是否同时抓取评论数据"
    )
    batch_size: int = Field(
        default=50,
        ge=1,
        le=500,
        description="每批处理的URL数量（1-500），数量越大越快但占用资源越多"
    )
    callback_url: Optional[str] = Field(
        default=None,
        description="任务完成后的回调地址（可选）"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [{
                "host": "123.158.253.65",
                "port": 30148,
                "user": "root",
                "password": "your_password",
                "database": "db_sdga_report",
                "table": "bigscreen_data_test",
                "url_column": "url",
                "mode": "both",
                "batch_size": 50
            }]
        }
    }


class TaskStatus(str, Enum):
    """异步任务状态枚举"""
    PENDING = "pending"       # 已提交，排队中
    RUNNING = "running"       # 正在执行
    COMPLETED = "completed"   # 已完成
    FAILED = "failed"         # 执行失败
    CANCELLED = "cancelled"   # 已取消


class TaskResponse(BaseModel):
    """任务创建响应 — 批量检测/文件上传/MySQL检测返回此结构"""
    task_id: str = Field(..., description="任务唯一ID，用于后续查询进度和下载结果")
    status: TaskStatus = Field(..., description="任务当前状态")
    message: str = Field(default="", description="附加消息")


class TaskProgressResponse(BaseModel):
    """任务进度查询响应"""
    task_id: str = Field(..., description="任务ID")
    status: TaskStatus = Field(..., description="任务状态: pending/running/completed/failed/cancelled")
    progress: float = Field(default=0.0, description="进度百分比 0-100")
    total: int = Field(default=0, description="总URL数量")
    processed: int = Field(default=0, description="已处理数量")
    message: str = Field(default="", description="状态消息或错误信息")
    result_file: Optional[str] = Field(default=None, description="结果文件路径（完成后可用 /task/{id}/result 下载）")
    logs: List[str] = Field(default=[], description="处理日志列表（增量拉取，配合 log_offset 参数）")
    log_total: int = Field(default=0, description="日志总条数（用于前端计算下次 log_offset）")


class UrlCheckResult(BaseModel):
    """单条URL检测结果"""
    id: int = Field(..., description="序号")
    url: str = Field(..., description="原始URL")
    platform: str = Field(default="", description="检测到的平台: dy/ks/bili/wb/toutiao")
    source_platform: str = Field(default="", description="原始来源平台；西瓜链接显示为 xigua，但检测链路仍走 toutiao")
    content_type: str = Field(default="", description="内容类型: video/article/note")
    author: str = Field(default="", description="作者名称")
    praise_count: Optional[int] = Field(default=None, description="点赞数")
    reply_count: Optional[int] = Field(default=None, description="评论数")
    visit_count: Optional[int] = Field(default=None, description="播放量/阅读量")
    share_count: Optional[int] = Field(default=None, description="转发/分享数")
    is_valid: int = Field(default=0, description="有效性: 1=有效, 2=无效, 3=不支持, 4=检测异常/待复核")
    validity_label: str = Field(default="", description="有效性中文文案")
    status_reason: str = Field(default="", description="检测说明或异常原因")


class SingleUrlResponse(BaseModel):
    """单链接检测响应"""
    result: UrlCheckResult = Field(..., description="检测结果详情")
    logs: List[str] = Field(default=[], description="处理日志（含检测方式等信息）")
    message: str = Field(default="ok", description="状态消息")


class TaskJsonResultResponse(BaseModel):
    """任务 JSON 格式结果响应"""
    task_id: str = Field(..., description="任务ID")
    total: int = Field(default=0, description="结果总数")
    completed_at: str = Field(default="", description="完成时间")
    results: List[Dict] = Field(default=[], description="检测结果列表")


class TaskCommentsResponse(BaseModel):
    """评论数据响应"""
    task_id: str = Field(..., description="任务ID")
    total_comments: int = Field(default=0, description="评论总数")
    results: List[Dict] = Field(default=[], description="按作品分组的评论列表")


class CallbackConfigResponse(BaseModel):
    """回调配置响应"""
    enabled: bool = Field(default=False, description="是否启用全局回调")
    url: str = Field(default="", description="全局回调地址")
    max_retries: int = Field(default=3, description="最大重试次数")
    retry_intervals: List[int] = Field(default=[5, 15, 30], description="重试间隔(秒)")


class CallbackConfigRequest(BaseModel):
    """回调配置更新请求"""
    enabled: Optional[bool] = Field(default=None, description="是否启用全局回调")
    url: Optional[str] = Field(default=None, description="全局回调地址")
    max_retries: Optional[int] = Field(default=None, description="最大重试次数")
    retry_intervals: Optional[List[int]] = Field(default=None, description="重试间隔(秒)")


class HealthResponse(BaseModel):
    """健康检查响应"""
    status: str = Field(default="ok", description="服务状态")
    version: str = Field(default="1.0.0", description="API版本号")


# ── Cookie 管理相关 ──

class CookieAddRequest(BaseModel):
    """添加 Cookie 请求 — 通过 API 手动录入 Cookie"""
    platform: str = Field(
        ...,
        description="平台标识: dy(抖音)/bili(B站)/ks(快手)/xhs(小红书)/wb(微博)/toutiao(头条)",
        json_schema_extra={"example": "dy"}
    )
    cookie: str = Field(
        ...,
        description="Cookie 字符串（从浏览器开发者工具复制）",
        json_schema_extra={"example": "sessionid=abc123; passport_csrf_token=xyz456;"}
    )
    note: str = Field(
        default="",
        description="备注信息（如：账号1、主号等）",
        json_schema_extra={"example": "主号"}
    )

    model_config = {
        "json_schema_extra": {
            "examples": [{
                "platform": "dy",
                "cookie": "sessionid=abc123; passport_csrf_token=xyz456;",
                "note": "主号"
            }]
        }
    }


class CookieRemoveRequest(BaseModel):
    """删除 Cookie 请求"""
    platform: str = Field(..., description="平台标识: dy/bili/ks/xhs/wb/toutiao")
    cookie_id: str = Field(
        ...,
        description="Cookie ID（通过 GET /cookies 查看）",
        json_schema_extra={"example": "dy_01"}
    )


class CookieEntry(BaseModel):
    """单条 Cookie 信息"""
    id: str = Field(..., description="Cookie唯一标识")
    cookie: str = Field(..., description="Cookie字符串内容")
    note: str = Field(default="", description="备注")
    valid: bool = Field(default=True, description="是否有效（false表示已被标记失效）")
    cookie_type: str = Field(default="account", description="Cookie类型: account/public_session/virtual")
    account_valid: bool = Field(default=True, description="是否具备账号登录态能力")
    public_detail_valid: bool = Field(default=True, description="是否可用于公开详情/互动量检测")
    public_comment_valid: bool = Field(default=False, description="是否可用于评论抓取")
    fatal_count: int = Field(default=0, description="致命失败累计次数（达到阈值自动标记失效）")
    use_count: int = Field(default=0, description="累计使用次数")
    last_used_at: Optional[str] = Field(default=None, description="最后使用时间")
    last_validated_at: Optional[str] = Field(default=None, description="最后验证时间")
    last_refreshed_at: Optional[str] = Field(default=None, description="最后刷新时间")


class CookiePoolResponse(BaseModel):
    """Cookie 池状态响应"""
    pool: Dict[str, List[CookieEntry]] = Field(
        default={},
        description="按平台分组的Cookie列表"
    )
    stats: Dict[str, Dict] = Field(
        default={},
        description="各平台统计信息（total/valid/invalid）"
    )


class CookieActionResponse(BaseModel):
    """Cookie 操作通用响应"""
    success: bool = Field(..., description="操作是否成功")
    message: str = Field(default="", description="操作结果描述")
    cookie_id: str = Field(default="", description="相关的Cookie ID或Session ID")
