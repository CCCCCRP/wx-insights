from __future__ import annotations

import logging
import random
import re
import time
from typing import Tuple
from urllib.parse import parse_qs, urlparse

import httpx

logger = logging.getLogger(__name__)

MP_BASE_URL = "https://mp.weixin.qq.com"
QR_ENDPOINT = f"{MP_BASE_URL}/cgi-bin/scanloginqrcode"
BIZ_LOGIN_ENDPOINT = f"{MP_BASE_URL}/cgi-bin/bizlogin"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://mp.weixin.qq.com/",
    "Origin": "https://mp.weixin.qq.com",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


class ScanLoginError(Exception):
    pass


class ScanLoginClient:
    """微信公众平台扫码登录（纯 HTTP，无浏览器）。"""

    def __init__(self) -> None:
        self.session_id = f"{int(time.time() * 1000)}{random.randint(100, 999)}"
        self.client = httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers=DEFAULT_HEADERS,
            trust_env=False,
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "ScanLoginClient":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def start_session(self) -> None:
        body = {
            "userlang": "zh_CN",
            "redirect_url": "",
            "login_type": 3,
            "sessionid": self.session_id,
            "token": "",
            "lang": "zh_CN",
            "f": "json",
            "ajax": 1,
        }
        resp = self.client.post(
            BIZ_LOGIN_ENDPOINT,
            params={"action": "startlogin"},
            data=body,
        )
        resp.raise_for_status()
        logger.info("scan session started: %s", self.session_id[:16])

    def fetch_qrcode(self) -> Tuple[bytes, str]:
        resp = self.client.get(
            QR_ENDPOINT,
            params={"action": "getqrcode", "random": int(time.time() * 1000)},
        )
        resp.raise_for_status()
        content = resp.content
        if content.startswith(b"\x89PNG"):
            return content, "png"
        if content.startswith(b"\xff\xd8\xff"):
            return content, "jpg"
        try:
            raise ScanLoginError(f"获取二维码失败: {resp.json()}")
        except ValueError:
            raise ScanLoginError("获取二维码失败: 响应不是有效图片")

    def check_scan_status(self) -> dict:
        resp = self.client.get(
            QR_ENDPOINT,
            params={"action": "ask", "token": "", "lang": "zh_CN", "f": "json", "ajax": 1},
        )
        resp.raise_for_status()
        return resp.json()

    def complete_login(self) -> dict:
        login_data = {
            "userlang": "zh_CN",
            "redirect_url": "",
            "cookie_forbidden": 0,
            "cookie_cleaned": 0,
            "plugin_used": 0,
            "login_type": 3,
            "token": "",
            "lang": "zh_CN",
            "f": "json",
            "ajax": 1,
        }
        resp = self.client.post(
            BIZ_LOGIN_ENDPOINT,
            params={"action": "login"},
            data=login_data,
        )
        resp.raise_for_status()
        result = resp.json()

        if result.get("base_resp", {}).get("ret") != 0:
            raise ScanLoginError(result.get("base_resp", {}).get("err_msg", "登录失败"))

        redirect_url = result.get("redirect_url", "")
        if not redirect_url:
            raise ScanLoginError("未获取到 redirect_url")

        token = parse_qs(urlparse(f"http://localhost{redirect_url}").query).get("token", [""])[0]
        if not token:
            raise ScanLoginError("未获取到 token")

        cookie_str = "; ".join(
            f"{c.name}={c.value}" for c in self.client.cookies.jar
        )
        nickname, fakeid = self._fetch_account_info(token, cookie_str)
        return {
            "token": token,
            "cookie": cookie_str,
            "fakeid": fakeid,
            "nickname": nickname,
            "expire_time": int((time.time() + 4 * 24 * 3600) * 1000),
        }

    def _fetch_account_info(self, token: str, cookie_str: str) -> Tuple[str, str]:
        headers = {**DEFAULT_HEADERS, "Cookie": cookie_str}
        nickname = "公众号"
        fakeid = ""

        info_resp = self.client.get(
            f"{MP_BASE_URL}/cgi-bin/home",
            params={"t": "home/index", "token": token, "lang": "zh_CN"},
            headers=headers,
        )
        nick_match = re.search(r'nick_name\s*[:=]\s*["\']([^"\']+)["\']', info_resp.text)
        if nick_match:
            nickname = nick_match.group(1)

        search_resp = self.client.get(
            f"{MP_BASE_URL}/cgi-bin/searchbiz",
            params={
                "action": "search_biz",
                "token": token,
                "lang": "zh_CN",
                "f": "json",
                "ajax": 1,
                "random": time.time(),
                "query": nickname,
                "begin": 0,
                "count": 5,
            },
            headers=headers,
        )
        search_result = search_resp.json()
        if search_result.get("base_resp", {}).get("ret") == 0:
            for account in search_result.get("list", []):
                if account.get("nickname") == nickname:
                    fakeid = account.get("fakeid", "")
                    break
            if not fakeid and search_result.get("list"):
                fakeid = search_result["list"][0].get("fakeid", "")

        return nickname, fakeid
