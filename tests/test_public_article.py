"""公开 /s/ 文章链接抓取测试。"""
from __future__ import annotations

import pytest

from worker.crawl.article_content import (
    BROWSER_HEADERS,
    DEFAULT_SAMPLE_URL,
    fetch_public_article,
    parse_article_html,
)


def test_parse_article_html_extracts_title_and_body():
    html = """
    <meta property="og:title" content="测试标题">
    <meta name="author" content="张三">
    <div id="js_content"><p>第一段</p><p>第二段</p></div>
    <script></script>
    """
    r = parse_article_html(html)
    assert r["blocked"] is False
    assert r["title"] == "测试标题"
    assert r["author"] == "张三"
    assert "第一段" in r["content"]
    assert r["content_len"] >= 6


def test_extract_author_from_html_meta():
    from worker.crawl.article_content import extract_author_from_html

    html = '<meta name="author" content="宝玉" />'
    assert extract_author_from_html(html) == "宝玉"
    assert extract_author_from_html("<html></html>") is None


@pytest.mark.integration
def test_public_article_without_token():
    r = fetch_public_article(
        DEFAULT_SAMPLE_URL,
        headers={"User-Agent": BROWSER_HEADERS["user-agent"]},
    )
    assert r["status"] == 200
    assert r["blocked"] is False
    assert r["content_len"] > 500
    assert r["title"]


@pytest.mark.integration
def test_public_article_with_browser_headers_no_cookie():
    r = fetch_public_article(DEFAULT_SAMPLE_URL, headers=BROWSER_HEADERS)
    assert r["status"] == 200
    assert r["blocked"] is False
    assert r["content_len"] > 500
