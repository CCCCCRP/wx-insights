from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass, field
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from worker.config import (
    NOTIFY_EMAILS,
    SMTP_FROM,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USE_SSL,
    SMTP_USER,
)
from worker.mail.recipients import resolve_recipients

logger = logging.getLogger(__name__)


class MailerConfigError(Exception):
    pass


@dataclass
class Mailer:
    """SMTP 邮件发送器，与业务逻辑解耦。"""

    host: str = SMTP_HOST
    port: int = SMTP_PORT
    user: str = SMTP_USER
    password: str = SMTP_PASSWORD
    sender: str = SMTP_FROM or SMTP_USER
    use_ssl: bool = SMTP_USE_SSL
    default_recipients: list[str] = field(default_factory=lambda: list(NOTIFY_EMAILS))

    def validate(self) -> None:
        missing = [k for k, v in {
            "SMTP_HOST": self.host,
            "SMTP_USER": self.user,
            "SMTP_PASSWORD": self.password,
        }.items() if not v]
        if missing:
            raise MailerConfigError(f"请在 worker/.env 中配置: {', '.join(missing)}")

    def _connect(self) -> smtplib.SMTP:
        self.validate()
        context = ssl.create_default_context()
        if self.use_ssl:
            server = smtplib.SMTP_SSL(self.host, self.port, context=context, timeout=30)
        else:
            server = smtplib.SMTP(self.host, self.port, timeout=30)
            server.starttls(context=context)
        server.ehlo()
        server.login(self.user, self.password)
        return server

    def test_connection(self) -> dict:
        server = self._connect()
        try:
            server.noop()
        finally:
            server.quit()
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "ssl": self.use_ssl,
            "password_len": len(self.password),
            "default_recipients": self.default_recipients,
        }

    def send(
        self,
        subject: str,
        body: str,
        *,
        to: Optional[list[str]] = None,
        html: bool = False,
    ) -> None:
        recipients = resolve_recipients(to, self.default_recipients)
        if not recipients:
            logger.warning("send: 无有效收件人，跳过")
            return
        subtype = "html" if html else "plain"
        msg = MIMEText(body, subtype, "utf-8")
        msg["Subject"] = subject
        msg["From"] = self.sender
        msg["To"] = ", ".join(recipients)
        server = self._connect()
        try:
            server.sendmail(self.sender, recipients, msg.as_string())
        finally:
            server.quit()
        logger.info("邮件已发送 [%s] -> %s", subject, recipients)

    def send_qrcode(
        self,
        qrcode_bytes: bytes,
        ext: str = "png",
        *,
        to: Optional[list[str]] = None,
        subject: Optional[str] = None,
        refreshed: bool = False,
        login_url: Optional[str] = None,
    ) -> None:
        recipients = resolve_recipients(to, self.default_recipients)
        if not recipients:
            logger.warning("send_qrcode: 无有效收件人，跳过")
            return
        tag = "（新二维码）" if refreshed else ""
        subject = subject or f"[wxspirder] 微信公众号登录二维码{tag}"

        if login_url:
            link_block = f"""
      <p style="font-size:16px;"><a href="{login_url}">点击打开登录页（电脑浏览器）</a></p><hr>"""
        else:
            link_block = """
      <p><b>请在电脑邮箱客户端打开本邮件</b>，用微信扫一扫对准 <b>电脑屏幕</b> 上的二维码。</p>
      <p style="color:#888;">请勿只在手机上看邮件——同一部手机无法用摄像头扫屏幕里的码。</p><hr>"""

        html_body = f"""<html><body style="font-family:sans-serif;">
      <h2>微信公众号扫码登录</h2>{link_block}
      <p>二维码约 2 分钟有效，过期后会自动重发（9:00～10:00）。</p>
      <p><img src="cid:qrcode"></p>
      <hr><p style="color:#666;font-size:12px;">wxspirder worker</p>
      </body></html>"""

        msg = MIMEMultipart("related")
        msg["Subject"] = subject
        msg["From"] = self.sender
        msg["To"] = ", ".join(recipients)
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        img = MIMEImage(qrcode_bytes, _subtype=ext)
        img.add_header("Content-ID", "<qrcode>")
        img.add_header("Content-Disposition", "inline", filename=f"login.{ext}")
        msg.attach(img)

        server = self._connect()
        try:
            server.sendmail(self.sender, recipients, msg.as_string())
        finally:
            server.quit()
        logger.info("二维码邮件已发送 -> %s", recipients)

    def send_login_result(
        self,
        success: bool,
        detail: str,
        *,
        to: Optional[list[str]] = None,
    ) -> None:
        try:
            self.validate()
        except MailerConfigError:
            logger.warning("SMTP 未配置，跳过结果通知")
            return
        subject = "[wxspirder] 登录成功" if success else "[wxspirder] 登录失败"
        self.send(subject, detail, to=to)

    def send_insight_report(
        self,
        week_id: str,
        body_md: str,
        *,
        to: Optional[list[str]] = None,
        meta: Optional[dict] = None,
    ) -> None:
        """发送 Weekly Insights 报告（Markdown 纯文本）。"""
        try:
            self.validate()
        except MailerConfigError:
            logger.warning("SMTP 未配置，跳过洞见报告邮件")
            return

        recipients = resolve_recipients(to, self.default_recipients)
        if not recipients:
            logger.warning("NOTIFY_EMAILS 未配置，跳过洞见报告邮件")
            return

        subject = f"[wxspirder] Weekly Insights · {week_id}"
        footer_lines = [f"\n\n---\nwxspirder · {week_id}"]
        if meta:
            primary = meta.get("primary_count")
            themes = meta.get("theme_count") or len(meta.get("themes") or [])
            if primary is not None:
                footer_lines.append(f"Primary: {primary} 篇")
            if themes:
                footer_lines.append(f"主题: {themes} 个")
        body = body_md.rstrip() + "\n".join(footer_lines) + "\n"

        self.send(subject, body, to=recipients)
        logger.info("洞见报告邮件已发送 · %s -> %s", week_id, recipients)

    def send_insight_report_html(
        self,
        week_id: str,
        html_body: str,
        *,
        to: Optional[list[str]] = None,
        meta: Optional[dict] = None,
    ) -> None:
        """发送 Weekly Insights 报告（HTML 富文本）。"""
        try:
            self.validate()
        except MailerConfigError:
            logger.warning("SMTP 未配置，跳过洞见报告邮件")
            return

        recipients = resolve_recipients(to, self.default_recipients)
        if not recipients:
            logger.warning("NOTIFY_EMAILS 未配置，跳过洞见报告邮件")
            return

        subject = f"[wxspirder] Weekly Insights · {week_id}"
        self.send(subject, html_body, to=recipients, html=True)
        logger.info("HTML 洞见报告邮件已发送 · %s -> %s", week_id, recipients)

    def send_schedule_reminder(
        self,
        next_monday: str,
        *,
        to: Optional[list[str]] = None,
    ) -> None:
        """发送周四/五提前提醒邮件：告知周一将发扫码通知。"""
        try:
            self.validate()
        except MailerConfigError:
            logger.warning("SMTP 未配置，跳过调度提醒邮件")
            return

        recipients = resolve_recipients(to, self.default_recipients)
        if not recipients:
            logger.warning("NOTIFY_EMAILS 未配置，跳过调度提醒邮件")
            return

        subject = f"[wxspirder] 提醒：{next_monday} 周一将启动数据采集"
        body = f"""<html><body style="font-family:sans-serif;max-width:600px;margin:auto;">
<h2 style="color:#333;">wxspirder 每周提醒</h2>
<p>本邮件为自动提醒。</p>
<p><b>{next_monday}（周一）早上 09:00</b> 将向您发送微信公众号扫码登录邮件，请注意查收并及时扫码。</p>
<ul>
  <li>收到二维码邮件后，请在<b>电脑</b>上打开邮件，用微信扫码。</li>
  <li>二维码 2 分钟有效，过期将自动重发。</li>
  <li>扫码完成后，系统将自动完成本周数据采集并发送洞见报告。</li>
</ul>
<p style="color:#888;font-size:12px;">wxspirder · 全自动周报系统</p>
</body></html>"""

        self.send(subject, body, to=recipients, html=True)
        logger.info("调度提醒邮件已发送 (%s) -> %s", next_monday, recipients)


mailer = Mailer()
