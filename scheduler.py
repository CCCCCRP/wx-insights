"""每周全自动调度：提醒 → 周一流水线（按时间先后执行）→ 休眠。

用法：
    python -m worker schedule [--dry-run] [--skip-login] [--now] [--once]

选项：
    --dry-run       只打印时间计划，不执行
    --skip-login    跳过扫码环节（token 已有效时可用）
    --now           立刻执行一次流水线（测试/手动触发）
    --once          跑完一次后退出，不进入永久休眠循环（常与 --now 联用）

流水线末尾：远程 DB 同步（未配置 BLOG_SSH_HOST 时自动跳过）。
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from worker.auth.token_manager import token_manager
from worker.config import (
    NOTIFY_EMAILS,
    SCHEDULE_PIPELINE_HOUR,
    SCHEDULE_PIPELINE_MINUTE,
    SCHEDULE_REMINDER_DAY,
    SCHEDULE_REMINDER_HOUR,
    SCHEDULE_REMINDER_MINUTE,
    db_sync_configured,
)
from worker.mail.mailer import MailerConfigError, mailer

logger = logging.getLogger(__name__)

TZ_CN = timezone(timedelta(hours=8))
WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

_PROXY_KEYS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
    "NO_PROXY", "no_proxy",
)


# ── 时间工具 ─────────────────────────────────────────────────────────────────

def now_cn() -> datetime:
    return datetime.now(TZ_CN)


def next_weekday_at(
    weekday: int,
    hour: int,
    minute: int,
    *,
    ref: datetime | None = None,
) -> datetime:
    """返回下一个指定 weekday（0=周一）的 hour:minute 时刻（北京时间）。"""
    n = ref if ref is not None else now_cn()
    days_ahead = (weekday - n.weekday()) % 7
    target = n.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if days_ahead == 0 and n >= target:
        days_ahead = 7
    target += timedelta(days=days_ahead)
    return target


def upcoming_schedule_events(
    *,
    ref: datetime | None = None,
    reminder_day: int = SCHEDULE_REMINDER_DAY,
    reminder_hour: int = SCHEDULE_REMINDER_HOUR,
    reminder_minute: int = SCHEDULE_REMINDER_MINUTE,
    pipeline_hour: int = SCHEDULE_PIPELINE_HOUR,
    pipeline_minute: int = SCHEDULE_PIPELINE_MINUTE,
) -> list[tuple[str, datetime]]:
    """按时间顺序返回下一次提醒与流水线（先发生者优先）。"""
    n = ref if ref is not None else now_cn()
    next_reminder = next_weekday_at(
        reminder_day, reminder_hour, reminder_minute, ref=n
    )
    next_pipeline = next_weekday_at(0, pipeline_hour, pipeline_minute, ref=n)
    return sorted(
        [("reminder", next_reminder), ("pipeline", next_pipeline)],
        key=lambda item: item[1],
    )


def sleep_until(dt: datetime, poll_secs: int = 60, dry_run: bool = False) -> None:
    """精确休眠到 dt，每 poll_secs 秒唤醒一次打印剩余时间。"""
    while True:
        remaining = (dt - now_cn()).total_seconds()
        if remaining <= 0:
            break
        hrs, rem = divmod(int(remaining), 3600)
        mins = rem // 60
        logger.info("休眠等待 %s … 剩余 %dh%02dm", dt.strftime("%m-%d %H:%M"), hrs, mins)
        if dry_run:
            logger.info("[dry-run] 跳过实际休眠")
            break
        time.sleep(min(poll_secs, remaining))


def _subprocess_env() -> dict:
    """子进程环境：去掉 shell 代理，避免 httpx SOCKS 报错。"""
    env = os.environ.copy()
    for key in _PROXY_KEYS:
        env.pop(key, None)
    return env


# ── 流水线步骤 ────────────────────────────────────────────────────────────────

def _run_cmd(args: list[str], step_name: str) -> int:
    """执行子进程命令，实时打印输出，返回退出码。"""
    cmd = [sys.executable, "-m", "worker"] + args
    logger.info("[%s] 开始: %s", step_name, " ".join(cmd))
    result = subprocess.run(
        cmd,
        cwd=Path(__file__).parent.parent,
        env=_subprocess_env(),
    )
    code = result.returncode
    if code != 0:
        logger.error("[%s] 退出码 %d", step_name, code)
    else:
        logger.info("[%s] 完成", step_name)
    return code


def send_reminder(dry_run: bool = False) -> None:
    """发送周四提醒邮件。"""
    next_monday = next_weekday_at(0, SCHEDULE_PIPELINE_HOUR, SCHEDULE_PIPELINE_MINUTE)
    date_str = next_monday.strftime("%m月%d日")
    logger.info("发送提醒邮件（下周周一 %s）-> %s", date_str, NOTIFY_EMAILS)
    if dry_run:
        logger.info("[dry-run] 跳过实际发送")
        return
    try:
        mailer.send_schedule_reminder(date_str, to=NOTIFY_EMAILS)
    except MailerConfigError as e:
        logger.warning("SMTP 未配置，跳过提醒邮件: %s", e)
    except Exception:
        logger.exception("发送提醒邮件失败")


def run_login(dry_run: bool = False) -> bool:
    """启动扫码登录，返回是否成功。"""
    if dry_run:
        logger.info("[dry-run] 跳过扫码登录")
        return True
    code = _run_cmd(["login", "--email"], "login")
    return code == 0


def run_crawl(dry_run: bool = False) -> bool:
    """抓取上周文章 + 正文。"""
    if dry_run:
        logger.info("[dry-run] 跳过 crawl")
        return True
    code = _run_cmd(["crawl", "--week", "last"], "crawl")
    return code == 0


def run_embed(dry_run: bool = False) -> bool:
    """补全缺失 embedding。"""
    if dry_run:
        logger.info("[dry-run] 跳过 embed")
        return True
    code = _run_cmd(["insight", "embed"], "embed")
    return code == 0


def run_insight(dry_run: bool = False) -> bool:
    """生成洞见报告（Phase A → B → C），自动发邮件；blog.publish_on_generate 时同步归档 HTML。"""
    if dry_run:
        logger.info("[dry-run] 跳过 insight")
        return True
    code = _run_cmd(["insight", "--week", "last"], "insight")
    return code == 0


def run_db_sync(dry_run: bool = False) -> bool:
    """将本地 PostgreSQL 同步到远程（未配置 SSH 主机时自动跳过）。"""
    if not db_sync_configured():
        logger.info("远程 DB 同步未配置（需 DATABASE_URL + BLOG_SSH_HOST），跳过")
        return True
    if dry_run:
        logger.info("[dry-run] 跳过 db-sync")
        return True
    try:
        from worker.db.sync_remote import sync_database_to_remote

        sync_database_to_remote()
        return True
    except Exception:
        logger.exception("db-sync 失败")
        return False


def run_weekly_pipeline(*, skip_login: bool = False, dry_run: bool = False) -> bool:
    """完整周一流水线：登录 → crawl → embed → insight → db-sync。返回是否全部成功。"""
    logger.info("========== 周一流水线开始 ==========")
    steps: list[tuple[str, bool]] = []

    if not skip_login:
        if token_manager.is_logged_in():
            logger.info("token 仍有效，跳过扫码登录")
            steps.append(("login", True))
        else:
            ok = run_login(dry_run)
            if not ok:
                logger.error("扫码登录失败，流水线中止")
                steps.append(("login", False))
                _send_pipeline_result(steps, dry_run)
                return False
            steps.append(("login", True))

    for name, fn in [("crawl", run_crawl), ("embed", run_embed), ("insight", run_insight)]:
        ok = fn(dry_run)
        steps.append((name, ok))
        if not ok:
            logger.warning("步骤 [%s] 返回非零，继续执行后续步骤", name)

    ok = run_db_sync(dry_run)
    steps.append(("db-sync", ok))
    if not ok:
        logger.warning("步骤 [db-sync] 失败")

    all_ok = all(ok for _, ok in steps)
    _send_pipeline_result(steps, dry_run)
    logger.info("========== 周一流水线完成 ==========")
    return all_ok


def _send_pipeline_result(steps: list[tuple[str, bool]], dry_run: bool) -> None:
    """发送流水线执行结果摘要邮件。"""
    if dry_run:
        return
    lines = ["本周自动调度流水线执行结果：\n"]
    for name, ok in steps:
        icon = "OK" if ok else "FAIL"
        lines.append(f"  [{icon}] {name}")
    all_ok = all(ok for _, ok in steps)
    subject = "[wxspirder] 本周流水线完成" if all_ok else "[wxspirder] 本周流水线部分失败"
    body = "\n".join(lines)
    try:
        mailer.send(subject, body, to=NOTIFY_EMAILS)
    except Exception:
        logger.exception("发送流水线结果邮件失败")


# ── 主循环 ────────────────────────────────────────────────────────────────────

def run_schedule_loop(
    *,
    skip_login: bool = False,
    dry_run: bool = False,
    now: bool = False,
    once: bool = False,
) -> None:
    """主调度循环。

    - 默认：永久循环（按时间顺序执行提醒与周一流水线 → 休眠）
    - --now --once：立刻跑一轮后退出
    - --dry-run：模拟一轮后退出
    """
    logger.info(
        "调度循环启动 | 提醒: %s %02d:%02d | 流水线: 周一 %02d:%02d | 收件人: %s",
        WEEKDAY_CN[SCHEDULE_REMINDER_DAY],
        SCHEDULE_REMINDER_HOUR, SCHEDULE_REMINDER_MINUTE,
        SCHEDULE_PIPELINE_HOUR, SCHEDULE_PIPELINE_MINUTE,
        NOTIFY_EMAILS,
    )

    if now:
        logger.info("--now: 立刻执行一次流水线")
        run_weekly_pipeline(skip_login=skip_login, dry_run=dry_run)
        if once or dry_run:
            logger.info("单次执行完成，退出")
            return

    while True:
        events = upcoming_schedule_events()
        next_reminder = next(
            dt for name, dt in events if name == "reminder"
        )
        next_pipeline = next(
            dt for name, dt in events if name == "pipeline"
        )
        logger.info(
            "下次提醒: %s  |  下次流水线: %s  |  执行顺序: %s",
            next_reminder.strftime("%Y-%m-%d %H:%M"),
            next_pipeline.strftime("%Y-%m-%d %H:%M"),
            " → ".join(
                f"{WEEKDAY_CN[dt.weekday()]} {dt.strftime('%m-%d %H:%M')}"
                for _, dt in events
            ),
        )

        for name, dt in events:
            sleep_until(dt, dry_run=dry_run)
            if name == "reminder":
                send_reminder(dry_run=dry_run)
            else:
                run_weekly_pipeline(skip_login=skip_login, dry_run=dry_run)

        if dry_run or once:
            logger.info("单次循环完成，退出")
            break
