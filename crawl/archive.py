from __future__ import annotations

import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from worker.config import ARCHIVE_MIN_CONTENT_LEN

TZ_CN = timezone(timedelta(hours=8))


def now_cn_iso() -> str:
    return datetime.now(TZ_CN).isoformat(timespec="seconds")


def _safe_filename(title: str) -> str:
    name = re.sub(r'[/\\:*?"<>|\n\r\t]', " ", title or "untitled")
    name = " ".join(name.split())[:60].strip() or "untitled"
    return name


def article_txt_path(archive_dir: Path, nickname: str, article: Dict) -> Path:
    sub = archive_dir / _safe_filename(nickname)
    pt = article.get("publish_time", 0)
    date_str = datetime.fromtimestamp(pt, TZ_CN).strftime("%Y%m%d") if pt else "unknown"
    fname = f"{date_str}_{_safe_filename(article.get('title', ''))}.txt"
    return sub / fname


def parse_article_txt(text: str) -> Tuple[Dict[str, str], str]:
    """解析 frontmatter + 正文。"""
    meta: Dict[str, str] = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].splitlines():
                line = line.strip()
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            body = parts[2].lstrip("\n")
    return meta, body


def is_detailed_archive(
    path: Path,
    *,
    min_len: int = ARCHIVE_MIN_CONTENT_LEN,
) -> bool:
    if not path.is_file():
        return False
    meta, body = parse_article_txt(path.read_text(encoding="utf-8"))
    if meta.get("content_fetched", "").lower() != "true":
        return False
    try:
        declared = int(meta.get("content_len") or 0)
    except ValueError:
        declared = 0
    body_len = len(body.strip())
    effective = max(declared, body_len)
    return effective >= min_len


def load_cached_content(article: Dict, path: Path) -> bool:
    """从已有 txt 恢复元数据与正文到 article。"""
    meta, body = parse_article_txt(path.read_text(encoding="utf-8"))
    plain = body.strip()
    if not plain:
        return False

    for key in ("title", "fakeid", "aid", "link", "digest", "author", "cover"):
        if meta.get(key):
            article[key] = meta[key]
    if meta.get("publish_time"):
        try:
            article["publish_time"] = int(meta["publish_time"])
        except ValueError:
            pass

    article["plain_content"] = plain
    article["content_fetched"] = True
    try:
        article["content_len"] = int(meta.get("content_len") or len(plain))
    except ValueError:
        article["content_len"] = len(plain)
    article["content_source"] = meta.get("content_source") or "local"
    if meta.get("crawled_at"):
        article["crawled_at"] = meta["crawled_at"]
    return True


def write_manifest(
    archive_dir: Path,
    *,
    week_id: str,
    start_ts: int,
    end_ts: int,
    accounts: List[Dict],
    stats: Dict,
    crawled_at: Optional[str] = None,
) -> None:
    archive_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "week_id": week_id,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "start": datetime.fromtimestamp(start_ts, TZ_CN).isoformat(),
        "end": datetime.fromtimestamp(end_ts, TZ_CN).isoformat(),
        "crawled_at": crawled_at or now_cn_iso(),
        "accounts": accounts,
        "stats": stats,
    }
    (archive_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_article_txt(archive_dir: Path, nickname: str, article: Dict) -> Path:
    path = article_txt_path(archive_dir, nickname, article)
    path.parent.mkdir(parents=True, exist_ok=True)

    pt = article.get("publish_time", 0)
    plain = (article.get("plain_content") or "").strip()
    fetched = article.get("content_fetched", False)
    body = plain if fetched and plain else (article.get("digest", "") or "")
    crawled_at = article.get("crawled_at") or now_cn_iso()

    lines = [
        "---",
        f"title: {article.get('title', '')}",
        f"nickname: {nickname}",
        f"fakeid: {article.get('fakeid', '')}",
        f"aid: {article.get('aid', '')}",
        f"publish_time: {pt}",
        f"link: {article.get('link', '')}",
        f"digest: {article.get('digest', '')}",
        f"author: {article.get('author', '')}",
        f"cover: {article.get('cover', '')}",
        f"content_fetched: {str(fetched).lower()}",
        f"content_len: {article.get('content_len', len(plain))}",
        f"content_source: {article.get('content_source', '')}",
        f"crawled_at: {crawled_at}",
        "---",
        "",
        body,
        "",
    ]
    if not fetched:
        err = article.get("content_error", "")
        if err:
            lines.extend([f"# 正文未抓取: {err}", ""])
        lines.append(f"原文: {article.get('link', '')}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path
