
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/weibo/field.py
# GitHub: https://github.com/NanmiCoder


# -*- coding: utf-8 -*-
# @Author  : relakkes@gmail.com
# @Time    : 2023/12/23 15:41
# @Desc    :
from enum import Enum


class SearchType(Enum):
    # Comprehensive
    DEFAULT = "1"

    # Real-time
    REAL_TIME = "61"

    # Popular
    POPULAR = "60"

    # Video
    VIDEO = "64"
