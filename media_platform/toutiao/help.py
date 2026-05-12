# -*- coding: utf-8 -*-
# 头条/西瓜 URL 解析工具

import re
from model.m_toutiao import ArticleUrlInfo


def parse_article_info_from_url(url: str) -> ArticleUrlInfo:
    """
    从头条/西瓜 URL 中提取文章/视频 ID
    支持格式:
    1. https://www.toutiao.com/i7629519642230538787/
    2. https://www.toutiao.com/a7629519642230538787/
    3. https://www.toutiao.com/article/7629519642230538787/
    4. https://www.ixigua.com/7629519642230538787
    5. 纯数字 ID: 7629519642230538787
    """
    if url.isdigit():
        return ArticleUrlInfo(item_id=url)

    # /i{id} 或 /a{id} 或 /article/{id}
    m = re.search(r"/(?:i|a|article/?)(\d{15,})", url)
    if m:
        return ArticleUrlInfo(item_id=m.group(1))

    # ixigua.com/{id}
    m = re.search(r"ixigua\.com/(\d{15,})", url)
    if m:
        return ArticleUrlInfo(item_id=m.group(1), url_type="ixigua")

    raise ValueError(f"无法从 URL 中解析头条/西瓜 ID: {url}")
