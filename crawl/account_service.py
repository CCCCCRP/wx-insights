from __future__ import annotations

import logging
import sys

from worker.auth.token_manager import token_manager
from worker.config import API_DIR
from worker.crawl.accounts import (
    add_account,
    read_accounts_file,
    resolve_missing_fakeids,
)
from worker.crawl.biz_search import BizSearcher, format_candidates

logger = logging.getLogger(__name__)


class AccountService:
    def _require_login(self) -> dict:
        if not token_manager.is_logged_in():
            raise RuntimeError("无有效 token，请先: python -m worker login --email")
        if str(API_DIR) not in sys.path:
            sys.path.insert(0, str(API_DIR))
        from utils.auth_manager import auth_manager  # noqa: E402

        creds = auth_manager.get_credentials()
        if not creds:
            raise RuntimeError("无凭证")
        return creds

    def _searcher(self) -> BizSearcher:
        creds = self._require_login()
        return BizSearcher(creds["token"], creds["cookie"])

    def resolve(self, *, dry_run: bool = False) -> int:
        accounts = read_accounts_file()
        missing = [a for a in accounts if not a.get("fakeid") and a.get("nickname")]
        if not missing:
            logger.info("所有账号已有 fakeid，无需回填")
            return 0

        with self._searcher() as searcher:
            resolve_missing_fakeids(accounts, searcher=searcher, save=not dry_run, dry_run=dry_run)
        return 0

    def add(self, nickname: str, *, dry_run: bool = False) -> int:
        with self._searcher() as searcher:
            add_account(nickname, searcher=searcher, dry_run=dry_run)
        return 0

    def search(self, query: str) -> int:
        with self._searcher() as searcher:
            results = searcher.search(query)
        if not results:
            logger.warning("未找到: %s", query)
            return 1
        logger.info("%s", format_candidates(results))
        return 0
