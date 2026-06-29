"""数据库读写操作。"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional

from worker.db.connection import get_conn

logger = logging.getLogger(__name__)


def upsert_account(fakeid: str, nickname: str) -> None:
    """插入或更新公众号记录（不覆盖 year_backfill_done）。"""
    sql = """
        INSERT INTO accounts (fakeid, nickname, updated_at)
        VALUES (%s, %s, now())
        ON CONFLICT (fakeid) DO UPDATE
            SET nickname   = EXCLUDED.nickname,
                updated_at = now()
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (fakeid, nickname))


def get_year_backfill_flags(fakeids: List[str]) -> Dict[str, bool]:
    """批量查询公众号是否已完成近一年回填。"""
    if not fakeids:
        return {}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT fakeid, year_backfill_done FROM accounts WHERE fakeid = ANY(%s)",
                (fakeids,),
            )
            return {row[0]: row[1] for row in cur.fetchall()}


def mark_year_backfill_done(fakeid: str) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE accounts SET year_backfill_done = TRUE, updated_at = now() WHERE fakeid = %s",
                (fakeid,),
            )


def _parse_crawled_dt(raw) -> Optional[datetime]:
    if isinstance(raw, str) and raw:
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            pass
    return None


def upsert_articles(fakeid: str, articles: List[Dict]) -> int:
    """批量 upsert 文章。
    优先按 aid 去重；aid 为空时按 link 去重；两者均无则跳过。
    """
    if not articles:
        return 0

    # 按 aid 去重（有 aid）
    sql_by_aid = """
        INSERT INTO articles (
            fakeid, aid, title, link, publish_time,
            digest, author, cover,
            content_fetched, content_len, content_source,
            plain_content, crawled_at, updated_at
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s, now()
        )
        ON CONFLICT (aid) WHERE aid IS NOT NULL DO UPDATE SET
            title           = EXCLUDED.title,
            link            = COALESCE(EXCLUDED.link, articles.link),
            author          = COALESCE(NULLIF(EXCLUDED.author, ''), articles.author),
            content_fetched = EXCLUDED.content_fetched,
            content_len     = EXCLUDED.content_len,
            content_source  = EXCLUDED.content_source,
            plain_content   = CASE
                                WHEN EXCLUDED.content_fetched AND EXCLUDED.plain_content IS NOT NULL
                                THEN EXCLUDED.plain_content
                                ELSE articles.plain_content
                              END,
            crawled_at      = EXCLUDED.crawled_at,
            updated_at      = now()
        WHERE NOT articles.content_fetched OR EXCLUDED.content_fetched
    """

    # 按 link 去重（无 aid 的旧数据兼容）
    sql_by_link = """
        INSERT INTO articles (
            fakeid, title, link, publish_time,
            digest, author, cover,
            content_fetched, content_len, content_source,
            plain_content, crawled_at, updated_at
        ) VALUES (
            %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s, now()
        )
        ON CONFLICT (link) WHERE link IS NOT NULL DO UPDATE SET
            title           = EXCLUDED.title,
            author          = COALESCE(NULLIF(EXCLUDED.author, ''), articles.author),
            content_fetched = EXCLUDED.content_fetched,
            content_len     = EXCLUDED.content_len,
            content_source  = EXCLUDED.content_source,
            plain_content   = CASE
                                WHEN EXCLUDED.content_fetched AND EXCLUDED.plain_content IS NOT NULL
                                THEN EXCLUDED.plain_content
                                ELSE articles.plain_content
                              END,
            crawled_at      = EXCLUDED.crawled_at,
            updated_at      = now()
        WHERE NOT articles.content_fetched OR EXCLUDED.content_fetched
    """

    rows_aid: List[tuple] = []
    rows_link: List[tuple] = []

    for a in articles:
        aid = a.get("aid") or None
        link = a.get("link") or None
        crawled_dt = _parse_crawled_dt(a.get("crawled_at"))

        if aid:
            rows_aid.append((
                fakeid, aid,
                a.get("title") or "", link,
                a.get("publish_time") or None,
                a.get("digest") or None, a.get("author") or None, a.get("cover") or None,
                bool(a.get("content_fetched")), int(a.get("content_len") or 0),
                a.get("content_source") or None, a.get("plain_content") or None,
                crawled_dt,
            ))
        elif link:
            rows_link.append((
                fakeid,
                a.get("title") or "", link,
                a.get("publish_time") or None,
                a.get("digest") or None, a.get("author") or None, a.get("cover") or None,
                bool(a.get("content_fetched")), int(a.get("content_len") or 0),
                a.get("content_source") or None, a.get("plain_content") or None,
                crawled_dt,
            ))

    count = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            if rows_aid:
                cur.executemany(sql_by_aid, rows_aid)
                count += cur.rowcount if cur.rowcount >= 0 else len(rows_aid)
            if rows_link:
                cur.executemany(sql_by_link, rows_link)
                count += cur.rowcount if cur.rowcount >= 0 else len(rows_link)
    return count


def find_existing_aids(aids: List[str]) -> set[str]:
    """批量查询数据库中已存在的 aid。"""
    clean = [a for a in aids if a]
    if not clean:
        return set()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT aid FROM articles WHERE aid = ANY(%s)", (clean,))
            return {row[0] for row in cur.fetchall()}


def find_existing_links(links: List[str]) -> set[str]:
    """批量查询数据库中已存在的 link（兼容旧数据）。"""
    clean = [link for link in links if link]
    if not clean:
        return set()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT link FROM articles WHERE link = ANY(%s)", (clean,))
            return {row[0] for row in cur.fetchall()}


def save_crawl_run(
    week_id: str,
    *,
    start_ts: int,
    end_ts: int,
    crawled_at: Optional[str] = None,
    stats: Optional[Dict] = None,
) -> int:
    """记录一次采集运行，返回新行 id。"""
    crawled_dt: Optional[datetime] = None
    if crawled_at:
        try:
            crawled_dt = datetime.fromisoformat(crawled_at)
        except ValueError:
            pass

    sql = """
        INSERT INTO crawl_runs (week_id, start_ts, end_ts, crawled_at, stats)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (
                week_id,
                start_ts,
                end_ts,
                crawled_dt,
                json.dumps(stats, ensure_ascii=False) if stats else None,
            ))
            row = cur.fetchone()
            return row[0] if row else -1
