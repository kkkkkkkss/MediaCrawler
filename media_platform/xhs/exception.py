
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/xhs/exception.py
# GitHub: https://github.com/NanmiCoder


from httpx import RequestError


class DataFetchError(RequestError):
    """something error when fetch"""


class IPBlockError(RequestError):
    """fetch so fast that the server block us ip"""


class NoteNotFoundError(RequestError):
    """Note does not exist or is abnormal"""
