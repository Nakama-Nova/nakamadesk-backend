"""
Sends a text message through Amazon SNS (SMS_PROVIDER=sns), using the AWS role the
server already runs under, so there are no keys to store. Messages go out as
Transactional, which is what carriers expect for one-time codes.

Not configured: in development the message is written to the log so a code can be read
from the console; in production it raises, so a missing setting is noticed instead of
silently dropping every code.

India needs a registered sender header and an approved message template (TRAI DLT).
SNS takes them as message attributes; set SMS_SENDER_ID, SMS_DLT_ENTITY_ID and
SMS_DLT_TEMPLATE_ID, and keep SMS_OTP_TEMPLATE word for word the same as the template.

ponytail: sent inside the request with a short timeout. Move to the worker process once
there is one, together with the email sending.
"""

import logging
import re

import boto3
from botocore.config import Config

from app.core.config import settings

logger = logging.getLogger(__name__)


class SmsNotConfigured(RuntimeError):
    """SMS settings are missing in production."""


def normalize_phone(raw: str) -> str:
    """
    A mobile number in international form (+919876543210), or ValueError. Accepts what a
    shopper types: 98765 43210, 919876543210 or +91 98765-43210. Only numbers starting
    with one of SMS_ALLOWED_PREFIXES are accepted.
    """
    digits = re.sub(r"[ \-()]", "", raw or "")
    if re.fullmatch(r"[6-9]\d{9}", digits):  # an Indian mobile without the country code
        digits = "+91" + digits
    elif re.fullmatch(r"91[6-9]\d{9}", digits):
        digits = "+" + digits
    if not re.fullmatch(r"\+\d{8,15}", digits):
        raise ValueError("not a phone number")
    if digits.startswith("+91") and not re.fullmatch(r"\+91[6-9]\d{9}", digits):
        raise ValueError("not an Indian mobile number")
    if not digits.startswith(tuple(settings.SMS_ALLOWED_PREFIXES)):
        raise ValueError("country not supported")
    return digits


def send(to: str, message: str) -> None:
    """Send `message` to `to` (international form). Raises SmsNotConfigured or a boto error."""
    if settings.SMS_PROVIDER != "sns":
        if settings.IS_PROD:
            raise SmsNotConfigured("SMS_PROVIDER is not set")
        logger.warning("SMS not sent (SMS not set up)\nto: %s\n%s", to, message)
        return

    attributes = {
        "AWS.SNS.SMS.SMSType": {"DataType": "String", "StringValue": "Transactional"}
    }
    optional = {
        "AWS.SNS.SMS.SenderID": settings.SMS_SENDER_ID,
        "AWS.MM.SMS.EntityId": settings.SMS_DLT_ENTITY_ID,
        "AWS.MM.SMS.TemplateId": settings.SMS_DLT_TEMPLATE_ID,
    }
    for name, value in optional.items():
        if value:
            attributes[name] = {"DataType": "String", "StringValue": value}

    client = boto3.client(
        "sns",
        region_name=settings.SMS_AWS_REGION,
        config=Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2}),
    )
    client.publish(PhoneNumber=to, Message=message, MessageAttributes=attributes)
