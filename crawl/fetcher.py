from __future__ import annotations

import json
import logging
import time
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

MP_URL = "https://mp.weixin.qq.com/cgi-bin/appmsgpublish"
PAGE_SIZE = 20
MAX_PAGES = 30


class FetchError(Exception):
    pass


class ArticleFetcher:
    """按 fakeid 分页拉取文章列表（元数据）。"""

    def __init__(self, token: str, cookie: str) -> None:
        self.token = token
        self.cookie = cookie
        self.client = httpx.Client(timeout=30.0, trust_env=False)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "ArticleFetcher":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def fetch_range(
        self,
        fakeid: str,
        start_ts: int,
        end_ts: int,
        *,
        max_pages: int = MAX_PAGES,
    ) -> List[Dict]:
        """拉取 publish_time 在 [start_ts, end_ts] 内的文章。"""
        collected: List[Dict] = []
        begin = 0

        for _ in range(max_pages):
            batch = self._fetch_page(fakeid, begin)
            if not batch:
                break

            oldest = min(a.get("publish_time", 0) for a in batch)
            for a in batch:
                pt = a.get("publish_time", 0)
                if start_ts <= pt <= end_ts:
                    collected.append(a)

            if oldest < start_ts:
                break
            begin += PAGE_SIZE
            time.sleep(1)

        logger.info("fakeid=%s 范围内文章 %d 篇", fakeid[:8], len(collected))
        return collected

    def fetch_week(
        self,
        fakeid: str,
        start_ts: int,
        end_ts: int,
    ) -> List[Dict]:
        return self.fetch_range(fakeid, start_ts, end_ts)

    def _fetch_page(self, fakeid: str, begin: int) -> List[Dict]:
        params = {
            "sub": "list",
            "search_field": "null",
            "begin": begin,
            "count": PAGE_SIZE,
            "query": "",
            "fakeid": fakeid,
            "type": "101_1",
            "free_publish_type": 1,
            "sub_action": "list_ex",
            "token": self.token,
            "lang": "zh_CN",
            "f": "json",
            "ajax": 1,
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://mp.weixin.qq.com/",
            "Cookie": self.cookie,
        }
        resp = self.client.get(MP_URL, params=params, headers=headers)
        resp.raise_for_status()
        result = resp.json()

        base = result.get("base_resp", {})
        if base.get("ret") != 0:
            raise FetchError(
                f"微信 API 错误 fakeid={fakeid[:8]} ret={base.get('ret')} "
                f"msg={base.get('err_msg', '')}"
            )

        publish_page = result.get("publish_page", {})
        if isinstance(publish_page, str):
            publish_page = json.loads(publish_page)
        if not isinstance(publish_page, dict):
            return []

        articles = []
        for item in publish_page.get("publish_list", []):
            info = item.get("publish_info", {})
            if isinstance(info, str):
                info = json.loads(info)
            if not isinstance(info, dict):
                continue
            for a in info.get("appmsgex", []):
                articles.append({
                    "aid": a.get("aid", ""),
                    "title": a.get("title", ""),
                    "link": a.get("link", ""),
                    "digest": a.get("digest", ""),
                    "cover": a.get("cover", ""),
                    "author": a.get("author", ""),
                    "publish_time": a.get("update_time", 0),
                })
        return articles
