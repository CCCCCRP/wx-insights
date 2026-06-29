"""Phase C 后验校验：aid / link 引用检查。"""
from __future__ import annotations

import re
from typing import Dict, List, Set

from worker.db import insight_repo
from worker.db.connection import database_available

LINK_RE = re.compile(r"\[([^\]]*)\]\((https://mp\.weixin\.qq\.com/s/[^)]+)\)")


def validate_report(
    report_md: str,
    primary_aids: Set[str],
    primary_links: Dict[str, str] | None = None,
) -> List[str]:
    """
    检查 report 中的微信链接是否来自 Primary 集合。
    primary_links: aid → link
    """
    warnings: List[str] = []
    primary_links = primary_links or {}
    link_to_aid = {v: k for k, v in primary_links.items() if v}

    for match in LINK_RE.finditer(report_md):
        title, link = match.group(1), match.group(2)
        aid = link_to_aid.get(link)

        if not aid and database_available():
            try:
                aid = insight_repo.lookup_aid_by_link(link)
            except Exception:
                pass

        if aid and aid not in primary_aids:
            warnings.append(f"引用了非 Primary 文章: {title[:40]} ({aid})")
        elif not aid:
            # 无法反查 aid 的链接仅 warn，不阻断
            warnings.append(f"无法验证来源: {title[:40]} ({link[:60]}...)")

    return warnings
