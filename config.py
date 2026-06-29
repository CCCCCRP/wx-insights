from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

WORKER_ROOT = Path(__file__).resolve().parent
CONFIG_DIR = WORKER_ROOT / "config"
DATA_DIR = WORKER_ROOT / "data"
ARCHIVE_ROOT = DATA_DIR / "archive"

_worker_env = WORKER_ROOT / ".env"
if _worker_env.exists():
    load_dotenv(_worker_env, override=True)


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip().strip('"').strip("'")


LOG_DIR = Path(_env("LOG_DIR") or str(DATA_DIR / "logs")).expanduser()
LOG_FILE = Path(_env("LOG_FILE") or str(LOG_DIR / "worker.log")).expanduser()
LOG_LEVEL = _env("LOG_LEVEL", "INFO").upper()
LOG_CONSOLE = _env("LOG_CONSOLE", "true").lower() in ("1", "true", "yes")
# 分模块日志：crawl.log / insight.log / auth.log / schedule.log
LOG_SPLIT = _env("LOG_SPLIT", "true").lower() in ("1", "true", "yes")
# 总览日志 worker.log：写入全量时间线（与分项日志并存）
LOG_AGGREGATE = _env("LOG_AGGREGATE", "true").lower() in ("1", "true", "yes")

# 微信凭证服务目录（见 env.example WECHAT_API_DIR）
_api_dir = _env("WECHAT_API_DIR")
API_DIR = Path(_api_dir).expanduser().resolve() if _api_dir else Path()


# ── 邮件收件人（list，逗号分隔）────────────────────────────────────────────
# NOTIFY_EMAILS=you@qq.com,you@zte.com.cn
# NOTIFY_EMAIL 仍兼容，会合并进 NOTIFY_EMAILS


def parse_email_list(raw: str) -> list[str]:
    """解析逗号/分号分隔的邮箱列表，去重保序。"""
    seen: set[str] = set()
    result: list[str] = []
    for part in raw.replace(";", ",").split(","):
        addr = part.strip()
        if addr and addr not in seen:
            seen.add(addr)
            result.append(addr)
    return result


def _load_notify_emails() -> list[str]:
    emails = parse_email_list(_env("NOTIFY_EMAILS"))
    legacy = _env("NOTIFY_EMAIL")
    if legacy and legacy not in emails:
        emails.append(legacy)
    return emails


NOTIFY_EMAILS: list[str] = _load_notify_emails()
# 兼容旧代码引用
NOTIFY_EMAIL: str = NOTIFY_EMAILS[0] if NOTIFY_EMAILS else ""
SMTP_HOST = _env("SMTP_HOST")
SMTP_PORT = int(_env("SMTP_PORT", "465") or "465")
SMTP_USER = _env("SMTP_USER")
SMTP_PASSWORD = _env("SMTP_PASSWORD")
SMTP_FROM = _env("SMTP_FROM") or SMTP_USER
SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "true").lower() in ("1", "true", "yes")

LOGIN_POLL_INTERVAL = int(os.getenv("LOGIN_POLL_INTERVAL", "3"))
# 旧逻辑：启动后固定秒数超时（仅 --timeout 时使用）
LOGIN_TIMEOUT = int(os.getenv("LOGIN_TIMEOUT", "600"))
# 发信窗口：9:00～10:00 可发/重发邮件；10:00 后不再发信，继续等到 grace 结束
LOGIN_WINDOW_START = _env("LOGIN_WINDOW_START", "09:00")
LOGIN_WINDOW_END = _env("LOGIN_WINDOW_END", "10:00")
LOGIN_GRACE_SECONDS = int(_env("LOGIN_GRACE_SECONDS", "600") or "600")
QR_RESEND_INTERVAL = int(_env("QR_RESEND_INTERVAL", "120") or "120")
LOGIN_REMINDER_TIME = _env("LOGIN_REMINDER_TIME", "09:30")
# 兼容旧配置名
QR_REFRESH_SECONDS = int(os.getenv("QR_REFRESH_SECONDS", str(QR_RESEND_INTERVAL)))

# 临时登录页（仅配置了公网地址时才写入邮件）
LOGIN_SERVER_HOST = _env("LOGIN_SERVER_HOST", "0.0.0.0")
LOGIN_SERVER_PORT = int(_env("LOGIN_SERVER_PORT", "8765") or "8765")
LOGIN_PUBLIC_URL = _env("LOGIN_PUBLIC_URL")

# ── 每周自动调度 ──────────────────────────────────────────────────────────
# 提醒发送日（0=周一 … 6=周日，默认周四=3）及时间
SCHEDULE_REMINDER_DAY = int(_env("SCHEDULE_REMINDER_DAY", "3") or "3")
SCHEDULE_REMINDER_HOUR = int(_env("SCHEDULE_REMINDER_HOUR", "20") or "20")
SCHEDULE_REMINDER_MINUTE = int(_env("SCHEDULE_REMINDER_MINUTE", "0") or "0")
# 周一流水线启动时间（等待扫码窗口由 LOGIN_WINDOW_* 控制）
SCHEDULE_PIPELINE_HOUR = int(_env("SCHEDULE_PIPELINE_HOUR", "9") or "9")
SCHEDULE_PIPELINE_MINUTE = int(_env("SCHEDULE_PIPELINE_MINUTE", "0") or "0")

# 文章页正文抓取（/s/ 公开链接，无需 token）
ARTICLE_FETCH_DELAY = float(_env("ARTICLE_FETCH_DELAY", "1.5") or "1.5")
ARTICLE_FETCH_RETRIES = int(_env("ARTICLE_FETCH_RETRIES", "2") or "2")
ARTICLE_FETCH_TIMEOUT = float(_env("ARTICLE_FETCH_TIMEOUT", "30") or "30")
# 本地 txt 正文达到此字数且 content_fetched=true 时跳过网页抓取
ARCHIVE_MIN_CONTENT_LEN = int(_env("ARCHIVE_MIN_CONTENT_LEN", "200") or "200")

# ── 数据库远程同步（本地 → 远程 wxspirder-pg）────────────────────────────

def is_meaningful_host(host: str) -> bool:
    """是否为有效的远程主机（非空、非文档占位符）。"""
    h = (host or "").strip()
    if not h:
        return False
    lower = h.lower()
    if lower in ("localhost", "127.0.0.1", "::1"):
        return False
    if lower.startswith("your-"):
        return False
    if "example.com" in lower:
        return False
    return True


def db_sync_configured() -> bool:
    """远程 DB 同步是否已配置（DATABASE_URL + 有效 SSH 主机）。"""
    if not _env("DATABASE_URL"):
        return False
    explicit = _env("DB_SYNC_ENABLED")
    if explicit:
        if explicit.lower() in ("0", "false", "no"):
            return False
        if explicit.lower() not in ("1", "true", "yes"):
            return False
    host = _env("DB_SYNC_SSH_HOST") or _env("BLOG_SSH_HOST")
    return is_meaningful_host(host)


def _db_sync_enabled() -> bool:
    return db_sync_configured()


DB_SYNC_ENABLED = _db_sync_enabled()
DB_SYNC_SSH_HOST = _env("DB_SYNC_SSH_HOST") or _env("BLOG_SSH_HOST")
DB_SYNC_SSH_USER = _env("DB_SYNC_SSH_USER") or _env("BLOG_SSH_USER", "root")
DB_SYNC_SSH_PORT = int(_env("DB_SYNC_SSH_PORT") or _env("BLOG_SSH_PORT") or "22")
DB_SYNC_SSH_PASSWORD = _env("DB_SYNC_SSH_PASSWORD") or _env("BLOG_SSH_PASSWORD")
DB_SYNC_SSH_IDENTITY = _env("DB_SYNC_SSH_IDENTITY") or _env("BLOG_SSH_IDENTITY")
DB_SYNC_REMOTE_DIR = _env("DB_SYNC_REMOTE_DIR", "/opt/wxspirder")
DB_SYNC_REMOTE_CONTAINER = _env("DB_SYNC_REMOTE_CONTAINER", "wxspirder-pg")
DB_SYNC_REMOTE_USER = _env("DB_SYNC_REMOTE_USER", "wx")
DB_SYNC_REMOTE_PASSWORD = _env("DB_SYNC_REMOTE_PASSWORD", "wx")
DB_SYNC_REMOTE_DB = _env("DB_SYNC_REMOTE_DB", "wxspirder")
DB_SYNC_MODE = _env("DB_SYNC_MODE", "auto")  # auto | full | incremental
