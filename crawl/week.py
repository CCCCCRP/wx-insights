"""按自然周 / 历史回填时间范围计算。"""

from __future__ import annotations

import re
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Tuple

TZ_CN = timezone(timedelta(hours=8))

_WEEK_ID_RE = re.compile(r"^(\d{4})-W(\d{1,2})$", re.IGNORECASE)


def week_id_from_ts(ts: int) -> str:
    """由 publish_time 得到 ISO 周标识，如 2026-W25。"""
    return datetime.fromtimestamp(ts, TZ_CN).strftime("%G-W%V")


def week_range(week: str = "last") -> Tuple[int, int, str]:
    """
    返回 (start_ts, end_ts, week_id)。
    week='last' → 上一自然周（周一 00:00 ～ 周日 23:59:59，UTC+8）。
    week='2026-W25' → 指定 ISO 自然周。
    """
    if week == "last":
        today = datetime.now(TZ_CN).date()
        this_monday = today - timedelta(days=today.weekday())
        last_monday = this_monday - timedelta(days=7)
        last_sunday = this_monday - timedelta(days=1)

        start = datetime.combine(last_monday, dt_time.min, tzinfo=TZ_CN)
        end = datetime.combine(last_sunday, dt_time(23, 59, 59), tzinfo=TZ_CN)
        week_id = last_monday.strftime("%G-W%V")
        return int(start.timestamp()), int(end.timestamp()), week_id

    m = _WEEK_ID_RE.match(week.strip())
    if m:
        year = int(m.group(1))
        week_num = int(m.group(2))
        monday = datetime.strptime(f"{year}-W{week_num:02d}-1", "%G-W%V-%u")
        monday = monday.replace(tzinfo=TZ_CN)
        sunday = monday + timedelta(days=6)
        start = datetime.combine(monday.date(), dt_time.min, tzinfo=TZ_CN)
        end = datetime.combine(sunday.date(), dt_time(23, 59, 59), tzinfo=TZ_CN)
        week_id = f"{year}-W{week_num:02d}"
        return int(start.timestamp()), int(end.timestamp()), week_id

    raise ValueError(f"暂不支持的 week 参数: {week}（可用 last 或 2026-W25）")


HISTORY_BACKFILL_DAYS = 180


def history_range(days: int = HISTORY_BACKFILL_DAYS) -> Tuple[int, int, str]:
    """近 N 天（默认 180，约半年）→ (start_ts, end_ts, period_id)。"""
    now = datetime.now(TZ_CN)
    start = now - timedelta(days=days)
    period_id = f"6month-{start.strftime('%Y%m%d')}-{now.strftime('%Y%m%d')}"
    return int(start.timestamp()), int(now.timestamp()), period_id


def year_range(days: int = HISTORY_BACKFILL_DAYS) -> Tuple[int, int, str]:
    """兼容旧名，等同 history_range。"""
    return history_range(days)


def context_range(week_start_ts: int, days: int = HISTORY_BACKFILL_DAYS) -> Tuple[int, int]:
    """Context 窗口：[now - days, week_start - 1]。"""
    now_ts = int(datetime.now(TZ_CN).timestamp())
    ctx_start = now_ts - days * 86400
    ctx_end = week_start_ts - 1
    return ctx_start, ctx_end
