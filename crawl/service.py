from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List

from worker.auth.token_manager import TokenManager, token_manager
from worker.config import API_DIR, ARCHIVE_ROOT
from worker.crawl.accounts import load_accounts
from worker.db import (
    init_db,
    upsert_account,
    upsert_articles,
    save_crawl_run,
    find_existing_aids,
    find_existing_links,
    get_year_backfill_flags,
    mark_year_backfill_done,
)
from worker.crawl.archive import (
    article_txt_path,
    is_detailed_archive,
    load_cached_content,
    now_cn_iso,
    write_article_txt,
    write_manifest,
)
from worker.crawl.biz_search import BizSearcher
from worker.crawl.content_fetcher import ArticlePageFetcher
from worker.crawl.fetcher import ArticleFetcher, FetchError
from worker.crawl.week import week_range, year_range, week_id_from_ts

logger = logging.getLogger(__name__)

YEAR_MAX_PAGES = 80  # 约 1600 篇


class CrawlService:
    """按周采集；首次对公众号回填近半年历史。"""

    def __init__(self, tokens: TokenManager | None = None) -> None:
        self.tokens = tokens or token_manager

    def run(
        self,
        week: str = "last",
        *,
        wait_for_login: bool = True,
        wait_timeout: int = 3600,
        poll_interval: int = 60,
        fetch_content: bool = True,
        force_content: bool = False,
    ) -> int:
        if not self._ensure_login(wait_for_login, wait_timeout, poll_interval):
            logger.error("无有效 token，采集取消。请先: python -m worker login --email")
            return 1

        start_ts, end_ts, week_id = week_range(week)
        year_start, year_end, year_period = year_range()
        logger.info(
            "采集周期 %s: %s ~ %s",
            week_id,
            time.strftime("%Y-%m-%d", time.localtime(start_ts)),
            time.strftime("%Y-%m-%d", time.localtime(end_ts)),
        )

        creds = self._get_creds()
        init_db()
        with BizSearcher(creds["token"], creds["cookie"]) as biz_searcher:
            accounts = load_accounts(resolve=True, searcher=biz_searcher)
            for acc in accounts:
                upsert_account(acc["fakeid"], acc["nickname"])

            year_flags = get_year_backfill_flags([a["fakeid"] for a in accounts])
            archive_dir = ARCHIVE_ROOT / week_id
            crawl_time = now_cn_iso()
            all_stats: Dict[str, int] = {}
            total = 0
            content_ok = 0
            content_fail = 0
            content_public = 0
            content_token = 0
            content_cached = 0
            db_backfill = 0
            year_backfill_accounts = 0

            with ArticleFetcher(creds["token"], creds["cookie"]) as fetcher, ArticlePageFetcher(
                wechat_token=creds["token"],
                wechat_cookie=creds["cookie"],
            ) as page_fetcher:
                for acc in accounts:
                    fakeid = acc["fakeid"]
                    nickname = acc["nickname"]

                    if not year_flags.get(fakeid, False):
                        self._run_year_backfill(
                            fetcher=fetcher,
                            page_fetcher=page_fetcher,
                            fakeid=fakeid,
                            nickname=nickname,
                            year_start=year_start,
                            year_end=year_end,
                            year_period=year_period,
                            crawl_time=crawl_time,
                            fetch_content=fetch_content,
                            force_content=force_content,
                        )
                        year_backfill_accounts += 1

                    try:
                        articles = fetcher.fetch_week(fakeid, start_ts, end_ts)
                    except FetchError as e:
                        logger.error("%s 拉取失败: %s", nickname, e)
                        all_stats[nickname] = 0
                        continue

                    proc = self._process_articles(
                        fakeid=fakeid,
                        nickname=nickname,
                        articles=articles,
                        archive_dir_for=lambda _a, d=archive_dir: d,
                        page_fetcher=page_fetcher,
                        crawl_time=crawl_time,
                        fetch_content=fetch_content,
                        force_content=force_content,
                    )
                    saved = upsert_articles(fakeid, articles)
                    all_stats[nickname] = saved
                    total += saved
                    content_ok += proc["content_ok"]
                    content_fail += proc["content_fail"]
                    content_public += proc["content_public"]
                    content_token += proc["content_token"]
                    content_cached += proc["cached"]
                    db_backfill += proc["db_backfill"]

                    if fetch_content:
                        extra_parts = []
                        if proc["cached"]:
                            extra_parts.append(f"本地跳过 {proc['cached']}")
                        if proc["db_backfill"]:
                            extra_parts.append(f"txt 回填库 {proc['db_backfill']}")
                        extra = f"，{'，'.join(extra_parts)}" if extra_parts else ""
                        logger.info(
                            "  %s: %d 篇（入库 %d，正文 %d%s）",
                            nickname,
                            len(articles),
                            saved,
                            proc["content_ok"],
                            extra,
                        )
                    else:
                        logger.info("  %s: %d 篇（入库 %d 篇）", nickname, len(articles), saved)
                    time.sleep(2)

            run_stats = {
                "by_account": all_stats,
                "total_saved": total,
                "content_fetched": content_ok,
                "content_failed": content_fail,
                "content_public": content_public,
                "content_token": content_token,
                "content_cached": content_cached,
                "db_backfill": db_backfill,
                "year_backfill_accounts": year_backfill_accounts,
            }
            write_manifest(
                archive_dir,
                week_id=week_id,
                start_ts=start_ts,
                end_ts=end_ts,
                accounts=accounts,
                stats=run_stats,
                crawled_at=crawl_time,
            )
            save_crawl_run(
                week_id,
                start_ts=start_ts,
                end_ts=end_ts,
                crawled_at=crawl_time,
                stats=run_stats,
            )
            if fetch_content:
                logger.info(
                    "完成。共入库 %d 篇，正文 %d 篇（公开 %d，token %d，"
                    "本地跳过 %d，txt 回填库 %d），失败 %d 篇",
                    total,
                    content_ok,
                    content_public,
                    content_token,
                    content_cached,
                    db_backfill,
                    content_fail,
                )
            else:
                logger.info("完成。共入库 %d 篇", total)
            if year_backfill_accounts:
                logger.info("本次近半年回填公众号: %d 个", year_backfill_accounts)
            logger.info("归档目录: %s", archive_dir)
        return 0

    def _run_year_backfill(
        self,
        *,
        fetcher: ArticleFetcher,
        page_fetcher: ArticlePageFetcher,
        fakeid: str,
        nickname: str,
        year_start: int,
        year_end: int,
        year_period: str,
        crawl_time: str,
        fetch_content: bool,
        force_content: bool,
    ) -> None:
        logger.info(
            "  %s: 近半年回填 (%s ~ %s)...",
            nickname,
            time.strftime("%Y-%m-%d", time.localtime(year_start)),
            time.strftime("%Y-%m-%d", time.localtime(year_end)),
        )
        try:
            articles = fetcher.fetch_range(
                fakeid, year_start, year_end, max_pages=YEAR_MAX_PAGES
            )
        except FetchError as e:
            logger.error("%s 近半年回填失败: %s", nickname, e)
            logger.warning("  %s: 近半年回填失败，下次重试", nickname)
            return

        if articles:
            proc = self._process_articles(
                fakeid=fakeid,
                nickname=nickname,
                articles=articles,
                archive_dir_for=lambda a: ARCHIVE_ROOT / week_id_from_ts(
                    a.get("publish_time") or 0
                ),
                page_fetcher=page_fetcher,
                crawl_time=crawl_time,
                fetch_content=fetch_content,
                force_content=force_content,
            )
            saved = upsert_articles(fakeid, articles)
            logger.info(
                "  %s: 近半年回填 %d 篇（入库 %d，正文 %d）",
                nickname,
                len(articles),
                saved,
                proc["content_ok"],
            )
        else:
            logger.info("  %s: 近半年无文章", nickname)

        mark_year_backfill_done(fakeid)

    def _process_articles(
        self,
        *,
        fakeid: str,
        nickname: str,
        articles: List[Dict],
        archive_dir_for: Callable[[Dict], Path],
        page_fetcher: ArticlePageFetcher,
        crawl_time: str,
        fetch_content: bool,
        force_content: bool,
    ) -> Dict[str, int]:
        nick_cached = 0
        nick_db_backfill = 0
        content_fail = 0

        existing_aids = find_existing_aids(
            [a.get("aid") for a in articles if a.get("aid")]
        )
        existing_links = find_existing_links(
            [a.get("link") for a in articles if a.get("link") and not a.get("aid")]
        )

        for a in articles:
            a["fakeid"] = fakeid
            archive_dir = archive_dir_for(a)
            txt_path = article_txt_path(archive_dir, nickname, a)
            skip_write = False

            if fetch_content and a.get("link"):
                if not force_content and is_detailed_archive(txt_path):
                    load_cached_content(a, txt_path)
                    aid = a.get("aid")
                    link = a.get("link")
                    in_db = (
                        (aid and aid in existing_aids)
                        or (not aid and link and link in existing_links)
                    )
                    if not in_db:
                        nick_db_backfill += 1
                        logger.info(
                            "txt 存在但库中无记录，将从 txt 回填: %s",
                            a.get("title") or link,
                        )
                    nick_cached += 1
                    skip_write = True
                    logger.info("跳过抓取（本地已有正文）: %s", txt_path.name)
                else:
                    page_fetcher.enrich(a)
                    if not a.get("content_fetched"):
                        content_fail += 1

            if not skip_write:
                a.setdefault("crawled_at", crawl_time)
                write_article_txt(archive_dir, nickname, a)

        return {
            "content_ok": sum(1 for x in articles if x.get("content_fetched")),
            "content_fail": content_fail,
            "content_public": sum(1 for x in articles if x.get("content_source") == "public"),
            "content_token": sum(1 for x in articles if x.get("content_source") == "token"),
            "cached": nick_cached,
            "db_backfill": nick_db_backfill,
        }

    def _ensure_login(
        self,
        wait: bool,
        timeout: int,
        poll_interval: int,
    ) -> bool:
        deadline = time.time() + timeout
        while True:
            if self.tokens.is_logged_in():
                return True
            if not wait or time.time() >= deadline:
                return False
            logger.info("等待有效 token...（%ds 后超时）", int(deadline - time.time()))
            time.sleep(poll_interval)

    def _get_creds(self) -> dict:
        if str(API_DIR) not in sys.path:
            sys.path.insert(0, str(API_DIR))
        from utils.auth_manager import auth_manager  # noqa: E402

        creds = auth_manager.get_credentials()
        if not creds:
            raise RuntimeError("无凭证")
        return creds
