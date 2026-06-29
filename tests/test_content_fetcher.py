"""ArticlePageFetcher 单元测试（不联网）。"""
from __future__ import annotations

from worker.crawl.content_fetcher import ArticlePageFetcher


def test_enrich_without_link():
    article = {"title": "t", "digest": "摘要"}
    with ArticlePageFetcher(delay=0) as fetcher:
        out = fetcher.enrich(article)
    assert out["content_fetched"] is False
    assert out["content_error"] == "no link"


def test_apply_public_result():
    fetcher = ArticlePageFetcher(delay=0)
    article = {"title": "t"}
    ok = fetcher._apply_public_result(
        article,
        {"blocked": False, "content": "正文内容", "content_len": 4},
    )
    assert ok is True
    assert article["content_source"] == "public"
    assert article["plain_content"] == "正文内容"


def test_token_fallback_when_public_blocked(monkeypatch):
    fetcher = ArticlePageFetcher(
        wechat_token="tok",
        wechat_cookie="a=1",
        delay=0,
        retries=0,
    )
    html = """
    <script>window.item_show_type = '0';</script>
    <div id="js_content"><p>token 正文</p></div>
    <script></script>
    """

    monkeypatch.setattr(fetcher, "_fetch_public", lambda url: {
        "blocked": True,
        "content": "",
        "content_len": 0,
    })
    monkeypatch.setattr(fetcher, "_fetch_with_token", lambda url: html)
    monkeypatch.setattr(fetcher, "_load_api_utils", lambda: True)
    monkeypatch.setattr(
        fetcher,
        "_process_article_content",
        lambda h, proxy_base_url=None: {
            "content": "<p>token 正文</p>",
            "plain_content": "token 正文",
        },
    )
    fetcher._is_article_unavailable = lambda h: False
    fetcher._has_article_content = lambda h: True
    fetcher._api_loaded = True

    article = {"link": "https://mp.weixin.qq.com/s/abc", "title": "t"}
    out = fetcher.enrich(article)
    assert out["content_fetched"] is True
    assert out["content_source"] == "token"
    assert out["plain_content"] == "token 正文"
