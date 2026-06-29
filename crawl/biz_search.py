from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

MP_SEARCH_URL = "https://mp.weixin.qq.com/cgi-bin/searchbiz"


class BizSearchError(Exception):
    pass


class BizSearcher:
    """通过公众平台 searchbiz 接口按名称查 fakeid。"""

    def __init__(self, token: str, cookie: str, *, timeout: float = 15.0) -> None:
        self.token = token
        self.cookie = cookie
        self.client = httpx.Client(timeout=timeout, follow_redirects=True, trust_env=False)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "BizSearcher":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def search(self, query: str, *, count: int = 5) -> List[Dict[str, str]]:
        query = (query or "").strip()
        if not query:
            raise BizSearchError("搜索关键词不能为空")

        resp = self.client.get(
            MP_SEARCH_URL,
            params={
                "action": "search_biz",
                "token": self.token,
                "lang": "zh_CN",
                "f": "json",
                "ajax": 1,
                "random": time.time(),
                "query": query,
                "begin": 0,
                "count": count,
            },
            headers={
                "Cookie": self.cookie,
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": "https://mp.weixin.qq.com/",
            },
        )
        resp.raise_for_status()
        try:
            result = resp.json()
        except Exception as e:
            raise BizSearchError("搜索返回非 JSON，可能 token 已失效") from e

        base = result.get("base_resp", {})
        if base.get("ret") != 0:
            raise BizSearchError(
                f"搜索失败 ret={base.get('ret')} msg={base.get('err_msg', '')}"
            )

        items = []
        for acc in result.get("list") or []:
            fid = (acc.get("fakeid") or "").strip()
            if not fid:
                continue
            items.append({
                "fakeid": fid,
                "nickname": (acc.get("nickname") or "").strip(),
                "alias": (acc.get("alias") or "").strip(),
            })
        return items


def pick_best_match(query: str, results: List[Dict[str, str]]) -> Optional[Dict[str, str]]:
    """优先精确 nickname，其次 alias，唯一结果直接采用。"""
    q = (query or "").strip()
    if not results:
        return None
    for item in results:
        if item.get("nickname") == q:
            return item
    for item in results:
        if item.get("alias") == q:
            return item
    if len(results) == 1:
        return results[0]
    return None


def format_candidates(results: List[Dict[str, str]]) -> str:
    lines = []
    for i, item in enumerate(results, 1):
        alias = f" (@{item['alias']})" if item.get("alias") else ""
        lines.append(f"  {i}. {item.get('nickname', '')}{alias}  fakeid={item.get('fakeid', '')}")
    return "\n".join(lines)
