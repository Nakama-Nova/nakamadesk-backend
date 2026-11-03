"""
Sending email through Amazon SES with the server's own AWS role (no password stored).
boto3 is faked; what is checked is exactly what the server would ask SES to send.
"""

import pytest

from app.core.config import settings
from app.services import email_service


class FakeSes:
    def __init__(self):
        self.calls = []

    def send_email(self, **kwargs):
        self.calls.append(kwargs)


@pytest.fixture
def ses(monkeypatch):
    fake, made = FakeSes(), []

    def client(service, **kwargs):
        made.append((service, kwargs.get("region_name")))
        return fake

    monkeypatch.setattr(email_service.boto3, "client", client)
    monkeypatch.setattr(settings, "EMAIL_PROVIDER", "ses")
    monkeypatch.setattr(settings, "SES_REGION", "ap-southeast-2")
    monkeypatch.setattr(
        settings, "EMAIL_FROM", "Vriksha Studios <hello@vrikshastudios.com>"
    )
    monkeypatch.setattr(settings, "EMAIL_REPLY_TO", "")
    fake.made = made
    return fake


def test_ses_gets_the_message_exactly_as_the_app_wrote_it(ses):
    email_service.send(
        "shopper@example.com", "Your code: 123456", "Your code is 123456."
    )
    (call,) = ses.calls
    assert call["FromEmailAddress"] == "Vriksha Studios <hello@vrikshastudios.com>"
    assert call["Destination"] == {"ToAddresses": ["shopper@example.com"]}
    simple = call["Content"]["Simple"]
    assert simple["Subject"]["Data"] == "Your code: 123456"
    assert simple["Body"]["Text"]["Data"] == "Your code is 123456."
    assert simple["Subject"]["Charset"] == simple["Body"]["Text"]["Charset"] == "UTF-8"
    assert "ReplyToAddresses" not in call


def test_the_reply_to_address_is_passed_when_one_is_set(ses, monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_REPLY_TO", "info@vrikshastudios.com")
    email_service.send("shopper@example.com", "Hi", "Hello")
    assert ses.calls[0]["ReplyToAddresses"] == ["info@vrikshastudios.com"]


def test_the_ses_client_is_made_in_the_configured_region(ses, monkeypatch):
    monkeypatch.setattr(settings, "SES_REGION", "us-east-1")
    email_service.send("shopper@example.com", "Hi", "Hello")
    assert ses.made == [("sesv2", "us-east-1")]


def test_a_non_ascii_name_and_message_are_sent_untouched(ses):
    email_service.send("shopper@example.com", "ನಿಮ್ಮ ಕೋಡ್", "ನಮಸ್ಕಾರ")
    simple = ses.calls[0]["Content"]["Simple"]
    assert simple["Subject"]["Data"] == "ನಿಮ್ಮ ಕೋಡ್"
    assert simple["Body"]["Text"]["Data"] == "ನಮಸ್ಕಾರ"


def test_with_the_placeholder_sender_production_raises_and_nothing_is_sent(
    ses, monkeypatch
):
    monkeypatch.setattr(settings, "EMAIL_FROM", "Vriksha Studios <no-reply@localhost>")
    monkeypatch.setattr(settings, "IS_PROD", True)
    with pytest.raises(email_service.EmailNotConfigured):
        email_service.send("shopper@example.com", "Hi", "Hello")
    assert ses.calls == []


def test_with_the_placeholder_sender_development_only_logs(ses, monkeypatch, caplog):
    monkeypatch.setattr(settings, "EMAIL_FROM", "Vriksha Studios <no-reply@localhost>")
    monkeypatch.setattr(settings, "IS_PROD", False)
    with caplog.at_level("WARNING"):
        email_service.send("shopper@example.com", "Code 654321", "Your code is 654321.")
    assert ses.calls == [] and "654321" in caplog.text


def test_a_ses_refusal_is_raised_so_the_callers_can_handle_it(ses, monkeypatch):
    def refuse(**kwargs):
        raise RuntimeError("MessageRejected: Email address is not verified")

    monkeypatch.setattr(ses, "send_email", refuse)
    with pytest.raises(RuntimeError):
        email_service.send("shopper@example.com", "Hi", "Hello")


def test_without_the_ses_provider_smtp_is_still_what_runs(monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_PROVIDER", "")
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    monkeypatch.setattr(settings, "IS_PROD", True)
    with pytest.raises(email_service.EmailNotConfigured):
        email_service.send("shopper@example.com", "Hi", "Hello")
