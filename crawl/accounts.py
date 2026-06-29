"""公众号配置：读取 accounts.yaml，并按 nickname 回填 fakeid。"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import yaml

from worker.config import CONFIG_DIR

logger = logging.getLogger(__name__)

ACCOUNTS_FILE = CONFIG_DIR / "accounts.yaml"
ACCOUNTS_HEADER = (
    "# 微信公众号订阅列表\n"
    "# 可只填 nickname（fakeid 留空）；crawl 时会自动 searchbiz 并写回本文件\n"
)


def read_accounts_file() -> List[Dict[str, str]]:
    if not ACCOUNTS_FILE.exists():
        example = CONFIG_DIR / "accounts.example.yaml"
        hint = f"请复制示例：cp {example} {ACCOUNTS_FILE}" if example.is_file() else ""
        raise FileNotFoundError(f"未找到 {ACCOUNTS_FILE}" + (f"（{hint}）" if hint else ""))
    data = yaml.safe_load(ACCOUNTS_FILE.read_text(encoding="utf-8")) or {}
    accounts: List[Dict[str, str]] = []
    for item in data.get("accounts") or []:
        fakeid = (item.get("fakeid") or "").strip()
        nickname = (item.get("nickname") or "").strip()
        if not fakeid and not nickname:
            continue
        accounts.append({"fakeid": fakeid, "nickname": nickname})
    return accounts


def write_accounts_file(accounts: List[Dict[str, str]]) -> None:
    """写回 accounts.yaml，保留已有 insight_lens / insight_tags 等字段。"""
    from worker.insight.tags import write_accounts_yaml

    existing_by_key: Dict[str, Dict] = {}
    if ACCOUNTS_FILE.exists():
        data = yaml.safe_load(ACCOUNTS_FILE.read_text(encoding="utf-8")) or {}
        for item in data.get("accounts") or []:
            key = (item.get("fakeid") or "").strip() or (item.get("nickname") or "").strip()
            if key:
                existing_by_key[key] = dict(item)

    merged: List[Dict] = []
    for acc in accounts:
        key = (acc.get("fakeid") or "").strip() or (acc.get("nickname") or "").strip()
        base = dict(existing_by_key.get(key, {}))
        base["fakeid"] = acc.get("fakeid", "")
        base["nickname"] = acc.get("nickname", "")
        merged.append(base)

    write_accounts_yaml(merged)


def load_accounts(*, resolve: bool = False, searcher=None) -> List[Dict[str, str]]:
    accounts = read_accounts_file()
    missing = [a for a in accounts if not a.get("fakeid") and a.get("nickname")]
    if missing:
        if not resolve:
            names = ", ".join(a["nickname"] for a in missing)
            raise ValueError(f"以下账号缺少 fakeid: {names}（需要有效 token 才能自动解析）")
        accounts, _ = resolve_missing_fakeids(accounts, searcher=searcher, save=True)

    ready = [a for a in accounts if a.get("fakeid")]
    if not ready:
        raise ValueError("accounts.yaml 中没有有效账号")
    return ready


def resolve_missing_fakeids(
    accounts: Optional[List[Dict[str, str]]] = None,
    *,
    searcher=None,
    save: bool = True,
    dry_run: bool = False,
) -> Tuple[List[Dict[str, str]], List[str]]:
    from worker.crawl.biz_search import BizSearchError, format_candidates, pick_best_match

    items = read_accounts_file() if accounts is None else list(accounts)
    logs: List[str] = []
    changed = False

    for acc in items:
        if acc.get("fakeid") or not acc.get("nickname"):
            continue
        nickname = acc["nickname"]
        try:
            results = searcher.search(nickname)
        except BizSearchError as e:
            raise ValueError(f"搜索「{nickname}」失败: {e}") from e

        match = pick_best_match(nickname, results)
        if not match:
            hint = format_candidates(results) or "  (无结果)"
            raise ValueError(
                f"「{nickname}」匹配不唯一，请改精确名称或手动填 fakeid:\n{hint}"
            )

        resolved_nick = match.get("nickname") or nickname
        acc["fakeid"] = match["fakeid"]
        if resolved_nick != nickname:
            acc["nickname"] = resolved_nick
        changed = True
        msg = f"✓ {nickname} → fakeid={match['fakeid']} ({resolved_nick})"
        logs.append(msg)
        logger.info("%s", msg)

    if changed and save and not dry_run:
        write_accounts_file(items)
        logger.info("已写入 %s", ACCOUNTS_FILE)

    return items, logs


def add_account(nickname: str, *, searcher=None, dry_run: bool = False) -> Dict[str, str]:
    nickname = (nickname or "").strip()
    if not nickname:
        raise ValueError("nickname 不能为空")

    accounts = read_accounts_file()
    for acc in accounts:
        if acc.get("nickname") == nickname and acc.get("fakeid"):
            logger.info("已存在: %s (%s)", nickname, acc["fakeid"])
            return acc

    target_idx = None
    for i, acc in enumerate(accounts):
        if acc.get("nickname") == nickname:
            target_idx = i
            break
    if target_idx is None:
        accounts.append({"fakeid": "", "nickname": nickname})
        target_idx = len(accounts) - 1

    updated, _ = resolve_missing_fakeids(
        accounts,
        searcher=searcher,
        save=not dry_run,
        dry_run=dry_run,
    )
    result = updated[target_idx]
    if not result.get("fakeid"):
        raise ValueError(f"未能解析 fakeid: {nickname}")
    return result
