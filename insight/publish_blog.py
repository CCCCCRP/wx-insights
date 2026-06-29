"""将洞见周报 HTML 归档发布到个人博客（Nginx 静态目录）。"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Iterable

from worker.crawl.week import week_range
from worker.insight.config import InsightSettings, load_insight_settings

logger = logging.getLogger(__name__)

_WEEK_DIR_RE = re.compile(r"^\d{4}-W\d{2}$")
_REPORT_HTML = "report.html"


def blog_publish_configured(settings: InsightSettings) -> bool:
    """博客归档是否已配置（enabled + 有效 ssh_host）。"""
    from worker.config import is_meaningful_host

    if not settings.blog_enabled:
        return False
    return is_meaningful_host(settings.blog_ssh_host)


def blog_publish_skip_reason(settings: InsightSettings) -> str | None:
    if not settings.blog_enabled:
        return "blog.enabled=false"
    from worker.config import is_meaningful_host

    if not is_meaningful_host(settings.blog_ssh_host):
        return "未配置有效 ssh_host（.env BLOG_SSH_HOST 或 insight.yaml blog.ssh_host）"
    return None


@dataclass(frozen=True)
class WeekArchive:
    week_id: str
    date_label: str
    n_primary: int | None = None
    n_themes: int | None = None


def resolve_week_id(week: str) -> str:
    if week == "last":
        _, _, week_id = week_range("last")
        return week_id
    _, _, week_id = week_range(week)
    return week_id


def _week_date_label(week_id: str) -> str:
    """ISO 周 → 可读日期范围，如 2026年6月16日 – 6月22日。"""
    start_ts, end_ts, _ = week_range(week_id)
    start = datetime.fromtimestamp(start_ts, timezone(timedelta(hours=8)))
    end = datetime.fromtimestamp(end_ts, timezone(timedelta(hours=8)))
    if start.year == end.year:
        if start.month == end.month:
            return f"{start.year}年{start.month}月{start.day}日 – {end.day}日"
        return f"{start.year}年{start.month}月{start.day}日 – {end.month}月{end.day}日"
    return (
        f"{start.year}年{start.month}月{start.day}日 – "
        f"{end.year}年{end.month}月{end.day}日"
    )
def _week_meta(week_dir: Path) -> tuple[int | None, int | None]:
    meta_path = week_dir / "report.meta.json"
    if not meta_path.is_file():
        return None, None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None
    n_primary = meta.get("n_primary")
    n_themes = meta.get("n_themes_report") or meta.get("n_themes")
    return (
        int(n_primary) if n_primary is not None else None,
        int(n_themes) if n_themes is not None else None,
    )


def collect_week_archive(insights_dir: Path, week_id: str) -> WeekArchive | None:
    week_dir = insights_dir / week_id
    if not (week_dir / _REPORT_HTML).is_file():
        return None
    n_primary, n_themes = _week_meta(week_dir)
    return WeekArchive(
        week_id=week_id,
        date_label=_week_date_label(week_id),
        n_primary=n_primary,
        n_themes=n_themes,
    )


def list_archives(insights_dir: Path) -> list[WeekArchive]:
    archives: list[WeekArchive] = []
    for path in sorted(insights_dir.iterdir()):
        if not path.is_dir() or not _WEEK_DIR_RE.match(path.name):
            continue
        archive = collect_week_archive(insights_dir, path.name)
        if archive:
            archives.append(archive)
    archives.sort(key=lambda item: item.week_id, reverse=True)
    return archives


def render_index_html(
    archives: Iterable[WeekArchive],
    *,
    base_url: str,
    site_title: str,
) -> str:
    base = base_url.rstrip("/")
    rows: list[str] = []
    for archive in archives:
        stats_bits: list[str] = []
        if archive.n_primary is not None:
            stats_bits.append(f"{archive.n_primary} 篇")
        if archive.n_themes is not None:
            stats_bits.append(f"{archive.n_themes} 主题")
        stats = " · ".join(stats_bits)
        href = f"{base}/insights/{escape(archive.week_id)}/{_REPORT_HTML}"
        meta = f'<span class="meta">{escape(stats)}</span>' if stats else ""
        rows.append(
            f'<li><a class="entry" href="{href}">'
            f"<strong>{escape(archive.date_label)}</strong>{meta}</a></li>"
        )
    body = "\n".join(rows) if rows else "<li>暂无归档</li>"
    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(site_title)}</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #fafafa;
      --card: #fff;
      --text: #1a1a1a;
      --muted: #666;
      --link: #2563eb;
      --border: #e5e7eb;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #0f1115;
        --card: #171a21;
        --text: #eef0f4;
        --muted: #9aa3b2;
        --link: #7cb3ff;
        --border: #2a3140;
      }}
    }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.6;
    }}
    main {{
      max-width: 720px;
      margin: 0 auto;
      padding: 2.5rem 1.25rem 4rem;
    }}
    h1 {{ font-size: 1.75rem; margin: 0 0 0.5rem; }}
    .subtitle {{ color: var(--muted); margin: 0 0 2rem; }}
    ul {{ list-style: none; padding: 0; margin: 0; display: grid; gap: 1rem; }}
    li {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 0;
    }}
    .entry {{
      display: block;
      padding: 1rem 1.15rem;
      color: inherit;
    }}
    .entry:hover strong {{ color: var(--link); }}
    .meta {{
      display: inline-block;
      margin-left: 0.5rem;
      color: var(--muted);
      font-size: 0.92rem;
      font-weight: normal;
    }}
    a {{ color: var(--link); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    footer {{ margin-top: 2rem; color: var(--muted); font-size: 0.85rem; }}
  </style>
</head>
<body>
  <main>
    <h1>{escape(site_title)}</h1>
    <p class="subtitle">公众号洞见周报，按自然周日期归档。</p>
    <ul>
{body}
    </ul>
    <footer>更新于 {updated}</footer>
  </main>
</body>
</html>
"""


def _ssh_target(settings: InsightSettings) -> str:
    host = settings.blog_ssh_host.strip()
    if "@" in host:
        return host
    user = settings.blog_ssh_user.strip() or "root"
    return f"{user}@{host}"


def _run_rsync(local_path: Path, remote_path: str, settings: InsightSettings, *, delete: bool = True) -> None:
    ssh_target = _ssh_target(settings)
    remote = remote_path if remote_path.endswith("/") else remote_path
    if local_path.is_dir() and not remote.endswith("/"):
        remote = f"{remote}/"
    ssh_opts = "-o StrictHostKeyChecking=no"
    if settings.blog_ssh_port:
        ssh_opts += f" -p {int(settings.blog_ssh_port)}"
    if settings.blog_ssh_identity:
        ssh_opts += f" -i {settings.blog_ssh_identity}"

    source = f"{local_path.as_posix()}/" if local_path.is_dir() else local_path.as_posix()
    rsync_cmd = [
        "rsync",
        "-avz",
        "-e",
        f"ssh {ssh_opts}",
        source,
        f"{ssh_target}:{remote}",
    ]
    if delete and local_path.is_dir():
        rsync_cmd.insert(2, "--delete")

    env = os.environ.copy()
    if settings.blog_ssh_password:
        sshpass = shutil.which("sshpass")
        if not sshpass:
            raise RuntimeError("已配置 BLOG_SSH_PASSWORD，但未安装 sshpass（brew install hudochenkov/sshpass/sshpass）")
        env["SSHPASS"] = settings.blog_ssh_password
        rsync_cmd = [sshpass, "-e"] + rsync_cmd

    logger.info("同步到 %s", remote)
    proc = subprocess.run(rsync_cmd, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"rsync 失败: {detail or proc.returncode}")


def publish_week(
    week: str = "last",
    *,
    settings: InsightSettings | None = None,
    dry_run: bool = False,
) -> str:
    settings = settings or load_insight_settings()
    reason = blog_publish_skip_reason(settings)
    if reason:
        raise RuntimeError(f"博客发布未配置：{reason}")

    week_id = resolve_week_id(week)
    week_dir = settings.insights_dir / week_id
    if not week_dir.is_dir():
        raise FileNotFoundError(f"找不到周报目录: {week_dir}")

    report_path = week_dir / _REPORT_HTML
    if not report_path.is_file():
        raise FileNotFoundError(f"{week_dir} 下没有 {_REPORT_HTML}")

    html_files = [report_path]

    archives = list_archives(settings.insights_dir)
    index_html = render_index_html(
        archives,
        base_url=settings.blog_base_url,
        site_title=settings.blog_index_title,
    )

    public_url = f"{settings.blog_base_url.rstrip('/')}/insights/{week_id}/report.html"
    if dry_run:
        names = ", ".join(path.name for path in html_files)
        logger.info("[dry-run] week=%s files=%s remote=%s", week_id, names, settings.blog_remote_dir)
        logger.info("[dry-run] index weeks=%d url=%s", len(archives), settings.blog_base_url + "/insights/")
        return public_url

    with tempfile.TemporaryDirectory(prefix="insight-publish-") as tmp:
        staging = Path(tmp)
        target_week = staging / week_id
        target_week.mkdir(parents=True)
        for path in html_files:
            shutil.copy2(path, target_week / path.name)

        (staging / "index.html").write_text(index_html, encoding="utf-8")

        remote_root = settings.blog_remote_dir.rstrip("/")
        _run_rsync(target_week, f"{remote_root}/{week_id}/", settings)
        _run_rsync(staging / "index.html", f"{remote_root}/index.html", settings, delete=False)

    logger.info("已发布 %s → %s", week_id, public_url)
    return public_url
