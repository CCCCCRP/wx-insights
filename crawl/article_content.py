from __future__ import annotations

import html as html_lib
import re

import httpx

BROWSER_HEADERS = {
    "accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
    "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    ),
}

DEFAULT_SAMPLE_URL = "https://mp.weixin.qq.com/s/xb7tP39YROUCC6wCCIXUdA"

_AUTHOR_PATTERNS = (
    r'<meta\s+name="author"\s+content="([^"]+)"',
    r"<meta\s+name='author'\s+content='([^']+)'",
    r'<meta\s+property="og:article:author"\s+content="([^"]+)"',
)


def extract_author_from_html(page_html: str) -> str | None:
    """从文章页 HTML 提取署名作者（非公众号 nickname）。"""
    for pat in _AUTHOR_PATTERNS:
        m = re.search(pat, page_html, re.I)
        if m:
            author = html_lib.unescape(m.group(1).strip())
            if author:
                return author
    return None


def parse_article_html(page_html: str) -> dict:
    blocked = "环境异常" in page_html or "完成验证后即可继续访问" in page_html
    title = None
    for pat in (
        r'var msg_title = "([^"]+)"',
        r'<meta property="og:title" content="([^"]+)"',
        r"<title>([^<]+)</title>",
    ):
        m = re.search(pat, page_html)
        if m:
            title = m.group(1).strip()
            break

    content_html = ""
    for pat in (
        r'id="js_content"[^>]*>([\s\S]*?)</div>\s*<script',
        r'class="rich_media_content[^"]*"[^>]*>([\s\S]*?)</div>\s*<script',
    ):
        m = re.search(pat, page_html)
        if m:
            content_html = m.group(1)
            break

    content_html = re.sub(r"<br\s*/?>", "\n", content_html, flags=re.I)
    content_html = re.sub(r"</p>", "\n", content_html, flags=re.I)
    plain = re.sub(r"<[^>]+>", "", content_html)
    plain = html_lib.unescape(plain)
    plain = re.sub(r"\n{3,}", "\n\n", plain)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in plain.split("\n")]
    plain = "\n".join(ln for ln in lines if ln)

    return {
        "blocked": blocked,
        "title": title,
        "author": extract_author_from_html(page_html),
        "content": plain,
        "content_len": len(plain),
    }


def fetch_public_article(
    url: str,
    *,
    headers: dict | None = None,
    cookies: dict | None = None,
    timeout: float = 30.0,
) -> dict:
    h = headers or {"User-Agent": BROWSER_HEADERS["user-agent"]}
    with httpx.Client(timeout=timeout, follow_redirects=True, trust_env=False) as client:
        resp = client.get(url, headers=h, cookies=cookies or {})
    parsed = parse_article_html(resp.text)
    parsed["status"] = resp.status_code
    parsed["html_len"] = len(resp.text)
    return parsed


def _main() -> int:
    import argparse
    import logging

    from worker.log_setup import setup_logging

    setup_logging()
    log = logging.getLogger(__name__)

    parser = argparse.ArgumentParser(description="抓取 /s/ 公开文章正文")
    parser.add_argument("--url", default=DEFAULT_SAMPLE_URL)
    parser.add_argument("--preview", type=int, default=0, help="只打印前 N 字，0=全文")
    args = parser.parse_args()

    log.info("URL: %s", args.url)
    r = fetch_public_article(args.url, headers=BROWSER_HEADERS)

    log.info("HTTP %s  html_len=%s  blocked=%s", r["status"], r["html_len"], r["blocked"])
    log.info("title: %s", r["title"])
    log.info("content_len: %s", r["content_len"])
    log.info("--- content ---")

    body = r["content"] or "(empty)"
    if args.preview > 0:
        body = body[: args.preview] + ("..." if len(r["content"]) > args.preview else "")
    log.info("%s", body)

    if r["blocked"] or r["content_len"] < 100:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
