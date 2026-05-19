# -*- coding: utf-8 -*-
# 举报投诉功能的 Pydantic 请求/响应模型

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class ReportSingleRequest(BaseModel):
    """单条链接举报请求（支持粘贴含文字的分享链接，后端自动提取URL）"""
    url: str = Field(
        ...,
        description="要举报的链接（支持完整URL或含文字的分享文本，后端自动提取链接）",
        json_schema_extra={"example": "https://www.douyin.com/video/7637377028366411407"}
    )
    reason: str = Field(
        default="不实信息",
        description="举报理由（中文，如：不实信息、虚假信息、违法违规等）"
    )
    description: str = Field(
        default="",
        description="补充说明（可选，部分平台支持填写文字补充）"
    )


class ReportBatchRequest(BaseModel):
    """批量链接举报请求"""
    urls: List[str] = Field(
        ...,
        min_length=1,
        description="URL列表（每项可以是完整URL或含文字的分享文本）"
    )
    reason: str = Field(
        default="不实信息",
        description="统一举报理由"
    )
    description: str = Field(
        default="",
        description="统一补充说明（可选）"
    )


class ReportMysqlRequest(BaseModel):
    """从 MySQL 读取链接进行举报"""
    host: str = Field(default="123.158.253.65")
    port: int = Field(default=30148)
    user: str = Field(default="root")
    password: str = Field(default="syyq12WER45!@#!")
    database: str = Field(default="db_sdga_report")
    table: str = Field(default="t_sdga_report_detail")
    url_column: str = Field(
        default="url",
        description="存放URL的列名"
    )
    limit: int = Field(
        default=100,
        description="最多读取的URL数量"
    )
    where: str = Field(
        default="",
        description="额外的WHERE条件（可选，如 status=0）"
    )
    reason: str = Field(default="不实信息")
    description: str = Field(default="")


class ReportTaskResponse(BaseModel):
    """举报任务提交响应"""
    task_id: str
    status: str = "pending"
    message: str = ""
    url_count: int = Field(default=0, description="待举报的链接数")


class ReportProgressResponse(BaseModel):
    """举报任务进度响应"""
    task_id: str
    status: str
    progress: float = Field(default=0, description="进度百分比 0-100")
    total: int = Field(default=0, description="总举报次数")
    processed: int = Field(default=0, description="已完成次数")
    message: str = ""
    logs: List[str] = []
    log_total: int = 0
    latest_screenshot: Optional[str] = Field(
        default=None, description="最新截图的base64编码（前端实时预览用）"
    )


class ReportReasonResponse(BaseModel):
    """举报理由列表响应"""
    platform: str
    platform_name: str
    reasons: List[str]
    default_reason: str


class ReportResultItem(BaseModel):
    """单次举报结果"""
    url: str
    platform: str
    cookie_id: str
    reason: str
    success: bool
    error_msg: str = ""
    screenshot_pre_path: str = ""   # 提交前截图路径
    screenshot_post_path: str = ""  # 提交后截图路径
    elapsed_sec: float = 0


class ReportTaskResultResponse(BaseModel):
    """举报任务结果响应"""
    task_id: str
    total: int
    success_count: int
    fail_count: int
    results: List[ReportResultItem]
