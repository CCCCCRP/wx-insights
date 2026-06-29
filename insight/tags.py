"""L1 账号画像：从 accounts.yaml + DB 加载 insight_lens / insight_tags。"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import yaml

from worker.crawl.accounts import ACCOUNTS_FILE

logger = logging.getLogger(__name__)

LENS_VALUES = {"industry", "interview", "science", "business", "general"}

ACCOUNTS_HEADER = (
    "# 微信公众号订阅列表\n"
    "# 可只填 nickname（fakeid 留空）；crawl 时会自动 searchbiz 并写回本文件\n"
    "# insight_lens: industry | interview | science | business | general\n"
    "# insight_profile_locked: true 时跳过自动画像，保留手工配置"
)


def read_accounts_with_profile() -> List[Dict]:
    """读取 accounts.yaml，保留 insight 相关字段。"""
    if not ACCOUNTS_FILE.exists():
        return []
    data = yaml.safe_load(ACCOUNTS_FILE.read_text(encoding="utf-8")) or {}
    accounts: List[Dict] = []
    for item in data.get("accounts") or []:
        fakeid = (item.get("fakeid") or "").strip()
        nickname = (item.get("nickname") or "").strip()
        if not fakeid and not nickname:
            continue
        lens = (item.get("insight_lens") or "general").strip()
        if lens not in LENS_VALUES:
            lens = "general"
        tags = item.get("insight_tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        accounts.append({
            "fakeid": fakeid,
            "nickname": nickname,
            "insight_lens": lens,
            "insight_tags": list(tags),
            "insight_profile_locked": bool(item.get("insight_profile_locked", False)),
        })
    return accounts


def format_accounts_yaml(accounts: List[Dict]) -> str:
    """将账号列表序列化为 accounts.yaml 文本。"""
    lines = [ACCOUNTS_HEADER, "", "accounts:"]
    for acc in accounts:
        lines.append(f"  - fakeid: {acc.get('fakeid', '')}")
        lines.append(f"    nickname: {acc.get('nickname', '')}")
        if acc.get("insight_lens"):
            lines.append(f"    insight_lens: {acc['insight_lens']}")
        if acc.get("insight_tags"):
            tags_str = ", ".join(str(t) for t in acc["insight_tags"])
            lines.append(f"    insight_tags: [{tags_str}]")
        if acc.get("insight_profile_locked"):
            lines.append("    insight_profile_locked: true")
    return "\n".join(lines) + "\n"


def write_accounts_yaml(accounts: List[Dict]) -> None:
    ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ACCOUNTS_FILE.write_text(format_accounts_yaml(accounts), encoding="utf-8")


def lens_by_fakeid() -> Dict[str, str]:
    """fakeid → insight_lens 映射。"""
    return {a["fakeid"]: a.get("insight_lens", "general") for a in read_accounts_with_profile() if a.get("fakeid")}


def nickname_by_fakeid() -> Dict[str, str]:
    return {a["fakeid"]: a.get("nickname", "") for a in read_accounts_with_profile() if a.get("fakeid")}


def get_account_lens(fakeid: str) -> str:
    return lens_by_fakeid().get(fakeid, "general")


def get_account_nickname(fakeid: str) -> str:
    return nickname_by_fakeid().get(fakeid, "")


def write_profile_to_yaml(
    fakeid: str,
    *,
    insight_lens: str,
    insight_tags: List[str],
    locked: Optional[bool] = None,
) -> None:
    """将 auto 画像结果写回 accounts.yaml（保留其他字段）。"""
    if not ACCOUNTS_FILE.exists():
        raise FileNotFoundError(f"未找到 {ACCOUNTS_FILE}")

    data = yaml.safe_load(ACCOUNTS_FILE.read_text(encoding="utf-8")) or {}
    items = data.get("accounts") or []
    updated = False
    for item in items:
        if (item.get("fakeid") or "").strip() != fakeid:
            continue
        if item.get("insight_profile_locked"):
            logger.info("账号 %s 已 lock，跳过写回 yaml", fakeid)
            return
        item["insight_lens"] = insight_lens
        item["insight_tags"] = insight_tags
        if locked is not None:
            item["insight_profile_locked"] = locked
        updated = True
        break

    if not updated:
        logger.warning("yaml 中未找到 fakeid=%s，跳过写回", fakeid)
        return

    write_accounts_yaml(items)
    logger.info("已写回账号画像到 %s", ACCOUNTS_FILE)


def sync_profiles_to_db() -> int:
    """将 accounts.yaml 中的 insight 字段同步到 DB（manual 来源）。"""
    from worker.db import insight_repo, repo
    from worker.db.connection import database_available

    if not database_available():
        logger.warning("数据库不可用，跳过 yaml → DB 画像同步")
        return 0

    count = 0
    for acc in read_accounts_with_profile():
        fakeid = acc.get("fakeid")
        if not fakeid:
            continue
        repo.upsert_account(fakeid, acc.get("nickname") or fakeid)
        insight_repo.sync_account_profile_from_yaml(
            fakeid,
            insight_lens=acc["insight_lens"],
            insight_tags=acc["insight_tags"],
            locked=acc.get("insight_profile_locked", False),
        )
        count += 1
    if count:
        logger.info("已从 yaml 同步 %d 个账号画像到 DB", count)
    return count
