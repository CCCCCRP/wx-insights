"""登录发信时间窗口（9:00～10:00 发邮件，10:00 后仅等待扫码）。"""

from __future__ import annotations

import logging
import time
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

TZ_CN = timezone(timedelta(hours=8))


def parse_hhmm(value: str) -> dt_time:
    parts = value.strip().split(":")
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0
    return dt_time(hour, minute)


def now_cn() -> datetime:
    return datetime.now(TZ_CN)


def today_at(t: dt_time) -> datetime:
    n = now_cn()
    return datetime.combine(n.date(), t, tzinfo=TZ_CN)


class LoginWindow:
    def __init__(
        self,
        start: str,
        end: str,
        grace_seconds: int,
        resend_interval: int,
        reminder_time: str = "",
    ) -> None:
        self.start = parse_hhmm(start)
        self.end = parse_hhmm(end)
        self.grace = timedelta(seconds=grace_seconds)
        self.resend_interval = resend_interval
        self.reminder = parse_hhmm(reminder_time) if reminder_time else None
        self.window_start = today_at(self.start)
        self.window_end = today_at(self.end)
        self.deadline = self.window_end + self.grace

    def wait_until_start(self) -> None:
        n = now_cn()
        if n < self.window_start:
            secs = (self.window_start - n).total_seconds()
            logger.info(
                "等待发信窗口开始 %s（约 %d 秒）...",
                self.start.strftime("%H:%M"),
                int(secs),
            )
            time.sleep(max(0, secs))

    def in_send_window(self, at: Optional[datetime] = None) -> bool:
        n = at or now_cn()
        return self.window_start <= n < self.window_end

    def past_deadline(self, at: Optional[datetime] = None) -> bool:
        return (at or now_cn()) >= self.deadline

    def can_resend(self, last_sent_ts: float) -> bool:
        return time.time() - last_sent_ts >= self.resend_interval

    def should_send_reminder(self, sent: bool, at: Optional[datetime] = None) -> bool:
        if sent or not self.reminder:
            return False
        n = at or now_cn()
        return n >= today_at(self.reminder) and self.in_send_window(n)

    def describe(self) -> str:
        return (
            f"发信窗口 {self.start.strftime('%H:%M')}～{self.end.strftime('%H:%M')}，"
            f"截止等待 {self.deadline.strftime('%H:%M')}，"
            f"重发间隔 {self.resend_interval}s"
        )
