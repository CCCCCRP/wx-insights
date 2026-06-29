from __future__ import annotations

import logging
import sys
import time
from typing import Callable, Optional

import httpx

from worker.config import (
    API_DIR,
    ARTICLE_FETCH_DELAY,
    ARTICLE_FETCH_RETRIES,
    ARTICLE_FETCH_TIMEOUT,
)
from worker.crawl.article_content import BROWSER_HEADERS, extract_author_from_html, parse_article_html

logger = logging.getLogger(__name__)


class ContentFetchError(Exception):
    pass


class ArticlePageFetcher:
    """抓取 /s/ 文章正文：优先公开链接，失败时回退 token + cookie。"""

    def __init__(
        self,
        *,
        wechat_token: str = "",
        wechat_cookie: str = "",
        delay: float = ARTICLE_FETCH_DELAY,
        retries: int = ARTICLE_FETCH_RETRIES,
        timeout: float = ARTICLE_FETCH_TIMEOUT,
    ) -> None:
        self.wechat_token = (wechat_token or "").strip()
        self.wechat_cookie = (wechat_cookie or "").strip()
        self.delay = delay
        self.retries = retries
        self.timeout = timeout
        self.client = httpx.Client(timeout=timeout, follow_redirects=True, trust_env=False)
        self._api_loaded = False
        self._has_article_content: Callable[[str], bool] | None = None
        self._is_article_unavailable: Callable[[str], bool] | None = None
        self._process_article_content: Callable[..., dict] | None = None

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "ArticlePageFetcher":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def _load_api_utils(self) -> bool:
        if self._api_loaded:
            return self._process_article_content is not None
        self._api_loaded = True
        if not API_DIR.is_dir():
            logger.warning("WECHAT_API_DIR 不存在，无法 token 回退: %s", API_DIR)
            return False
        if str(API_DIR) not in sys.path:
            sys.path.insert(0, str(API_DIR))
        try:
            from utils.content_processor import process_article_content  # noqa: E402
            from utils.helpers import has_article_content, is_article_unavailable  # noqa: E402

            self._process_article_content = process_article_content
            self._has_article_content = has_article_content
            self._is_article_unavailable = is_article_unavailable
            return True
        except Exception as e:
            logger.warning("加载微信凭证服务解析模块失败: %s", e)
            return False

    @staticmethod
    def _is_verification_page(html: str) -> bool:
        html_lower = html.lower()
        return (
            "verifycode" in html_lower
            or "请输入图片中的字符" in html
            or "环境异常" in html
            or "完成验证后即可继续访问" in html
        )

    def _fetch_public(self, url: str) -> dict:
        last: dict = {}
        for attempt in range(self.retries + 1):
            resp = self.client.get(url, headers=BROWSER_HEADERS)
            last = parse_article_html(resp.text)
            last["status"] = resp.status_code
            last["html_len"] = len(resp.text)
            last["html"] = resp.text

            if resp.status_code != 200:
                raise ContentFetchError(f"HTTP {resp.status_code}: {url}")

            if not last["blocked"] and last["content_len"] > 0:
                last["source"] = "public"
                return last

            if attempt < self.retries:
                wait = self.delay * (attempt + 1)
                logger.warning(
                    "公开页抓取受阻，%ss 后重试 (%d/%d): %s",
                    wait,
                    attempt + 1,
                    self.retries,
                    url[:80],
                )
                time.sleep(wait)

        last["source"] = "public"
        return last

    def _fetch_with_token(self, url: str) -> Optional[str]:
        if not self.wechat_token or not self.wechat_cookie:
            return None

        separator = "&" if "?" in url else "?"
        full_url = f"{url}{separator}token={self.wechat_token}"
        headers = {
            **BROWSER_HEADERS,
            "Cookie": self.wechat_cookie,
            "Referer": "https://mp.weixin.qq.com/",
        }

        last_html = ""
        for attempt in range(self.retries + 1):
            resp = self.client.get(full_url, headers=headers)
            if resp.status_code != 200:
                logger.warning("token 抓取 HTTP %d: %s", resp.status_code, url[:80])
                if attempt < self.retries:
                    time.sleep(self.delay * (attempt + 1))
                    continue
                return None

            last_html = resp.text
            if self._is_verification_page(last_html):
                logger.warning("token 抓取遇验证页 (%d/%d): %s", attempt + 1, self.retries + 1, url[:80])
            elif self._load_api_utils() and self._has_article_content(last_html):
                logger.info("token 抓取成功 len=%d: %s", len(last_html), url[:80])
                return last_html
            else:
                logger.warning("token 抓取无正文 (%d/%d): %s", attempt + 1, self.retries + 1, url[:80])

            if attempt < self.retries:
                time.sleep(self.delay * (attempt + 1))

        return last_html or None

    def _apply_author(self, article: dict, html: str) -> None:
        if article.get("author"):
            return
        author = extract_author_from_html(html)
        if author:
            article["author"] = author

    def _apply_public_result(self, article: dict, page: dict) -> bool:
        plain = (page.get("content") or "").strip()
        if page.get("blocked") or not plain:
            return False
        article["plain_content"] = plain
        article["content"] = ""
        article["content_len"] = page.get("content_len", len(plain))
        article["content_fetched"] = True
        article["content_source"] = "public"
        if page.get("title") and not article.get("title"):
            article["title"] = page["title"]
        if page.get("author"):
            article["author"] = page["author"]
        elif page.get("html"):
            self._apply_author(article, page["html"])
        return True

    def _apply_token_html(self, article: dict, html: str) -> bool:
        if not self._load_api_utils():
            return False
        assert self._is_article_unavailable and self._has_article_content
        assert self._process_article_content

        if self._is_verification_page(html):
            return False
        if self._is_article_unavailable(html):
            article["content_error"] = "unavailable"
            return False
        if not self._has_article_content(html):
            return False

        result = self._process_article_content(html, proxy_base_url=None)
        plain = (result.get("plain_content") or "").strip()
        if not plain:
            return False

        article["content"] = result.get("content") or ""
        article["plain_content"] = plain
        article["content_len"] = len(plain)
        article["content_fetched"] = True
        article["content_source"] = "token"
        self._apply_author(article, html)
        return True

    def fetch(self, url: str) -> dict:
        """公开抓取；失败且配置了 token 时自动回退。"""
        if not url or not url.startswith("http"):
            raise ContentFetchError(f"无效链接: {url!r}")

        public = self._fetch_public(url)
        if not public.get("blocked") and public.get("content_len", 0) > 0:
            return public

        html = self._fetch_with_token(url)
        if html:
            public["token_html"] = html
            public["fallback"] = "token"
        return public

    def enrich(self, article: dict) -> dict:
        """拉取 link 正文：public → token 回退。"""
        link = (article.get("link") or "").strip()
        if not link:
            article["content_fetched"] = False
            article["content_error"] = "no link"
            return article

        try:
            public = self._fetch_public(link)
            if self._apply_public_result(article, public):
                time.sleep(self.delay)
                return article

            logger.info("公开页失败，尝试 token 回退: %s", link[:80])
            html = self._fetch_with_token(link)
            if html and self._apply_token_html(article, html):
                time.sleep(self.delay)
                return article

            article["plain_content"] = ""
            article["content"] = ""
            article["content_fetched"] = False
            if public.get("blocked"):
                article["content_error"] = "blocked"
            elif not html:
                article["content_error"] = "token unavailable" if self.wechat_token else "no token"
            else:
                article["content_error"] = "empty content"
        except ContentFetchError as e:
            article["plain_content"] = ""
            article["content_fetched"] = False
            article["content_error"] = str(e)
            logger.error("正文抓取失败 %s: %s", link[:80], e)

        time.sleep(self.delay)
        return article
