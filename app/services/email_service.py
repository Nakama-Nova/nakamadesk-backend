"""
Sends email through any SMTP server (Amazon SES, Brevo, Resend and others all offer
one). Set SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD and EMAIL_FROM.

Not configured: in development the message is written to the log instead of sent, so
a sign-in code can be read from the console; in production it raises, so a missing
setting is noticed rather than silently dropping every code.

ponytail: sent inside the request, with a 10 second timeout. Move to the worker
process once there is one, so a slow mail server never slows a page.
"""

import logging
import smtplib
from email.message import EmailMessage

from app.core.config import settings

logger = logging.getLogger(__name__)


class EmailNotConfigured(RuntimeError):
    """SMTP settings are missing in production."""


def send(to: str, subject: str, body: str) -> None:
    if not settings.SMTP_HOST:
        if settings.IS_PROD:
            raise EmailNotConfigured("SMTP_HOST is not set")
        logger.warning(
            "EMAIL not sent (SMTP not set up)\nto: %s\n%s\n\n%s", to, subject, body
        )
        return
    msg = EmailMessage()
    msg["From"] = settings.EMAIL_FROM
    msg["To"] = to
    if settings.EMAIL_REPLY_TO:
        msg["Reply-To"] = settings.EMAIL_REPLY_TO
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as smtp:
        smtp.starttls()
        if settings.SMTP_USER:
            smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        smtp.send_message(msg)
