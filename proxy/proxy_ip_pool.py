
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/proxy/proxy_ip_pool.py
# GitHub: https://github.com/NanmiCoder

# -*- coding: utf-8 -*-
# @Author  : relakkes@gmail.com
# @Time    : 2023/12/2 13:45
# @Desc    : IP proxy pool implementation
import random
import asyncio
from typing import Dict, List, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed
from tools.httpx_util import make_async_client

import config
from proxy.providers import (
    new_kuai_daili_proxy,
    new_wandou_http_proxy,
)
from tools import utils

from .base_proxy import ProxyProvider
from .types import IpInfoModel, ProviderNameEnum


class ProxyIpPool:

    def __init__(
        self, ip_pool_count: int, enable_validate_ip: bool, ip_provider: ProxyProvider
    ) -> None:
        """

        Args:
            ip_pool_count:
            enable_validate_ip:
            ip_provider:
        """
        self.valid_ip_url = "https://echo.apifox.cn/"  # URL to validate if IP is valid
        self.ip_pool_count = ip_pool_count
        self.enable_validate_ip = enable_validate_ip
        self.proxy_list: List[IpInfoModel] = []
        self.ip_provider: ProxyProvider = ip_provider
        self.current_proxy: IpInfoModel | None = None  # Currently used proxy
        self._leased_proxy_keys: set[str] = set()
        self._checkout_lock = asyncio.Lock()

    @staticmethod
    def proxy_key(proxy: IpInfoModel) -> str:
        return f"{proxy.ip}:{proxy.port}"

    @staticmethod
    def proxy_url(proxy: IpInfoModel) -> str:
        protocol_raw = (proxy.protocol or "http").strip()
        # 兼容服务商返回的 http、http:、http:// 三种写法；旧拼接会把 http:// 变成 http:://。
        protocol = protocol_raw if protocol_raw.endswith("://") else protocol_raw.rstrip(":/") + "://"
        if proxy.user and proxy.password:
            return f"{protocol}{proxy.user}:{proxy.password}@{proxy.ip}:{proxy.port}"
        return f"{protocol}{proxy.ip}:{proxy.port}"

    async def load_proxies(self) -> None:
        """
        Load IP proxies
        Returns:

        """
        self.proxy_list = await self.ip_provider.get_proxy(self.ip_pool_count)

    async def _is_valid_proxy(self, proxy: IpInfoModel) -> bool:
        """
        Validate if proxy IP is valid
        :param proxy:
        :return:
        """
        utils.logger.info(
            f"[ProxyIpPool._is_valid_proxy] testing {proxy.ip} is it valid "
        )
        try:
            async with make_async_client(proxy=self.proxy_url(proxy)) as client:
                response = await client.get(self.valid_ip_url)
            if response.status_code == 200:
                return True
            else:
                return False
        except Exception as e:
            utils.logger.info(
                f"[ProxyIpPool._is_valid_proxy] testing {proxy.ip} err: {e}"
            )
            raise e

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(1))
    async def get_proxy(self) -> IpInfoModel:
        """
        Randomly extract a proxy IP from the proxy pool
        :return:
        """
        if len(self.proxy_list) == 0:
            await self._reload_proxies()

        proxy = random.choice(self.proxy_list)
        self.proxy_list.remove(proxy)  # Remove an IP once extracted
        if self.enable_validate_ip:
            if not await self._is_valid_proxy(proxy):
                raise Exception(
                    "[ProxyIpPool.get_proxy] current ip invalid and again get it"
                )
        self.current_proxy = proxy  # Save currently used proxy
        return proxy

    async def checkout_proxy(
        self,
        min_ttl_sec: int = 90,
        retry_count: int = 3,
        retry_interval_sec: int = 60,
    ) -> IpInfoModel:
        """
        为浏览器 worker 独占提取一个代理。
        旧的 current_proxy 适合单客户端复用；头条多 worker 必须独占 IP，
        否则多个浏览器仍共用同一出口，无法分散平台风控。
        """
        async with self._checkout_lock:
            for attempt in range(retry_count + 1):
                proxy = await self._pop_available_proxy(min_ttl_sec)
                if proxy:
                    self._leased_proxy_keys.add(self.proxy_key(proxy))
                    self.current_proxy = proxy
                    return proxy

                # 豌豆等短效代理 API 不适合多个 worker 同时刷新；checkout 串行化避免重复提取。
                await self._reload_proxies()
                proxy = await self._pop_available_proxy(min_ttl_sec)
                if proxy:
                    self._leased_proxy_keys.add(self.proxy_key(proxy))
                    self.current_proxy = proxy
                    return proxy

                if attempt < retry_count:
                    utils.logger.warning(
                        f"[ProxyIpPool.checkout_proxy] 暂无可用独占代理，"
                        f"{retry_interval_sec}s 后重试({attempt + 1}/{retry_count})"
                    )
                    await asyncio.sleep(retry_interval_sec)

        raise RuntimeError("代理池无可用独占 IP")

    async def _pop_available_proxy(self, min_ttl_sec: int) -> Optional[IpInfoModel]:
        candidates = [
            p for p in self.proxy_list
            if self.proxy_key(p) not in self._leased_proxy_keys
            and not p.is_expired(min_ttl_sec)
        ]
        while candidates:
            proxy = random.choice(candidates)
            self.proxy_list.remove(proxy)
            if self.enable_validate_ip:
                try:
                    if not await self._is_valid_proxy(proxy):
                        candidates.remove(proxy)
                        continue
                except Exception as e:
                    # 短效代理池里可能混入 407/超时出口；跳过坏 IP，不能让整个 worker 直接失败。
                    utils.logger.warning(
                        f"[ProxyIpPool._pop_available_proxy] 跳过不可用代理 "
                        f"{self.proxy_key(proxy)}: {e}"
                    )
                    candidates.remove(proxy)
                    continue
            return proxy
        return None

    def release_proxy(self, proxy: Optional[IpInfoModel]) -> None:
        """释放 worker 独占代理；未过期的 IP 可回到池中复用。"""
        if not proxy:
            return
        key = self.proxy_key(proxy)
        self._leased_proxy_keys.discard(key)
        if not proxy.is_expired(30) and all(self.proxy_key(p) != key for p in self.proxy_list):
            self.proxy_list.append(proxy)

    def drop_proxy(self, proxy: Optional[IpInfoModel], reason: str = "") -> None:
        """废弃坏代理；连续空白页/代理异常时不能再回池复用。"""
        if not proxy:
            return
        key = self.proxy_key(proxy)
        self._leased_proxy_keys.discard(key)
        self.proxy_list = [p for p in self.proxy_list if self.proxy_key(p) != key]
        if self.current_proxy and self.proxy_key(self.current_proxy) == key:
            self.current_proxy = None
        utils.logger.warning(
            f"[ProxyIpPool.drop_proxy] 废弃代理 {key}"
            f"{'，原因: ' + reason if reason else ''}"
        )

    def is_current_proxy_expired(self, buffer_seconds: int = 30) -> bool:
        """
        Check if current proxy has expired
        Args:
            buffer_seconds: Buffer time (seconds), how many seconds ahead to consider expired
        Returns:
            bool: True means expired or no current proxy, False means still valid
        """
        if self.current_proxy is None:
            return True
        return self.current_proxy.is_expired(buffer_seconds)

    async def get_or_refresh_proxy(self, buffer_seconds: int = 30) -> IpInfoModel:
        """
        Get current proxy, automatically refresh if expired
        Call this method before each request to ensure proxy is valid
        Args:
            buffer_seconds: Buffer time (seconds), how many seconds ahead to consider expired
        Returns:
            IpInfoModel: Valid proxy IP information
        """
        if self.is_current_proxy_expired(buffer_seconds):
            utils.logger.info(
                f"[ProxyIpPool.get_or_refresh_proxy] Current proxy expired or not set, getting new proxy..."
            )
            return await self.get_proxy()
        return self.current_proxy

    async def _reload_proxies(self):
        """
        Reload proxy pool
        :return:
        """
        self.proxy_list = []
        await self.load_proxies()


IpProxyProvider: Dict[str, ProxyProvider] = {
    ProviderNameEnum.KUAI_DAILI_PROVIDER.value: new_kuai_daili_proxy(),
    ProviderNameEnum.WANDOU_HTTP_PROVIDER.value: new_wandou_http_proxy(),
}


async def create_ip_pool(ip_pool_count: int, enable_validate_ip: bool) -> ProxyIpPool:
    """
    Create IP proxy pool
    :param ip_pool_count: Number of IPs in the pool
    :param enable_validate_ip: Whether to enable IP proxy validation
    :return:
    """
    pool = ProxyIpPool(
        ip_pool_count=ip_pool_count,
        enable_validate_ip=enable_validate_ip,
        ip_provider=IpProxyProvider.get(config.IP_PROXY_PROVIDER_NAME),
    )
    await pool.load_proxies()
    return pool


if __name__ == "__main__":
    pass
