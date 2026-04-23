
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/bilibili/field.py
# GitHub: https://github.com/NanmiCoder


# -*- coding: utf-8 -*-
# @Author  : relakkes@gmail.com
# @Time    : 2023/12/3 16:20
# @Desc    :

from enum import Enum


class SearchOrderType(Enum):
    # Comprehensive sorting
    DEFAULT = ""

    # Most clicks
    MOST_CLICK = "click"

    # Latest published
    LAST_PUBLISH = "pubdate"

    # Most danmu (comments)
    MOST_DANMU = "dm"

    # Most bookmarks
    MOST_MARK = "stow"


class CommentOrderType(Enum):
    # By popularity only
    DEFAULT = 0

    # By popularity + time
    MIXED = 1

    # By time
    TIME = 2
