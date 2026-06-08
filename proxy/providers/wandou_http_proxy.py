
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/proxy/providers/wandou_http_proxy.py
# GitHub: https://github.com/NanmiCoder

# -*- coding: utf-8 -*-
# @Author  : relakkes@gmail.com
# @Time    : 2025/7/31
# @Desc    : WanDou HTTP proxy IP implementation
import os
from typing import Dict, List
from urllib.parse import urlencode

import httpx

import config
from proxy import IpCache, IpGetError, ProxyProvider
from proxy.types import IpInfoModel
from tools import utils
from tools.httpx_util import make_async_client


class WanDouHttpProxy(ProxyProvider):

    def __init__(self, app_key: str, num: int = 100):
        """
        WanDou HTTP proxy IP implementation
        :param app_key: Open app_key, can be obtained through user center
        :param num: Number of IPs extracted at once, maximum 100
        """
        self.proxy_brand_name = "WANDOUHTTP"
        self.api_path = "https://api.wandouapp.com/"
        self.params = {
            "app_key": app_key,
            "num": num,
            "xy": getattr(config, "WANDOU_PROXY_XY", 1),
            "type": getattr(config, "WANDOU_API_TYPE", 2),
            "nr": getattr(config, "WANDOU_NR", 99),
            "area_id": getattr(config, "WANDOU_AREA_ID", 0),
            "isp": getattr(config, "WANDOU_ISP", 0),
        }
        self.ip_cache = IpCache()

    async def get_proxy(self, num: int) -> List[IpInfoModel]:
        """
        :param num:
        :return:
        """

        # Prioritize getting IP from cache
        ip_cache_list = self.ip_cache.load_all_ip(
            proxy_brand_name=self.proxy_brand_name
        )
        if len(ip_cache_list) >= num:
            return ip_cache_list[:num]

        # If the quantity in cache is insufficient, get from IP provider to supplement, then store in cache
        need_get_count = num - len(ip_cache_list)
        self.params.update({"num": min(need_get_count, 100)})  # Maximum 100
        ip_infos = []
        async with make_async_client() as client:
            url = self.api_path + "?" + urlencode(self.params)
            safe_params = {**self.params, "app_key": "***"}
            utils.logger.info(
                f"[WanDouHttpProxy.get_proxy] 提取豌豆短效IP params={safe_params}"
            )
            response = await client.get(
                url,
                headers={
                    "User-Agent": "MediaCrawler https://github.com/NanmiCoder/MediaCrawler",
                },
            )
            res_dict: Dict = response.json()
            if res_dict.get("code") in (0, 200):
                data = res_dict.get("data", [])
                if isinstance(data, dict):
                    data = data.get("proxy_list") or data.get("list") or []
                current_ts = utils.get_unix_timestamp()
                for ip_item in data:
                    expire_ts = utils.get_unix_time_from_time_str(
                        ip_item.get("expire_time")
                    )
                    if not expire_ts:
                        # 豌豆试用短效 IP 默认 10 分钟；字段异常时用保守 TTL，避免长期复用坏时间。
                        expire_ts = current_ts + getattr(config, "WANDOU_DEFAULT_EXPIRE_SEC", 600)
                    ip_info_model = IpInfoModel(
                        ip=ip_item.get("ip"),
                        port=ip_item.get("port"),
                        user="",  # WanDou HTTP does not require username password authentication
                        password="",
                        protocol="http://",
                        expired_time_ts=expire_ts,
                    )
                    ip_key = f"WANDOUHTTP_{ip_info_model.ip}_{ip_info_model.port}"
                    ip_value = ip_info_model.model_dump_json()
                    ip_infos.append(ip_info_model)
                    ttl = max(ip_info_model.expired_time_ts - current_ts, 1)
                    self.ip_cache.set_ip(ip_key, ip_value, ex=ttl)
            else:
                error_msg = res_dict.get("msg", "unknown error")
                # Handle specific error codes
                error_code = res_dict.get("code")
                if error_code == 10001:
                    error_msg = "General error, check msg content for specific error information"
                elif error_code == 10048:
                    error_msg = "No available package"
                raise IpGetError(f"{error_msg} (code: {error_code})")
        return ip_cache_list + ip_infos


def new_wandou_http_proxy() -> WanDouHttpProxy:
    """
    Construct WanDou HTTP instance
    Supports two environment variable naming formats:
    1. Uppercase format: WANDOU_APP_KEY
    2. Lowercase format: wandou_app_key
    Prioritize uppercase format, use lowercase format if not exists
    Returns:

    """
    # Support both uppercase and lowercase environment variable formats, prioritize uppercase
    app_key = os.getenv("WANDOU_APP_KEY") or os.getenv("wandou_app_key", "your_wandou_http_app_key")

    return WanDouHttpProxy(app_key=app_key)
