"""Newsletter double opt-in: sign-up, confirm, unsubscribe. Brevo is faked, no network."""

import re
import uuid
from datetime import timedelta

import httpx
import pytest

from app.core import ratelimit
from app.core.config import settings
from app.models.newsletter_subscriber import NewsletterSubscriber
from app.services import newsletter_service as svc

STOREFRONT = "https://shop.example"


class FakeBrevo:
    """Stands in for httpx.request. Records calls; `fail` makes Brevo answer an error."""

    def __init__(self):
        self.calls = []
        self.status = None  # force this HTTP status on the next calls
        self.body = {}
        self.network_error = False

    def __call__(self, method, url, json=None, headers=None, timeout=None, **kw):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            }
        )
        if self.network_error:
            raise httpx.ConnectTimeout("boom")
        status = self.status or 201
        return httpx.Response(
            status, json=self.body, request=httpx.Request(method, url)
        )

    def to(self, suffix):
        return [c for c in self.calls if c["url"].endswith(suffix)]


@pytest.fixture(autouse=True)
def brevo(monkeypatch):
    ratelimit.reset()
    monkeypatch.setattr(settings, "PUBLIC_RATE_LIMIT", 1000)
    monkeypatch.setattr(settings, "BREVO_API_KEY", "test-key")
    monkeypatch.setattr(settings, "BREVO_LIST_ID", 7)
    monkeypatch.setattr(settings, "BREVO_SENDER_EMAIL", "news@vrikshastudios.com")
    monkeypatch.setattr(settings, "BREVO_SENDER_NAME", "Vriksha Studios")
    monkeypatch.setattr(settings, "STOREFRONT_URL", STOREFRONT)
    fake = FakeBrevo()
    monkeypatch.setattr(httpx, "request", fake)
    yield fake
    ratelimit.reset()


@pytest.fixture(autouse=True)
def clean(db):
    db.query(NewsletterSubscriber).delete()
    db.commit()


def subscribe(client, email="Meena@Example.com", **over):
    return client.post("/public/newsletter/subscribe", json={"email": email, **over})


def tokens(brevo):
    """(confirm_token, unsubscribe_token) from the last email Brevo was asked to send."""
    sent = brevo.to("/smtp/email")[-1]["json"]
    confirm = re.search(r"/newsletter/confirm\?token=([^\s\"]+)", sent["textContent"])
    leave = re.search(r"/newsletter/unsubscribe\?token=([^\s\"]+)", sent["textContent"])
    return confirm.group(1), leave.group(1)


def row(db, email="meena@example.com"):
    db.expire_all()
    return db.query(NewsletterSubscriber).filter_by(email=email).one()


def confirmed(client, brevo, email="meena@example.com"):
    subscribe(client, email)
    c, u = tokens(brevo)
    assert (
        client.post("/public/newsletter/confirm", json={"token": c}).status_code == 200
    )
    return c, u


def test_subscribe_sends_a_confirmation_and_stores_a_pending_row(client, db, brevo):
    r = subscribe(client, source="footer")
    assert r.status_code == 202
    assert r.json() == {"status": "check_your_email"}

    sub = row(db)
    assert sub.status == "pending"
    assert sub.source == "footer"
    assert sub.consent_version == svc.CONSENT_VERSION
    assert sub.subscribed_at and sub.last_confirmation_sent_at
    assert sub.confirmed_at is None

    (call,) = brevo.to("/smtp/email")
    assert call["method"] == "POST"
    assert call["url"] == "https://api.brevo.com/v3/smtp/email"
    assert call["headers"]["api-key"] == "test-key"
    assert call["timeout"] <= 10
    body = call["json"]
    assert body["sender"] == {
        "email": "news@vrikshastudios.com",
        "name": "Vriksha Studios",
    }
    assert body["to"] == [{"email": "meena@example.com"}]
    assert body["textContent"] and body["htmlContent"]
    assert f"{STOREFRONT}/newsletter/confirm?token=" in body["textContent"]
    assert f"{STOREFRONT}/newsletter/unsubscribe?token=" in body["textContent"]
    assert f"{STOREFRONT}/newsletter/confirm?token=" in body["htmlContent"]


def test_no_raw_token_is_stored(client, db, brevo):
    subscribe(client)
    c, u = tokens(brevo)
    sub = row(db)
    for column in NewsletterSubscriber.__table__.columns:
        value = str(getattr(sub, column.name))
        assert c not in value and u not in value


def test_emails_are_lower_cased_and_case_variants_are_one_person(client, db, brevo):
    subscribe(client, "Meena@Example.COM")
    subscribe(client, "  meena@example.com")  # same person: within the hour, no resend
    assert db.query(NewsletterSubscriber).count() == 1
    assert row(db).email == "meena@example.com"
    assert len(brevo.to("/smtp/email")) == 1


def test_source_is_optional_and_trimmed(client, db):
    assert subscribe(client).status_code == 202
    assert row(db).source is None
    assert subscribe(client, "b@example.com", source="  home ").status_code == 202
    assert row(db, "b@example.com").source == "home"
    assert subscribe(client, "c@example.com", source="x" * 31).status_code == 422


def test_bad_email_is_rejected(client):
    assert subscribe(client, "not-an-email").status_code == 422


def test_spam_trap_field_does_nothing_but_looks_normal(client, db, brevo):
    r = subscribe(client, website="http://spam.example")
    assert r.status_code == 202
    assert r.json() == {"status": "check_your_email"}
    assert db.query(NewsletterSubscriber).count() == 0
    assert brevo.calls == []


def test_empty_spam_trap_field_is_a_real_sign_up(client, db, brevo):
    assert subscribe(client, website="").status_code == 202
    assert len(brevo.to("/smtp/email")) == 1


def test_already_confirmed_gets_the_same_202_and_no_email(client, brevo):
    confirmed(client, brevo)
    brevo.calls.clear()
    r = subscribe(client)
    assert r.status_code == 202
    assert r.json() == {"status": "check_your_email"}
    assert brevo.calls == []


def test_pending_address_is_emailed_again_only_after_an_hour(client, db, brevo):
    subscribe(client)
    subscribe(client)
    assert len(brevo.to("/smtp/email")) == 1

    sub = row(db)
    sub.last_confirmation_sent_at -= timedelta(minutes=61)
    db.commit()
    r = subscribe(client)
    assert r.status_code == 202
    assert len(brevo.to("/smtp/email")) == 2


def test_confirm_marks_confirmed_and_adds_to_the_brevo_list(client, db, brevo):
    subscribe(client)
    c, _ = tokens(brevo)
    r = client.post("/public/newsletter/confirm", json={"token": c})
    assert r.status_code == 200
    assert r.json() == {"status": "confirmed"}

    sub = row(db)
    assert sub.status == "confirmed" and sub.confirmed_at
    (call,) = brevo.to("/contacts")
    assert call["url"] == "https://api.brevo.com/v3/contacts"
    assert call["headers"]["api-key"] == "test-key"
    assert call["json"] == {
        "email": "meena@example.com",
        "listIds": [7],
        "updateEnabled": True,
    }


def test_confirming_twice_is_fine(client, db, brevo):
    c, _ = confirmed(client, brevo)
    first = row(db).confirmed_at
    r = client.post("/public/newsletter/confirm", json={"token": c})
    assert r.status_code == 200
    assert r.json() == {"status": "confirmed"}
    assert row(db).confirmed_at == first


@pytest.mark.parametrize(
    "token", ["", "garbage", "a.b.c", f"{uuid.uuid4().hex}.0.{'0' * 32}", "x" * 300]
)
def test_bad_tokens_are_400_for_confirm_and_unsubscribe(client, token):
    for path in ("confirm", "unsubscribe"):
        r = client.post(f"/public/newsletter/{path}", json={"token": token})
        assert r.status_code == 400
        assert r.json()["detail"] == "This link is not valid or has expired."


def test_a_tampered_token_is_refused(client, brevo):
    subscribe(client)
    c, u = tokens(brevo)
    sub_id, exp, sig = c.split(".")
    for bad in (
        f"{sub_id}.{int(exp) + 86400}.{sig}",  # longer expiry, same signature
        f"{sub_id}.{exp}.{'0' * 32}",
        f"{uuid.uuid4().hex}.{exp}.{sig}",  # someone else's id
    ):
        assert (
            client.post("/public/newsletter/confirm", json={"token": bad}).status_code
            == 400
        )


def test_confirm_and_unsubscribe_links_are_not_interchangeable(client, db, brevo):
    subscribe(client)
    c, u = tokens(brevo)
    assert (
        client.post("/public/newsletter/unsubscribe", json={"token": c}).status_code
        == 400
    )
    assert (
        client.post("/public/newsletter/confirm", json={"token": u}).status_code == 400
    )
    assert row(db).status == "pending"


def test_an_expired_confirm_link_is_400(client, db, brevo, monkeypatch):
    subscribe(client)
    c, _ = tokens(brevo)
    real_now = svc._now
    monkeypatch.setattr(svc, "_now", lambda: real_now() + timedelta(days=7, minutes=1))
    r = client.post("/public/newsletter/confirm", json={"token": c})
    assert r.status_code == 400
    assert r.json()["detail"] == "This link is not valid or has expired."
    assert row(db).status == "pending"
    assert brevo.to("/contacts") == []


def test_a_confirm_link_still_works_just_inside_seven_days(client, brevo, monkeypatch):
    subscribe(client)
    c, _ = tokens(brevo)
    real_now = svc._now
    monkeypatch.setattr(svc, "_now", lambda: real_now() + timedelta(days=6, hours=23))
    assert (
        client.post("/public/newsletter/confirm", json={"token": c}).status_code == 200
    )


def test_unsubscribe_removes_from_the_list_and_never_expires(
    client, db, brevo, monkeypatch
):
    _, u = confirmed(client, brevo)
    real_now = svc._now
    monkeypatch.setattr(svc, "_now", lambda: real_now() + timedelta(days=400))
    r = client.post("/public/newsletter/unsubscribe", json={"token": u})
    assert r.status_code == 200
    assert r.json() == {"status": "unsubscribed"}

    sub = row(db)
    assert sub.status == "unsubscribed" and sub.unsubscribed_at
    (call,) = brevo.to("/contacts/lists/7/contacts/remove")
    assert call["method"] == "POST"
    assert call["json"] == {"emails": ["meena@example.com"]}
    assert call["headers"]["api-key"] == "test-key"


def test_unsubscribing_twice_is_fine(client, db, brevo):
    _, u = confirmed(client, brevo)
    for _i in range(2):
        r = client.post("/public/newsletter/unsubscribe", json={"token": u})
        assert r.status_code == 200
        assert r.json() == {"status": "unsubscribed"}
    assert row(db).status == "unsubscribed"


def test_unsubscribe_when_brevo_does_not_know_the_contact_is_fine(client, brevo):
    _, u = confirmed(client, brevo)
    brevo.status = 400
    brevo.body = {"code": "document_not_found", "message": "Contact does not exist"}
    r = client.post("/public/newsletter/unsubscribe", json={"token": u})
    assert r.status_code == 200


def test_an_unsubscribe_link_works_even_if_never_confirmed(client, db, brevo):
    subscribe(client)
    _, u = tokens(brevo)
    assert (
        client.post("/public/newsletter/unsubscribe", json={"token": u}).status_code
        == 200
    )
    assert row(db).status == "unsubscribed"


def test_resubscribe_after_unsubscribing_goes_back_to_pending_and_emails(
    client, db, brevo
):
    _, u = confirmed(client, brevo)
    client.post("/public/newsletter/unsubscribe", json={"token": u})
    brevo.calls.clear()

    r = subscribe(client, source="journal")
    assert r.status_code == 202
    sub = row(db)
    assert sub.status == "pending"
    assert sub.source == "journal"
    assert sub.confirmed_at is None and sub.unsubscribed_at is None
    assert len(brevo.to("/smtp/email")) == 1  # even inside the hour: a new round

    c, _ = tokens(brevo)
    assert (
        client.post("/public/newsletter/confirm", json={"token": c}).status_code == 200
    )
    assert row(db).status == "confirmed"


def test_an_old_confirm_link_cannot_undo_an_unsubscribe(client, db, brevo):
    c, u = confirmed(client, brevo)
    client.post("/public/newsletter/unsubscribe", json={"token": u})
    brevo.calls.clear()
    r = client.post("/public/newsletter/confirm", json={"token": c})
    assert r.status_code == 400
    assert row(db).status == "unsubscribed"
    assert brevo.calls == []


def test_unconfigured_subscribe_is_503(client, db, brevo, monkeypatch):
    monkeypatch.setattr(settings, "BREVO_API_KEY", "")
    r = subscribe(client)
    assert r.status_code == 503
    assert r.json()["detail"] == "The newsletter is not set up yet."
    assert db.query(NewsletterSubscriber).count() == 0
    assert brevo.calls == []


@pytest.mark.parametrize(
    "name,value",
    [("BREVO_LIST_ID", 0), ("BREVO_SENDER_EMAIL", ""), ("STOREFRONT_URL", "")],
)
def test_any_missing_brevo_setting_counts_as_unconfigured(
    client, monkeypatch, name, value
):
    monkeypatch.setattr(settings, name, value)
    assert subscribe(client).status_code == 503


def test_unconfigured_confirm_and_unsubscribe_are_503(client, brevo, monkeypatch):
    subscribe(client)
    c, u = tokens(brevo)
    monkeypatch.setattr(settings, "BREVO_API_KEY", "")
    assert (
        client.post("/public/newsletter/confirm", json={"token": c}).status_code == 503
    )
    assert (
        client.post("/public/newsletter/unsubscribe", json={"token": u}).status_code
        == 503
    )


def test_in_dev_the_link_is_logged_instead_of_failing(
    client, db, brevo, monkeypatch, caplog
):
    monkeypatch.setattr(settings, "BREVO_API_KEY", "")
    monkeypatch.setattr(settings, "ENV", "dev")
    monkeypatch.setattr(settings, "IS_PROD", False)
    with caplog.at_level("WARNING"):
        r = subscribe(client)
    assert r.status_code == 202
    assert brevo.calls == []
    assert "/newsletter/confirm?token=" in caplog.text
    assert row(db).status == "pending"

    link = re.search(r"token=(\S+)", caplog.text).group(1)
    assert (
        client.post("/public/newsletter/confirm", json={"token": link}).status_code
        == 200
    )
    assert row(db).status == "confirmed"
    assert brevo.calls == []


def test_dev_mode_never_applies_in_production(client, brevo, monkeypatch):
    monkeypatch.setattr(settings, "BREVO_API_KEY", "")
    monkeypatch.setattr(settings, "ENV", "dev")
    monkeypatch.setattr(
        settings, "IS_PROD", True
    )  # ENV unset on Vercel still reads "dev"
    assert subscribe(client).status_code == 503


@pytest.mark.parametrize("failure", ["status", "network"])
def test_brevo_failure_on_subscribe_is_502_and_can_be_retried(
    client, db, brevo, failure
):
    if failure == "status":
        brevo.status = 500
    else:
        brevo.network_error = True
    r = subscribe(client)
    assert r.status_code == 502
    assert r.json()["detail"] == "We could not send the email. Please try again."
    assert row(db).last_confirmation_sent_at is None  # no "already sent" lockout

    brevo.status = None
    brevo.network_error = False
    assert subscribe(client).status_code == 202
    assert len(brevo.to("/smtp/email")) == 2
    assert row(db).last_confirmation_sent_at is not None


def test_brevo_failure_on_confirm_leaves_it_unconfirmed_so_the_link_can_be_retried(
    client, db, brevo
):
    subscribe(client)
    c, _ = tokens(brevo)
    brevo.status = 503
    assert (
        client.post("/public/newsletter/confirm", json={"token": c}).status_code == 502
    )
    assert row(db).status == "pending"

    brevo.status = None
    assert (
        client.post("/public/newsletter/confirm", json={"token": c}).status_code == 200
    )
    assert row(db).status == "confirmed"


def test_brevo_failure_on_unsubscribe_is_502_and_not_reported_as_done(
    client, db, brevo
):
    _, u = confirmed(client, brevo)
    brevo.network_error = True
    assert (
        client.post("/public/newsletter/unsubscribe", json={"token": u}).status_code
        == 502
    )
    assert row(db).status == "confirmed"

    brevo.network_error = False
    assert (
        client.post("/public/newsletter/unsubscribe", json={"token": u}).status_code
        == 200
    )
    assert row(db).status == "unsubscribed"


def test_the_routes_share_the_public_rate_limit(client, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_RATE_LIMIT", 2)
    ratelimit.reset()
    assert subscribe(client, "a@example.com").status_code == 202
    assert subscribe(client, "b@example.com").status_code == 202
    assert subscribe(client, "c@example.com").status_code == 429


def test_email_text_says_who_why_and_has_both_links(client, brevo):
    subscribe(client)
    sent = brevo.to("/smtp/email")[-1]["json"]
    assert "Vriksha Studios" in sent["textContent"]
    assert "asked for the Vriksha Studios newsletter" in sent["textContent"]
    assert "If this was not you" in sent["textContent"]
    assert sent["subject"]
    assert sent["htmlContent"].count("/newsletter/") == 2
