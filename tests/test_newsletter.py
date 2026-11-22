"""Newsletter double opt-in: sign-up, confirm, unsubscribe. Brevo is faked, no network."""

import logging
import re
import uuid
from datetime import timedelta

import httpx
import pytest

from app.core import ratelimit
from app.core.config import settings
from app.core.middleware import HideSecretInAccessLog
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
        self.fail_method = None  # answer 500 to this HTTP method only

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
        status = 500 if method == self.fail_method else (self.status or 201)
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
    # and a blocklist flag left over from Brevo is cleared (see the re-subscribe tests)
    (unblock,) = brevo.to("/contacts/meena%40example.com")
    assert unblock["method"] == "PUT"
    assert unblock["json"] == {"emailBlacklisted": False, "listIds": [7]}


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


# --- Brevo's webhook: people who leave inside Brevo ---------------------------------

SECRET = "hook-secret-0123456789"
HOOK = "/public/newsletter/brevo-webhook"


@pytest.fixture
def hook(monkeypatch):
    monkeypatch.setattr(settings, "BREVO_WEBHOOK_SECRET", SECRET)


def call_hook(client, event="unsubscribe", email="meena@example.com", **over):
    """What Brevo posts (marketing shape), with the secret in the URL."""
    body = {
        "event": event,
        "email": email,
        "id": 1,
        "camp_id": 9,
        "ts_event": 1790000000,
        **over,
    }
    return client.post(f"{HOOK}?secret={SECRET}", json=body)


def test_webhook_secret_in_the_url_or_in_a_header_is_accepted(client, db, brevo, hook):
    confirmed(client, brevo)
    assert call_hook(client).status_code == 200
    assert row(db).status == "unsubscribed"

    confirmed(client, brevo, "b@example.com")
    r = client.post(
        HOOK,
        json={"event": "unsubscribe", "email": "b@example.com"},
        headers={"X-Webhook-Secret": SECRET},
    )
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    assert row(db, "b@example.com").status == "unsubscribed"


@pytest.mark.parametrize(
    "url,headers",
    [
        (HOOK, {}),
        (f"{HOOK}?secret=", {}),
        (f"{HOOK}?secret=wrong-secret", {}),
        (f"{HOOK}?secret={SECRET}x", {}),
        (f"{HOOK}?secret={SECRET[:-1]}", {}),
        (HOOK, {"X-Webhook-Secret": "wrong-secret"}),
        (HOOK, {"X-Webhook-Secret": ""}),
        (HOOK, {"Authorization": f"Bearer {SECRET}"}),  # not a scheme we read
    ],
)
def test_webhook_with_a_bad_or_missing_secret_is_401_and_changes_nothing(
    client, db, brevo, hook, caplog, url, headers
):
    confirmed(client, brevo)
    body = {"event": "unsubscribe", "email": "meena@example.com"}
    with caplog.at_level("DEBUG"):
        r = client.post(url, json=body, headers=headers)
    assert r.status_code == 401
    logged = " ".join(x.getMessage() for x in caplog.records if x.name != "httpx")
    assert SECRET not in r.text and SECRET not in logged  # httpx is the test client's
    assert row(db).status == "confirmed"


def test_webhook_checks_the_secret_before_reading_the_body(client, hook):
    assert client.post(HOOK, json={"nonsense": True}).status_code == 401
    assert client.post(HOOK).status_code == 401


def test_webhook_secret_is_compared_in_constant_time(client, hook, monkeypatch):
    seen = []
    real = svc.hmac.compare_digest
    monkeypatch.setattr(
        svc.hmac, "compare_digest", lambda a, b: seen.append(1) or real(a, b)
    )
    client.post(f"{HOOK}?secret=wrong", json={})
    assert seen


@pytest.mark.parametrize("given", ["", "anything", SECRET])
def test_webhook_refuses_everything_when_no_secret_is_set(
    client, db, brevo, monkeypatch, given
):
    confirmed(client, brevo)
    monkeypatch.setattr(settings, "BREVO_WEBHOOK_SECRET", "")
    body = {"event": "unsubscribe", "email": "meena@example.com"}
    assert client.post(f"{HOOK}?secret={given}", json=body).status_code == 503
    r = client.post(HOOK, json=body, headers={"X-Webhook-Secret": given})
    assert r.status_code == 503
    assert client.post(HOOK, json=body).status_code == 503
    assert row(db).status == "confirmed"


@pytest.mark.parametrize(
    "event,status",
    [
        ("unsubscribe", "unsubscribed"),  # marketing
        ("unsubscribed", "unsubscribed"),  # transactional
        ("hard_bounce", "bounced"),  # marketing
        ("hardBounce", "bounced"),  # transactional
        ("spam", "complained"),
    ],
)
def test_each_event_marks_the_row_and_never_calls_brevo(
    client, db, brevo, hook, event, status
):
    confirmed(client, brevo)
    brevo.calls.clear()
    r = call_hook(client, event)
    assert r.status_code == 200
    sub = row(db)
    assert sub.status == status
    assert sub.unsubscribed_at is not None
    assert brevo.calls == []


def test_a_pending_address_is_marked_too(client, db, brevo, hook):
    subscribe(client)
    assert call_hook(client, "hard_bounce").status_code == 200
    assert row(db).status == "bounced"


def test_the_address_is_matched_ignoring_case_and_spaces(client, db, brevo, hook):
    confirmed(client, brevo)
    assert call_hook(client, email=" Meena@Example.COM ").status_code == 200
    assert row(db).status == "unsubscribed"


def test_an_unknown_address_is_a_quiet_200(client, db, brevo, hook):
    r = call_hook(client, email="nobody@example.com")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    assert db.query(NewsletterSubscriber).count() == 0


@pytest.mark.parametrize(
    "event",
    [
        "delivered",
        "opened",
        "click",
        "soft_bounce",
        "contact_deleted",
        "list_addition",
        "",
    ],
)
def test_events_we_do_not_act_on_are_a_200_and_change_nothing(
    client, db, brevo, hook, event
):
    confirmed(client, brevo)
    assert call_hook(client, event).status_code == 200
    assert row(db).status == "confirmed"


@pytest.mark.parametrize("body", [{}, {"event": "unsubscribe"}, {"email": "a@b.co"}])
def test_a_body_missing_event_or_email_is_ignored_not_an_error(
    client, db, brevo, hook, body
):
    confirmed(client, brevo)
    assert client.post(f"{HOOK}?secret={SECRET}", json=body).status_code == 200
    assert row(db).status == "confirmed"


def test_leaving_some_other_brevo_list_does_not_unsubscribe_here(
    client, db, brevo, hook
):
    confirmed(client, brevo)  # our list id is 7
    assert call_hook(client, list_id=[3, 4]).status_code == 200
    assert row(db).status == "confirmed"
    assert call_hook(client, list_id=[3, 7]).status_code == 200
    assert row(db).status == "unsubscribed"


def test_list_id_may_be_a_single_number(client, db, brevo, hook):
    confirmed(client, brevo)
    call_hook(client, list_id=3)
    assert row(db).status == "confirmed"
    call_hook(client, list_id=7)
    assert row(db).status == "unsubscribed"


def test_a_bounce_or_spam_report_counts_whatever_list_it_names(client, db, brevo, hook):
    confirmed(client, brevo)
    call_hook(client, "spam", list_id=[3])
    assert row(db).status == "complained"


def test_the_same_event_twice_changes_nothing_the_second_time(client, db, brevo, hook):
    confirmed(client, brevo)
    assert call_hook(client).status_code == 200
    first = row(db).unsubscribed_at
    assert call_hook(client).status_code == 200
    assert row(db).status == "unsubscribed"
    assert row(db).unsubscribed_at == first


def test_a_stronger_reason_replaces_a_weaker_one_but_never_the_reverse(
    client, db, brevo, hook
):
    confirmed(client, brevo)
    call_hook(client, "unsubscribe")
    first = row(db).unsubscribed_at
    call_hook(client, "spam")  # unsubscribed -> complained
    assert row(db).status == "complained"
    assert row(db).unsubscribed_at == first  # the first time they left stays on record
    call_hook(client, "hard_bounce")
    call_hook(client, "unsubscribe")
    assert row(db).status == "complained"


def test_the_webhook_is_not_behind_the_per_ip_rate_limit(
    client, db, brevo, hook, monkeypatch
):
    monkeypatch.setattr(settings, "PUBLIC_RATE_LIMIT", 1)
    ratelimit.reset()
    assert all(call_hook(client, "delivered").status_code == 200 for _ in range(10))


def test_the_access_log_masks_the_url_secret():
    rec = logging.LogRecord(
        "uvicorn.access",
        20,
        "",
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "POST", f"{HOOK}?a=1&secret={SECRET}&b=2", "1.1", 200),
        None,
    )
    assert HideSecretInAccessLog().filter(rec)
    assert SECRET not in rec.getMessage()
    assert f"{HOOK}?a=1&secret=***&b=2" in rec.getMessage()


# --- signing up again after leaving inside Brevo ------------------------------------


def test_resubscribe_after_a_brevo_side_unsubscribe_clears_the_blocklist(
    client, db, brevo, hook
):
    confirmed(client, brevo)
    call_hook(
        client
    )  # left through Brevo's own link; our row said "confirmed" until now
    brevo.calls.clear()

    r = subscribe(client, source="footer")
    assert r.status_code == 202
    assert row(db).status == "pending"
    assert len(brevo.to("/smtp/email")) == 1  # a new confirmation, not a silent return
    assert (
        brevo.to("/contacts") == []
    )  # nothing is added or unblocked before they confirm

    c, _ = tokens(brevo)
    assert (
        client.post("/public/newsletter/confirm", json={"token": c}).status_code == 200
    )
    sub = row(db)
    assert sub.status == "confirmed" and sub.unsubscribed_at is None

    add, unblock = [x for x in brevo.calls if "/contacts" in x["url"]]
    assert add["method"] == "POST" and add["json"]["listIds"] == [7]
    assert unblock["method"] == "PUT"
    assert unblock["url"] == "https://api.brevo.com/v3/contacts/meena%40example.com"
    assert unblock["json"] == {"emailBlacklisted": False, "listIds": [7]}
    assert unblock["headers"]["api-key"] == "test-key"


def test_confirmation_stays_retryable_when_clearing_the_blocklist_fails(
    client, db, brevo, hook
):
    confirmed(client, brevo)
    call_hook(client)
    subscribe(client)
    c, _ = tokens(brevo)

    brevo.fail_method = "PUT"
    r = client.post("/public/newsletter/confirm", json={"token": c})
    assert r.status_code == 502
    assert row(db).status == "pending"  # not confirmed while Brevo still blocks them

    brevo.fail_method = None
    r = client.post("/public/newsletter/confirm", json={"token": c})
    assert r.status_code == 200
    assert row(db).status == "confirmed"


def test_a_hard_bounced_address_may_sign_up_again_and_confirm(client, db, brevo, hook):
    confirmed(client, brevo)
    call_hook(client, "hard_bounce")
    brevo.calls.clear()
    assert subscribe(client).status_code == 202
    assert row(db).status == "pending"
    assert len(brevo.to("/smtp/email")) == 1  # they must receive it to get any further
    c, _ = tokens(brevo)
    assert (
        client.post("/public/newsletter/confirm", json={"token": c}).status_code == 200
    )
    assert row(db).status == "confirmed"


def test_someone_who_reported_spam_is_not_resubscribed_automatically(
    client, db, brevo, hook
):
    confirmed(client, brevo)
    call_hook(client, "spam")
    brevo.calls.clear()

    r = subscribe(client)  # the same plain 202 as always: nothing to tell the visitor
    assert r.status_code == 202
    assert r.json() == {"status": "check_your_email"}
    assert row(db).status == "complained"
    assert brevo.calls == []  # no email, nothing touched in Brevo


def test_old_links_and_our_own_unsubscribe_cannot_change_a_bounce_or_spam_row(
    client, db, brevo, hook
):
    c, u = confirmed(client, brevo)
    for event, status in (("hard_bounce", "bounced"), ("spam", "complained")):
        call_hook(client, event)
        brevo.calls.clear()
        r = client.post("/public/newsletter/confirm", json={"token": c})
        assert r.status_code == 400
        assert row(db).status == status
        assert brevo.to("/contacts") == []
        # their own opt-out link still works (it is idempotent) but keeps the reason
        client.post("/public/newsletter/unsubscribe", json={"token": u})
        assert row(db).status == status
