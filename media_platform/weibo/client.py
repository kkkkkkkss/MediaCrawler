
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/weibo/client.py
# GitHub: https://github.com/NanmiCoder

# -*- coding: utf-8 -*-
# @Author  : relakkes@gmail.com
# @Time    : 2023/12/23 15:40
# @Desc    : Weibo crawler API request client

import asyncio
import copy
import json
import re
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Union
from urllib.parse import parse_qs, unquote, urlencode

import httpx
from httpx import Response
from playwright.async_api import BrowserContext, Page
from tools.httpx_util import make_async_client
from tenacity import retry, stop_after_attempt, wait_fixed

import config
from proxy.proxy_mixin import ProxyRefreshMixin
from tools import utils

if TYPE_CHECKING:
    from proxy.proxy_ip_pool import ProxyIpPool

from .exception import DataFetchError
from .field import SearchType


class WeiboClient(ProxyRefreshMixin):

    def __init__(
        self,
        timeout=60,  # If media crawling is enabled, Weibo images need a longer timeout
        proxy=None,
        *,
        headers: Dict[str, str],
        playwright_page: Page,
        cookie_dict: Dict[str, str],
        proxy_ip_pool: Optional["ProxyIpPool"] = None,
    ):
        self.proxy = proxy
        self.timeout = timeout
        self.headers = headers
        self._host = "https://m.weibo.cn"
        self.cookie_urls = [self._host]
        self.playwright_page = playwright_page
        self.cookie_dict = cookie_dict
        self._image_agent_host = "https://i1.wp.com/"
        # Initialize proxy pool (from ProxyRefreshMixin)
        self.init_proxy_pool(proxy_ip_pool)

    @retry(stop=stop_after_attempt(5), wait=wait_fixed(3))
    async def request(self, method, url, **kwargs) -> Union[Response, Dict]:
        # Check if proxy is expired before each request
        await self._refresh_proxy_if_expired()

        enable_return_response = kwargs.pop("return_response", False)
        async with make_async_client(proxy=self.proxy) as client:
            response = await client.request(method, url, timeout=self.timeout, **kwargs)

        if enable_return_response:
            return response

        try:
            data: Dict = response.json()
        except json.decoder.JSONDecodeError:
            # issue: #771 Search API returns error 432, retry multiple times + update h5 cookies
            utils.logger.error(f"[WeiboClient.request] request {method}:{url} err code: {response.status_code} res:{response.text}")
            await self.playwright_page.goto(self._host)
            await asyncio.sleep(2)
            await self.update_cookies(browser_context=self.playwright_page.context)
            raise DataFetchError(f"get response code error: {response.status_code}")

        ok_code = data.get("ok")
        if ok_code == 0:  # response error
            utils.logger.error(f"[WeiboClient.request] request {method}:{url} err, res:{data}")
            raise DataFetchError(data.get("msg", "response error"))
        elif ok_code != 1:  # unknown error
            utils.logger.error(f"[WeiboClient.request] request {method}:{url} err, res:{data}")
            raise DataFetchError(data.get("msg", "unknown error"))
        else:  # response right
            return data.get("data", {})

    async def get(self, uri: str, params=None, headers=None, **kwargs) -> Union[Response, Dict]:
        final_uri = uri
        if isinstance(params, dict):
            final_uri = (f"{uri}?"
                         f"{urlencode(params)}")

        if headers is None:
            headers = self.headers
        return await self.request(method="GET", url=f"{self._host}{final_uri}", headers=headers, **kwargs)

    async def post(self, uri: str, data: dict) -> Dict:
        json_str = json.dumps(data, separators=(',', ':'), ensure_ascii=False)
        return await self.request(method="POST", url=f"{self._host}{uri}", data=json_str, headers=self.headers)

    async def pong(self) -> bool:
        """get a note to check if login state is ok"""
        utils.logger.info("[WeiboClient.pong] Begin pong weibo...")
        ping_flag = False
        try:
            uri = "/api/config"
            resp_data: Dict = await self.request(method="GET", url=f"{self._host}{uri}", headers=self.headers)
            if resp_data.get("login"):
                ping_flag = True
            else:
                utils.logger.error(f"[WeiboClient.pong] cookie may be invalid and again login...")
        except Exception as e:
            utils.logger.error(f"[WeiboClient.pong] Pong weibo failed: {e}, and try to login again...")
            ping_flag = False
        return ping_flag

    async def update_cookies(self, browser_context: BrowserContext, urls: Optional[List[str]] = None):
        """
        Update cookies from browser context
        :param browser_context: Browser context
        :param urls: Optional list of URLs to filter cookies (e.g., ["https://m.weibo.cn"])
                     If provided, only cookies for these URLs will be retrieved
        """
        cookie_urls = urls or self.cookie_urls
        cookie_str, cookie_dict = await utils.convert_browser_context_cookies(
            browser_context,
            urls=cookie_urls,
        )
        self.headers["Cookie"] = cookie_str
        self.cookie_dict = cookie_dict
        utils.logger.info(
            f"[WeiboClient.update_cookies] Cookie updated successfully for {cookie_urls}, total: {len(cookie_dict)} cookies"
        )

    async def get_note_by_keyword(
        self,
        keyword: str,
        page: int = 1,
        search_type: SearchType = SearchType.DEFAULT,
    ) -> Dict:
        """
        search note by keyword
        :param keyword: Search keyword for Weibo
        :param page: Pagination parameter - current page number
        :param search_type: Search type, see SearchType enum in weibo/field.py
        :return:
        """
        uri = "/api/container/getIndex"
        containerid = f"100103type={search_type.value}&q={keyword}"
        params = {
            "containerid": containerid,
            "page_type": "searchall",
            "page": page,
        }
        return await self.get(uri, params)

    async def get_note_comments(self, mid_id: str, max_id: int, max_id_type: int = 0) -> Dict:
        """get notes comments
        :param mid_id: Weibo ID
        :param max_id: Pagination parameter ID
        :param max_id_type: Pagination parameter ID type
        :return:
        """
        uri = "/comments/hotflow"
        params = {
            "id": mid_id,
            "mid": mid_id,
            "max_id_type": max_id_type,
        }
        if max_id > 0:
            params.update({"max_id": max_id})
        referer_url = f"https://m.weibo.cn/detail/{mid_id}"
        headers = copy.copy(self.headers)
        headers["Referer"] = referer_url

        return await self.get(uri, params, headers=headers)

    async def get_note_all_comments(
        self,
        note_id: str,
        crawl_interval: float = 1.0,
        callback: Optional[Callable] = None,
        max_count: int = 10,
    ):
        """
        get note all comments include sub comments
        :param note_id:
        :param crawl_interval:
        :param callback:
        :param max_count:
        :return:
        """
        result = []
        is_end = False
        max_id = -1
        max_id_type = 0
        while not is_end and len(result) < max_count:
            comments_res = await self.get_note_comments(note_id, max_id, max_id_type)
            max_id: int = comments_res.get("max_id")
            max_id_type: int = comments_res.get("max_id_type")
            comment_list: List[Dict] = comments_res.get("data", [])
            is_end = max_id == 0
            if len(result) + len(comment_list) > max_count:
                comment_list = comment_list[:max_count - len(result)]
            if callback:  # If callback function exists, execute it
                await callback(note_id, comment_list)
            await asyncio.sleep(crawl_interval)
            result.extend(comment_list)
            sub_comment_result = await self.get_comments_all_sub_comments(note_id, comment_list, callback)
            result.extend(sub_comment_result)
        return result

    @staticmethod
    async def get_comments_all_sub_comments(
        note_id: str,
        comment_list: List[Dict],
        callback: Optional[Callable] = None,
    ) -> List[Dict]:
        """
        Get all sub-comments of comments
        Args:
            note_id:
            comment_list:
            callback:

        Returns:

        """
        if not config.ENABLE_GET_SUB_COMMENTS:
            utils.logger.info(f"[WeiboClient.get_comments_all_sub_comments] Crawling sub_comment mode is not enabled")
            return []

        res_sub_comments = []
        for comment in comment_list:
            sub_comments = comment.get("comments")
            if sub_comments and isinstance(sub_comments, list):
                if callback:
                    await callback(note_id, sub_comments)
                res_sub_comments.extend(sub_comments)
        return res_sub_comments

    async def get_note_info_by_id(self, note_id: str) -> Dict:
        """
        Get note details by note ID
        :param note_id:
        :return:
        """
        url = f"{self._host}/detail/{note_id}"
        async with make_async_client(proxy=self.proxy) as client:
            response = await client.request("GET", url, timeout=self.timeout, headers=self.headers)
            if response.status_code != 200:
                raise DataFetchError(f"get weibo detail err: {response.text}")
            match = re.search(r'var \$render_data = (\[.*?\])\[0\]', response.text, re.DOTALL)
            if match:
                render_data_json = match.group(1)
                render_data_dict = json.loads(render_data_json)
                note_detail = render_data_dict[0].get("status")
                note_item = {"mblog": note_detail}
                return note_item
            else:
                utils.logger.info(f"[WeiboClient.get_note_info_by_id] $render_data value not found")
                return dict()

    async def get_note_info_by_url(self, original_url: str) -> Dict:
        """
        通过原始URL获取微博详情，用于 ttarticle/tv/show 等非标准URL。
        策略按优先级：
          1. ttarticle → 专用 API（无需登录）
          2. httpx重定向 → 找到标准mid → 走标准API
          3. Playwright渲染（兜底，适用于 tv/show 等 SPA 页面）
        """
        # ttarticle（长微博）→ 专用文章详情API
        if "/ttarticle/" in original_url:
            return await self._fetch_ttarticle(original_url)

        # tv/show（微博视频）→ 先尝试重定向找mid，失败则Playwright渲染
        if "/tv/show/" in original_url:
            return await self._fetch_tv_show(original_url)

        # 其他未知格式 → httpx重定向兜底
        try:
            async with make_async_client(proxy=self.proxy) as client:
                resp = await client.request(
                    "GET", original_url, timeout=self.timeout,
                    headers=self.headers, follow_redirects=True,
                )
                final_url = str(resp.url)
                mid = self._extract_mid_from_url(final_url)
                if mid:
                    return await self.get_note_info_by_id(mid)
                result = self._parse_render_data(resp.text)
                if result:
                    return result
        except Exception as e:
            utils.logger.warning(
                f"[WeiboClient.get_note_info_by_url] 兜底获取失败: {e}"
            )
        return dict()

    async def _fetch_ttarticle(self, url: str) -> Dict:
        """
        长微博文章：用 ttarticle/x/m/aj/detail API 获取文章详情。
        此API无需登录，返回 title/author/read_count 等。
        """
        from urllib.parse import urlparse, parse_qs as _parse_qs

        parsed = urlparse(url)
        params = _parse_qs(parsed.query)
        article_id = (params.get("id") or [None])[0]
        if not article_id:
            utils.logger.warning(f"[WeiboClient._fetch_ttarticle] 无法提取文章ID: {url}")
            return dict()

        api_url = f"https://weibo.com/ttarticle/x/m/aj/detail?id={article_id}"
        try:
            async with make_async_client(proxy=self.proxy) as client:
                resp = await client.request(
                    "GET", api_url, timeout=self.timeout,
                    headers={
                        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X)",
                        "Referer": "https://card.weibo.com/",
                    },
                )
                data = resp.json()
                if str(data.get("code")) == "100000":
                    article = data.get("data", {})
                    userinfo = article.get("userinfo", {})
                    # 构造兼容 fallback_field_map 的 mblog 格式
                    mblog = {
                        "text": article.get("title", ""),
                        "reads_count": article.get("read_count"),
                        "user": {
                            "screen_name": (
                                userinfo.get("screen_name")
                                or article.get("writer", {}).get("screen_name", "")
                            ),
                        },
                        "created_at": article.get("complete_create_at", ""),
                        # 长微博文章本身不直接暴露点赞/评论/转发（需要关联mblog）
                        "_article_id": article_id,
                        "_article_title": article.get("title", ""),
                    }
                    utils.logger.info(
                        f"[WeiboClient._fetch_ttarticle] 文章获取成功: "
                        f"title={article.get('title','')[:30]}, "
                        f"read_count={article.get('read_count')}"
                    )
                    return {"mblog": mblog}
                else:
                    utils.logger.warning(
                        f"[WeiboClient._fetch_ttarticle] API返回异常: {data.get('msg')}"
                    )
        except Exception as e:
            utils.logger.error(
                f"[WeiboClient._fetch_ttarticle] 请求失败: {e}"
            )
        return dict()

    async def _fetch_tv_show(self, url: str) -> Dict:
        """
        微博视频页：用 h5.video.weibo.com/api/component POST 接口获取视频指标。
        该API只需 SUB/SUBP cookie（不需要 XSRF-TOKEN），但要求Cookie具备
        krvideo服务的SSO授权——并非所有m.weibo.cn的Cookie都满足。
        当当前Cookie返回302时，自动从Cookie池中取其他wb Cookie逐个尝试，
        因为此处是纯httpx调用，不占用浏览器资源。
        """
        m = re.search(r"(?:tv/show/|/show/)(\d+:\d+)", url)
        object_id = m.group(1) if m else None
        if not object_id:
            utils.logger.warning(f"[WeiboClient._fetch_tv_show] 无法提取 object_id: {url}")
            return dict()

        # 收集所有可用的wb Cookie（当前的 + 池中其他的）
        cookie_dicts_to_try = [self.cookie_dict]
        try:
            from proxy.cookie_pool import cookie_pool
            for entry in cookie_pool._pool.get("wb", []):
                if not entry.valid or not entry.cookie:
                    continue
                cd = {}
                for pair in entry.cookie.split(";"):
                    pair = pair.strip()
                    if "=" in pair:
                        k, _, v = pair.partition("=")
                        cd[k.strip()] = v.strip()
                # 跳过跟当前Cookie相同的（通过SUB值判断）
                if cd.get("SUB") == self.cookie_dict.get("SUB"):
                    continue
                cookie_dicts_to_try.append(cd)
        except Exception:
            pass

        for i, cd in enumerate(cookie_dicts_to_try):
            result = await self._try_tv_show_api(object_id, cd)
            if result is not None:
                return result
            if i < len(cookie_dicts_to_try) - 1:
                utils.logger.info(
                    f"[WeiboClient._fetch_tv_show] Cookie#{i}缺SSO授权，尝试下一个"
                )
        utils.logger.warning(
            f"[WeiboClient._fetch_tv_show] 所有Cookie均无法访问视频API: {object_id}"
        )
        return dict()

    async def _try_tv_show_api(
        self, object_id: str, cookie_dict: Dict
    ) -> Optional[Dict]:
        """
        尝试用指定Cookie调用h5视频组件API。
        返回 dict(成功) / None(302/失败，需换Cookie)。
        """
        import json as _json
        from urllib.parse import quote

        api_url = (
            f"https://h5.video.weibo.com/api/component"
            f"?page={quote(f'/show/{object_id}')}"
        )
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookie_dict.items())
        api_headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15",
            "Referer": f"https://h5.video.weibo.com/show/{object_id}",
            "Origin": "https://h5.video.weibo.com",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json, text/plain, */*",
            "page-referer": f"/show/{object_id}",
            "Cookie": cookie_str,
        }
        post_body = f'data={_json.dumps({"Component_Play_Playinfo": {"oid": object_id}})}'

        try:
            async with make_async_client(proxy=self.proxy) as client:
                resp = await client.request(
                    "POST", api_url, timeout=self.timeout,
                    headers=api_headers, content=post_body,
                    follow_redirects=False,
                )
                # 302 → Cookie缺少krvideo SSO授权
                if resp.status_code == 302:
                    return None
                raw_body = resp.text
                if not raw_body or not raw_body.strip():
                    return None
                data = resp.json()
                if str(data.get("code")) == "100000":
                    play_info = data.get("data", {}).get("Component_Play_Playinfo", {})
                    if play_info:
                        mblog = {
                            "mid": play_info.get("mid"),
                            "attitudes_count": play_info.get("attitudes_count", 0),
                            "comments_count": play_info.get("comments_count", 0),
                            "reposts_count": play_info.get("reposts_count", 0),
                            "play_count": play_info.get("play_count", 0),
                            "text": play_info.get("title", ""),
                            "user": {"screen_name": play_info.get("author", "")},
                            "page_info": {
                                "play_count": play_info.get("play_count", 0),
                            },
                        }
                        utils.logger.info(
                            f"[WeiboClient._fetch_tv_show] 视频API成功: "
                            f"mid={play_info.get('mid')}, "
                            f"author={play_info.get('author')}, "
                            f"likes={play_info.get('attitudes_count')}, "
                            f"comments={play_info.get('comments_count')}"
                        )
                        return {"mblog": mblog}
                    utils.logger.warning(
                        f"[WeiboClient._fetch_tv_show] API无 Playinfo: {object_id}"
                    )
                    return dict()
                else:
                    utils.logger.warning(
                        f"[WeiboClient._fetch_tv_show] API异常: code={data.get('code')}, "
                        f"msg={data.get('msg')}"
                    )
                    return dict()
        except Exception as e:
            utils.logger.error(
                f"[WeiboClient._fetch_tv_show] 请求失败: {e}"
            )
            return dict()

    async def _fetch_via_playwright(self, url: str) -> Dict:
        """
        用 Playwright 新开标签页渲染微博页面，从 DOM/JS 变量提取指标。
        先将Cookie注入到目标域名，确保登录态生效。
        """
        page = None
        try:
            ctx = self.playwright_page.context

            # 将 m.weibo.cn cookie 复制到 weibo.com 域，确保PC页面登录态
            await self._inject_cookies_for_domain(ctx, url)

            page = await ctx.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            # SPA页面需要较长渲染时间
            await asyncio.sleep(5)

            # 检查页面最终URL是否跳转到了标准微博页
            final_url = page.url
            mid = self._extract_mid_from_url(final_url)
            if mid:
                utils.logger.info(
                    f"[WeiboClient._fetch_via_playwright] 页面跳转到 mid={mid}"
                )
                await page.close()
                page = None
                return await self.get_note_info_by_id(mid)

            # 从页面 JS 变量提取数据
            metrics = await page.evaluate("""() => {
                // $render_data（移动版页面）
                if (window.$render_data) {
                    const rd = Array.isArray(window.$render_data)
                        ? window.$render_data[0] : window.$render_data;
                    if (rd && rd.status) return {mblog: rd.status};
                }
                // __INITIAL_STATE__（Vue SPA）
                if (window.__INITIAL_STATE__) {
                    const s = window.__INITIAL_STATE__;
                    if (s.mblog) return {mblog: s.mblog};
                    if (s.status) return {mblog: s.status};
                }
                return null;
            }""")

            if metrics and metrics.get("mblog"):
                utils.logger.info(
                    f"[WeiboClient._fetch_via_playwright] 从JS变量提取成功: {url[:60]}"
                )
                return metrics

            utils.logger.warning(
                f"[WeiboClient._fetch_via_playwright] 无法提取数据: {url[:60]}, "
                f"final_url={final_url[:80]}"
            )
            return dict()

        except Exception as e:
            utils.logger.error(
                f"[WeiboClient._fetch_via_playwright] 渲染失败: {url[:60]}, err: {e}"
            )
            return dict()
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass

    async def _inject_cookies_for_domain(
        self, ctx: BrowserContext, target_url: str
    ):
        """
        将当前 cookie_dict 中的关键Cookie注入到目标URL和相关域名，
        解决 m.weibo.cn cookie 不能跨域到 weibo.com / h5.video.weibo.com 的问题。
        """
        from urllib.parse import urlparse
        parsed = urlparse(target_url)
        # 同时注入到 .weibo.com 和 .weibo.cn 两个顶级域，覆盖所有微博子域名
        target_domains = [".weibo.com", ".weibo.cn"]
        if parsed.hostname and parsed.hostname not in ("weibo.com", "weibo.cn"):
            target_domains.append(f".{parsed.hostname}")

        key_cookies = ["SUB", "SUBP", "SSOLoginState", "XSRF-TOKEN", "login_sid_t"]
        cookies_to_add = []
        for domain in target_domains:
            for name in key_cookies:
                value = self.cookie_dict.get(name)
                if value:
                    cookies_to_add.append({
                        "name": name,
                        "value": value,
                        "domain": domain,
                        "path": "/",
                    })
        if cookies_to_add:
            await ctx.add_cookies(cookies_to_add)
            utils.logger.info(
                f"[WeiboClient] 注入 {len(cookies_to_add)} 个Cookie到 {target_domains}"
            )

    @staticmethod
    def _extract_mid_from_url(url: str) -> Optional[str]:
        """从URL路径中提取微博标准 mid（如 /detail/xxx 或 /{uid}/{mid}）"""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            path = parsed.path

            m = re.search(r"/detail/(\w+)", path)
            if m:
                return m.group(1)
            m = re.search(r"/(\d+)/(\w+)", path)
            if m:
                return m.group(2)
        except Exception:
            pass
        return None

    def _parse_render_data(self, html: str) -> Optional[Dict]:
        """从 m.weibo.cn 页面提取 $render_data 中的 status 数据"""
        match = re.search(
            r'var \$render_data = (\[.*?\])\[0\]', html, re.DOTALL
        )
        if match:
            try:
                render_list = json.loads(match.group(1))
                status = render_list[0].get("status")
                if status:
                    return {"mblog": status}
            except (json.JSONDecodeError, IndexError, TypeError):
                pass
        return None

    async def get_note_image(self, image_url: str) -> bytes:
        image_url = image_url[8:]  # Remove https://
        sub_url = image_url.split("/")
        image_url = ""
        for i in range(len(sub_url)):
            if i == 1:
                image_url += "large/"  # Get high-resolution images
            elif i == len(sub_url) - 1:
                image_url += sub_url[i]
            else:
                image_url += sub_url[i] + "/"
        # Weibo image hosting has anti-hotlinking, so proxy access is needed
        # Since Weibo images are accessed through i1.wp.com, we need to concatenate the URL
        final_uri = (f"{self._image_agent_host}"
                     f"{image_url}")
        async with make_async_client(proxy=self.proxy) as client:
            try:
                response = await client.request("GET", final_uri, timeout=self.timeout)
                response.raise_for_status()
                if not response.reason_phrase == "OK":
                    utils.logger.error(f"[WeiboClient.get_note_image] request {final_uri} err, res:{response.text}")
                    return None
                else:
                    return response.content
            except httpx.HTTPError as exc:  # some wrong when call httpx.request method, such as connection error, client error, server error or response status code is not 2xx
                utils.logger.error(f"[DouYinClient.get_aweme_media] {exc.__class__.__name__} for {exc.request.url} - {exc}")    # Keep original exception type name for developer debugging
                return None

    async def get_creator_container_info(self, creator_id: str) -> Dict:
        """
        Get user's container ID, container information represents the real API request path
            fid_container_id: Container ID for user's Weibo detail API
            lfid_container_id: Container ID for user's Weibo list API
        Args:
            creator_id: User ID

        Returns: Dictionary with container IDs

        """
        response = await self.get(f"/u/{creator_id}", return_response=True)
        m_weibocn_params = response.cookies.get("M_WEIBOCN_PARAMS")
        if not m_weibocn_params:
            raise DataFetchError("get containerid failed")
        m_weibocn_params_dict = parse_qs(unquote(m_weibocn_params))
        return {"fid_container_id": m_weibocn_params_dict.get("fid", [""])[0], "lfid_container_id": m_weibocn_params_dict.get("lfid", [""])[0]}

    async def get_creator_info_by_id(self, creator_id: str) -> Dict:
        """
        Get user details by user ID
        Args:
            creator_id:

        Returns:

        """
        uri = "/api/container/getIndex"
        containerid = f"100505{creator_id}"
        params = {
            "jumpfrom": "weibocom",
            "type": "uid",
            "value": creator_id,
            "containerid":containerid,
        }
        user_res = await self.get(uri, params)
        return user_res

    async def get_notes_by_creator(
        self,
        creator: str,
        container_id: str,
        since_id: str = "0",
    ) -> Dict:
        """
        Get creator's notes
        Args:
            creator: Creator ID
            container_id: Container ID
            since_id: ID of the last note from previous page
        Returns:

        """

        uri = "/api/container/getIndex"
        params = {
            "jumpfrom": "weibocom",
            "type": "uid",
            "value": creator,
            "containerid": container_id,
            "since_id": since_id,
        }
        return await self.get(uri, params)

    async def get_all_notes_by_creator_id(
        self,
        creator_id: str,
        container_id: str,
        crawl_interval: float = 1.0,
        callback: Optional[Callable] = None,
    ) -> List[Dict]:
        """
        Get all posts published by a specified user, this method will continuously fetch all posts from a user
        Args:
            creator_id: Creator user ID
            container_id: Container ID for the user
            crawl_interval: Interval between requests in seconds
            callback: Optional callback function to process notes

        Returns: List of all notes

        """
        result = []
        notes_has_more = True
        since_id = ""
        crawler_total_count = 0
        while notes_has_more:
            notes_res = await self.get_notes_by_creator(creator_id, container_id, since_id)
            if not notes_res:
                utils.logger.error(f"[WeiboClient.get_notes_by_creator] The current creator may have been banned by Weibo, so they cannot access the data.")
                break
            since_id = notes_res.get("cardlistInfo", {}).get("since_id", "0")
            if "cards" not in notes_res:
                utils.logger.info(f"[WeiboClient.get_all_notes_by_creator] No 'notes' key found in response: {notes_res}")
                break

            notes = notes_res["cards"]
            utils.logger.info(f"[WeiboClient.get_all_notes_by_creator] got user_id:{creator_id} notes len : {len(notes)}")
            notes = [note for note in notes if note.get("card_type") == 9]
            if callback:
                await callback(notes)
            await asyncio.sleep(crawl_interval)
            result.extend(notes)
            crawler_total_count += 10
            notes_has_more = notes_res.get("cardlistInfo", {}).get("total", 0) > crawler_total_count
        return result
