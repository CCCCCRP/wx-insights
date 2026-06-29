from pathlib import Path

from worker.crawl.archive import is_detailed_archive, load_cached_content


def test_is_detailed_archive_true(tmp_path: Path):
    p = tmp_path / "a.txt"
    p.write_text(
        "---\ncontent_fetched: true\ncontent_len: 500\n---\n\n" + ("正文" * 100),
        encoding="utf-8",
    )
    assert is_detailed_archive(p, min_len=200) is True


def test_is_detailed_archive_digest_only(tmp_path: Path):
    p = tmp_path / "a.txt"
    p.write_text(
        "---\ncontent_fetched: false\ncontent_len: 10\n---\n\n只有摘要\n\n原文: http://x",
        encoding="utf-8",
    )
    assert is_detailed_archive(p) is False


def test_load_cached_content(tmp_path: Path):
    p = tmp_path / "a.txt"
    p.write_text(
        "---\ncontent_fetched: true\ncontent_len: 5\ncontent_source: public\n"
        "crawled_at: 2026-06-26T10:00:00+08:00\n---\n\nhello\n",
        encoding="utf-8",
    )
    article = {}
    assert load_cached_content(article, p) is True
    assert article["plain_content"] == "hello"
    assert article["content_source"] == "public"
    assert article["crawled_at"] == "2026-06-26T10:00:00+08:00"


def test_load_cached_content_merges_frontmatter(tmp_path: Path):
    p = tmp_path / "a.txt"
    p.write_text(
        "---\n"
        "title: 完整标题\n"
        "link: https://mp.weixin.qq.com/s/abc\n"
        "fakeid: FAKE123\n"
        "publish_time: 1781482356\n"
        "content_fetched: true\ncontent_len: 5\ncontent_source: public\n"
        "crawled_at: 2026-06-26T10:00:00+08:00\n---\n\nhello\n",
        encoding="utf-8",
    )
    article = {"title": "API标题"}
    assert load_cached_content(article, p) is True
    assert article["title"] == "完整标题"
    assert article["link"] == "https://mp.weixin.qq.com/s/abc"
    assert article["fakeid"] == "FAKE123"
    assert article["publish_time"] == 1781482356
    assert article["plain_content"] == "hello"


def test_write_article_txt_includes_crawled_at(tmp_path: Path):
    from worker.crawl.archive import write_article_txt

    write_article_txt(
        tmp_path,
        "返朴",
        {
            "title": "测试",
            "fakeid": "x",
            "publish_time": 1781569061,
            "link": "http://x",
            "digest": "d",
            "plain_content": "正文",
            "content_fetched": True,
            "content_len": 2,
            "content_source": "public",
            "crawled_at": "2026-06-26T22:00:00+08:00",
        },
    )
    text = (tmp_path / "返朴" / "20260616_测试.txt").read_text(encoding="utf-8")
    assert "crawled_at: 2026-06-26T22:00:00+08:00" in text
