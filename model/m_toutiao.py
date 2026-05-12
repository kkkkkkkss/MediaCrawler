# -*- coding: utf-8 -*-

from pydantic import BaseModel, Field


class ArticleUrlInfo(BaseModel):
    """头条/西瓜视频 URL 信息"""
    item_id: str = Field(title="item id (文章/视频 ID)")
    url_type: str = Field(default="normal", title="url type: normal, ixigua")


class CreatorUrlInfo(BaseModel):
    """头条创作者 URL 信息"""
    user_id: str = Field(title="user id (创作者 ID)")
