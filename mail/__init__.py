"""邮件通知（SMTP）。"""

from worker.mail.mailer import Mailer, MailerConfigError, mailer

__all__ = ["Mailer", "MailerConfigError", "mailer"]
