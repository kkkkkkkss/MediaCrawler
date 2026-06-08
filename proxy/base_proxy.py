
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/proxy/base_proxy.py
# GitHub: https://github.com/NanmiCoder


# -*- coding: utf-8 -*-
# @Author  : relakkes@gmail.com
# @Time    : 2023/12/2 11:18
# @Desc    : Crawler IP acquisition implementation
# @Url     : KuaiDaili HTTP implementation, official documentation: https://www.kuaidaili.com/?ref=ldwkjqipvz6c
import json
from abc import ABC, abstractmethod
from typing import List

import config
from cache.abs_cache import AbstractCache
from cache.cache_factory import CacheFactory
from tools.utils import utils

from .types import IpInfoModel


class IpGetError(Exception):
    """ ip get error"""


class ProxyProvider(ABC):
    @abstractmethod
    async def get_proxy(self, num: int) -> List[IpInfoModel]:
        """
        Abstract method to get IP, different HTTP proxy providers need to implement this method
        :param num: Number of IPs to extract
        :return:
        """
        raise NotImplementedError



class IpCache:
    def __init__(self):
        self.cache_client: AbstractCache = CacheFactory.create_cache(cache_type=config.CACHE_TYPE_REDIS)
        self._disabled = False

    def set_ip(self, ip_key: str, ip_value_info: str, ex: int):
        """
        Set IP with expiration time, Redis is responsible for deletion after expiration
        :param ip_key:
        :param ip_value_info:
        :param ex:
        :return:
        """
        if self._disabled:
            return
        try:
            self.cache_client.set(key=ip_key, value=ip_value_info, expire_time=ex)
        except Exception as e:
            # 代理缓存只是性能优化；Redis 不可用时不能阻断短效 IP 提取和检测主流程。
            self._disabled = True
            utils.logger.warning(f"[IpCache.set_ip] skip proxy cache write: {e}")

    def load_all_ip(self, proxy_brand_name: str) -> List[IpInfoModel]:
        """
        Load all unexpired IP information from Redis
        :param proxy_brand_name: Proxy provider name
        :return:
        """
        if self._disabled:
            return []
        all_ip_list: List[IpInfoModel] = []
        try:
            all_ip_keys: List[str] = self.cache_client.keys(pattern=f"{proxy_brand_name}_*")
            for ip_key in all_ip_keys:
                ip_value = self.cache_client.get(ip_key)
                if not ip_value:
                    continue
                all_ip_list.append(IpInfoModel(**json.loads(ip_value)))
        except Exception as e:
            # Redis 缓存失效不等于代理服务商不可用；返回空列表让 provider 直接提取新 IP。
            self._disabled = True
            utils.logger.warning(f"[IpCache.load_all_ip] skip proxy cache read: {e}")
        return all_ip_list
