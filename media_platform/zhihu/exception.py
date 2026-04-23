
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/zhihu/exception.py
# GitHub: https://github.com/NanmiCoder


from httpx import RequestError


class DataFetchError(RequestError):
    """something error when fetch"""


class IPBlockError(RequestError):
    """fetch so fast that the server block us ip"""

class ForbiddenError(RequestError):
    """Forbidden"""
