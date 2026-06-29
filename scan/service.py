from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

from worker.auth.token_manager import TokenManager, token_manager
from worker.config import (
    DATA_DIR,
    LOGIN_GRACE_SECONDS,
    LOGIN_POLL_INTERVAL,
    LOGIN_REMINDER_TIME,
    LOGIN_TIMEOUT,
    LOGIN_WINDOW_END,
    LOGIN_WINDOW_START,
    NOTIFY_EMAILS,
    QR_RESEND_INTERVAL,
)
from worker.mail.mailer import Mailer, MailerConfigError, mailer
from worker.scan.client import ScanLoginClient
from worker.scan.server import LoginPageServer
from worker.scan.window import LoginWindow

logger = logging.getLogger(__name__)


class ScanLoginService:
    """扫码登录编排：时间窗口 + 邮件通知 + token 持久化。"""

    def __init__(
        self,
        *,
        tokens: TokenManager | None = None,
        mail: Mailer | None = None,
        qrcode_path: Path | None = None,
    ) -> None:
        self.tokens = tokens or token_manager
        self.mail = mail or mailer
        self.qrcode_path = qrcode_path or (DATA_DIR / "last_qrcode.png")
        self.page_server = LoginPageServer()

    def run_with_email(self, to_email: list[str] | None = None, timeout: int | None = None) -> int:
        recipients = to_email if to_email is not None else NOTIFY_EMAILS
        self.tokens.mark_pending()

        window = (
            LoginWindow(
                LOGIN_WINDOW_START,
                LOGIN_WINDOW_END,
                LOGIN_GRACE_SECONDS,
                QR_RESEND_INTERVAL,
                LOGIN_REMINDER_TIME,
            )
            if timeout is None
            else None
        )

        if window:
            if window.past_deadline():
                logger.warning("已过等待截止时间（%s）", window.deadline.strftime("%H:%M"))
                self.tokens.mark_failed("past deadline")
                return 1
            window.wait_until_start()
            logger.info("%s", window.describe())

        legacy_deadline = time.time() + (timeout or LOGIN_TIMEOUT)

        def done() -> bool:
            return window.past_deadline() if window else time.time() >= legacy_deadline

        try:
            with ScanLoginClient() as client:
                return self._poll_loop(client, recipients, window, done)
        finally:
            self.page_server.stop()

    def _poll_loop(
        self,
        client: ScanLoginClient,
        recipient: list[str],
        window: LoginWindow | None,
        done,
    ) -> int:
        last_sent = 0.0
        resend_count = 0
        sent_reminder = False
        window_closed_msg = False
        login_url = LoginPageServer.public_login_url()

        if not window or window.in_send_window():
            qr, ext = self._refresh_qr(client)
            self.page_server.start(qr, ext)
            if self._send_qr(qr, ext, recipient, login_url=login_url):
                last_sent = time.time()
                logger.info("已发送登录邮件 -> %s", recipient)
            logger.info("本地二维码: %s", self.qrcode_path)

        while not done():
            if window and not window.in_send_window() and not window_closed_msg:
                logger.info(
                    "%s 发信窗口结束，不再发邮件；等待扫码至 %s...",
                    LOGIN_WINDOW_END,
                    window.deadline.strftime("%H:%M"),
                )
                window_closed_msg = True

            data = client.check_scan_status()
            if data.get("base_resp", {}).get("ret", -1) != 0:
                time.sleep(LOGIN_POLL_INTERVAL)
                continue

            status = data.get("status", 0)
            logger.info("扫码状态 status=%s", status)

            if status == 1:
                return self._on_success(client, recipient)

            in_send = (not window) or window.in_send_window()

            if window and window.should_send_reminder(sent_reminder):
                qr, ext = self._refresh_qr(client)
                if self._send_qr(
                    qr, ext, recipient, refreshed=True,
                    subject="[wxspirder] 登录提醒（9:30）",
                    login_url=login_url,
                ):
                    last_sent = time.time()
                    sent_reminder = True
                    resend_count += 1
                    logger.info("已发送 9:30 提醒邮件")

            if status == 2 and in_send and window and window.can_resend(last_sent):
                qr, ext = self._refresh_qr(client)
                if self._send_qr(qr, ext, recipient, refreshed=True, login_url=login_url):
                    last_sent = time.time()
                    resend_count += 1
                    logger.info("二维码过期，已重发（第 %d 次）", resend_count)
            elif status == 2 and not window and time.time() - last_sent >= QR_RESEND_INTERVAL:
                qr, ext = self._refresh_qr(client)
                if self._send_qr(qr, ext, recipient, refreshed=True, login_url=login_url):
                    last_sent = time.time()
                    resend_count += 1

            elif status in (4, 6):
                acct = data.get("acct_size", 0)
                logger.info(
                    "已扫码，请在手机上选择账号..." if acct > 1 else "已扫码，请确认登录..."
                )
            elif status == 3:
                logger.warning("扫码失败，等待重试...")

            time.sleep(LOGIN_POLL_INTERVAL)

        return self._on_timeout(recipient, window)

    def _refresh_qr(self, client: ScanLoginClient) -> tuple[bytes, str]:
        client.start_session()
        qr, ext = client.fetch_qrcode()
        self.qrcode_path.write_bytes(qr)
        self.page_server.update_qrcode(qr, ext)
        return qr, ext

    def _send_qr(
        self,
        qr: bytes,
        ext: str,
        recipient: list[str],
        *,
        refreshed: bool = False,
        subject: str | None = None,
        login_url: str | None = None,
    ) -> bool:
        try:
            self.mail.send_qrcode(
                qr, ext, to=recipient,
                refreshed=refreshed, subject=subject, login_url=login_url,
            )
            return True
        except MailerConfigError as e:
            logger.warning("邮件未发送: %s", e)
            return False
        except Exception as e:
            logger.exception("发邮件失败")
            return False

    def _on_success(self, client: ScanLoginClient, recipient: list[str]) -> int:
        logger.info("扫码成功，正在保存 token...")
        creds = client.complete_login()
        if not self.tokens.save_credentials(creds):
            self.tokens.mark_failed("save_credentials failed")
            self.mail.send_login_result(False, "凭证保存失败", to=recipient)
            return 1
        self.tokens.mark_success(creds)
        msg = (
            f"登录成功\n"
            f"账号: {creds.get('nickname')}\n"
            f"fakeid: {creds.get('fakeid')}\n"
            f"token_refreshed_at: {self.tokens.token_refreshed_at()}\n"
            f"过期时间(ms): {creds['expire_time']}"
        )
        logger.info("%s", msg)
        try:
            self.mail.send_login_result(True, msg, to=recipient)
        except Exception:
            pass
        return 0

    def _on_timeout(self, recipient: list[str], window: LoginWindow | None) -> int:
        fail_msg = (
            f"本周登录未完成（窗口 {LOGIN_WINDOW_START}～{LOGIN_WINDOW_END}，"
            f"截止 {window.deadline.strftime('%H:%M') if window else 'N/A'}）。"
            f"请重新运行: python -m worker login --email"
        )
        self.tokens.mark_failed("timeout")
        try:
            self.mail.send_login_result(False, fail_msg, to=recipient)
        except Exception:
            pass
        logger.error("%s", fail_msg)
        return 1
