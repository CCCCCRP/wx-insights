from __future__ import annotations

import argparse
import logging

from worker.auth.token_manager import token_manager
from worker.config import NOTIFY_EMAILS, parse_email_list
from worker.mail.mailer import MailerConfigError, mailer
from worker.crawl.account_service import AccountService
from worker.crawl.service import CrawlService
from worker.insight.publish_blog import blog_publish_skip_reason, publish_week
from worker.insight.service import InsightService
from worker.scan.service import ScanLoginService
from worker.scheduler import run_schedule_loop

from worker.log_setup import setup_logging

logger = logging.getLogger(__name__)


def cmd_clear_token() -> int:
    token_manager.clear()
    logger.info("已清空 token/cookie")
    return 0


def cmd_status() -> int:
    from worker.log_setup import log_paths

    logger.info("%s", token_manager.status_report())
    logger.info("=== logging ===")
    for k, v in log_paths().items():
        logger.info("  %s: %s", k, v)
    return 0


def _parse_email_list_arg(value: str | None) -> list[str] | None:
    if not value:
        return None
    return parse_email_list(value)


def cmd_test_email() -> int:
    from worker.config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD

    logger.info("=== SMTP ===")
    logger.info("  host: %s:%s", SMTP_HOST, SMTP_PORT)
    logger.info("  user: %s", SMTP_USER)
    logger.info("  password_len: %d", len(SMTP_PASSWORD))
    logger.info("  notify: %s", NOTIFY_EMAILS)
    try:
        logger.info("SMTP 登录成功: %s", mailer.test_connection())
        return 0
    except Exception as e:
        logger.error("SMTP 登录失败: %s", e)
        return 1


def cmd_login_email(args: argparse.Namespace) -> int:
    service = ScanLoginService()
    return service.run_with_email(
        to_email=args.recipients,
        timeout=args.timeout,
    )


def cmd_crawl(args: argparse.Namespace) -> int:
    service = CrawlService()
    return service.run(
        week=args.week,
        wait_for_login=not args.no_wait,
        wait_timeout=args.wait_timeout,
        fetch_content=not args.no_content,
        force_content=args.force_content,
    )


def cmd_accounts(args: argparse.Namespace) -> int:
    service = AccountService()
    try:
        if args.accounts_cmd == "resolve":
            return service.resolve(dry_run=args.dry_run)
        if args.accounts_cmd == "add":
            return service.add(args.nickname, dry_run=args.dry_run)
        if args.accounts_cmd == "search":
            return service.search(args.query)
    except (RuntimeError, ValueError) as e:
        logger.error("%s", e)
        return 1
    return 1


def cmd_insight(args: argparse.Namespace) -> int:
    service = InsightService()
    cmd = getattr(args, "insight_cmd", None)

    if cmd == "embed":
        return service.run_embed()
    if cmd == "sync-profiles":
        return service.run_profile_cmd(sync_only=True)
    if cmd == "publish-blog":
        settings = InsightService().settings
        reason = blog_publish_skip_reason(settings)
        if reason and not getattr(args, "dry_run", False):
            logger.error("博客发布未配置：%s", reason)
            return 1
        try:
            url = publish_week(
                week=getattr(args, "week", "last") or "last",
                dry_run=getattr(args, "dry_run", False),
            )
        except (RuntimeError, FileNotFoundError) as e:
            logger.error("%s", e)
            return 1
        logger.info("博客链接: %s", url)
        return 0
    if cmd == "profile":
        return service.run_profile_cmd(
            nickname=getattr(args, "nickname", None),
            dry_run=getattr(args, "dry_run", False),
            sync_only=getattr(args, "sync_only", False),
        )
    # 默认：生成洞见报告
    return service.run(
        week=getattr(args, "week", "last") or "last",
        dry_run=getattr(args, "dry_run", False),
        skip_embed=getattr(args, "skip_embed", False),
        skip_profile=getattr(args, "skip_profile", False),
        send_email=False if getattr(args, "no_email", False) else None,
        email_to=_parse_email_list_arg(getattr(args, "email_to", None)),
        publish_blog=False if getattr(args, "no_publish_blog", False) else None,
    )


def cmd_schedule(args: argparse.Namespace) -> int:
    run_schedule_loop(
        skip_login=args.skip_login,
        dry_run=args.dry_run,
        now=args.now,
        once=args.once,
    )
    return 0


def cmd_db(args: argparse.Namespace) -> int:
    if args.db_cmd == "sync":
        from worker.config import db_sync_configured
        from worker.db.sync_remote import sync_database_to_remote

        if getattr(args, "mark_baseline", False) or getattr(args, "dry_run", False):
            pass
        elif not db_sync_configured():
            logger.info("远程 DB 同步未配置（需 DATABASE_URL + BLOG_SSH_HOST），已跳过")
            return 0
        try:
            mode = None
            if getattr(args, "full", False):
                mode = "full"
            elif getattr(args, "incremental", False):
                mode = "incremental"
            sync_database_to_remote(
                dry_run=getattr(args, "dry_run", False),
                mode=mode,
                mark_baseline=getattr(args, "mark_baseline", False),
            )
        except Exception as e:
            logger.error("%s", e)
            return 1
        return 0
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="wxspirder worker — 扫码登录 / 按周采集 / 邮件",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("clear-token", help="清空微信 token/cookie")
    sub.add_parser("status", help="查看 token 状态")
    sub.add_parser("test-email", help="测试 SMTP 连接")

    p_login = sub.add_parser("login", help="扫码登录")
    p_login.add_argument("--email", action="store_true", help="邮件 + 时间窗口扫码")
    p_login.add_argument("--to", dest="email_addr", default=None, help="收件邮箱，逗号分隔多个")
    p_login.add_argument("--timeout", type=int, default=None, help="调试用固定超时（秒）")

    p_crawl = sub.add_parser("crawl", help="按周采集公众号文章")
    p_crawl.add_argument("--week", default="last", help="采集周期，默认 last=上一自然周")
    p_crawl.add_argument("--no-wait", action="store_true", help="无 token 时不等待，直接失败")
    p_crawl.add_argument("--wait-timeout", type=int, default=3600, help="等待 token 最长时间（秒）")
    p_crawl.add_argument("--no-content", action="store_true", help="只拉列表/摘要，不抓 /s/ 正文")
    p_crawl.add_argument("--force-content", action="store_true", help="忽略本地 txt，强制重新抓正文")

    p_accounts = sub.add_parser("accounts", help="公众号配置（nickname → fakeid）")
    acc_sub = p_accounts.add_subparsers(dest="accounts_cmd", required=True)

    p_resolve = acc_sub.add_parser("resolve", help="为缺 fakeid 的条目自动搜索并写回 yaml")
    p_resolve.add_argument("--dry-run", action="store_true", help="只打印，不写文件")

    p_add = acc_sub.add_parser("add", help="添加公众号（仅 nickname）并回填 fakeid")
    p_add.add_argument("nickname", help="公众号名称")
    p_add.add_argument("--dry-run", action="store_true", help="只打印，不写文件")

    p_search = acc_sub.add_parser("search", help="搜索公众号（预览，不写入 yaml）")
    p_search.add_argument("query", help="搜索关键词")

    p_sched = sub.add_parser("schedule", help="每周全自动调度（永久循环）")
    p_sched.add_argument("--dry-run", action="store_true", help="只打印计划，不执行实际操作")
    p_sched.add_argument("--skip-login", action="store_true", help="跳过扫码登录（token 已有效时使用）")
    p_sched.add_argument("--now", action="store_true", help="立刻执行一次流水线")
    p_sched.add_argument("--once", action="store_true", help="跑完一次后退出，不进入永久休眠")

    p_insight = sub.add_parser("insight", help="Weekly Insights 洞见报告")
    p_insight.add_argument("--week", default="last", help="week_id 或 last（默认 last）")
    p_insight.add_argument("--dry-run", action="store_true", help="只打印选取统计")
    p_insight.add_argument("--skip-embed", action="store_true", help="跳过 embedding")
    p_insight.add_argument("--skip-profile", action="store_true", help="跳过账号画像")
    p_insight.add_argument("--no-email", action="store_true", help="不发送洞见报告邮件")
    p_insight.add_argument("--no-publish-blog", action="store_true", help="不上传 HTML 到博客")
    p_insight.add_argument("--email-to", default=None, help="报告收件邮箱，逗号分隔（默认 NOTIFY_EMAILS）")
    ins_sub = p_insight.add_subparsers(dest="insight_cmd")

    ins_sub.add_parser("embed", help="补全缺失 embedding")
    p_ins_profile = ins_sub.add_parser("profile", help="账号自动画像")
    p_ins_profile.add_argument("--nickname", default=None, help="指定公众号")
    p_ins_profile.add_argument("--dry-run", action="store_true")
    ins_sub.add_parser("sync-profiles", help="仅将 accounts.yaml 画像同步到 DB")
    p_pub = ins_sub.add_parser("publish-blog", help="将周报 HTML 归档发布到个人博客")
    p_pub.add_argument("--week", default="last", help="week_id 或 last（默认 last）")
    p_pub.add_argument("--dry-run", action="store_true", help="只打印，不上传")

    p_db = sub.add_parser("db", help="数据库工具")
    db_sub = p_db.add_subparsers(dest="db_cmd", required=True)
    p_db_sync = db_sub.add_parser("sync", help="将本地 PostgreSQL 同步到阿里云 wxspirder 库")
    p_db_sync.add_argument("--dry-run", action="store_true", help="只打印计划，不执行 dump/restore")
    p_db_sync.add_argument("--full", action="store_true", help="强制全量同步")
    p_db_sync.add_argument("--incremental", action="store_true", help="强制增量同步")
    p_db_sync.add_argument(
        "--mark-baseline",
        action="store_true",
        help="标记当前为增量基线（已全量同步后使用，不上传）",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    log_path = setup_logging()
    if log_path:
        logging.getLogger(__name__).info("日志文件: %s", log_path)
    args = build_parser().parse_args(argv)

    if args.command == "clear-token":
        return cmd_clear_token()
    if args.command == "status":
        return cmd_status()
    if args.command == "test-email":
        return cmd_test_email()
    if args.command == "login":
        if not args.email:
            logger.error("请使用: python -m worker login --email")
            return 1
        return cmd_login_email(argparse.Namespace(
            recipients=_parse_email_list_arg(args.email_addr),
            timeout=args.timeout,
        ))
    if args.command == "schedule":
        return cmd_schedule(args)
    if args.command == "crawl":
        return cmd_crawl(args)
    if args.command == "accounts":
        return cmd_accounts(args)
    if args.command == "insight":
        return cmd_insight(args)
    if args.command == "db":
        return cmd_db(args)
    return 1
