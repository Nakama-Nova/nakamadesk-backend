"""
Newsletter sign-up with double opt-in, kept in step with a Brevo list.

subscribe: save the address as pending and email a confirmation link (Brevo's
transactional API). confirm: the link marks it confirmed and adds it to the Brevo list.
unsubscribe: the link in every email takes it off the list. Campaigns are written and sent
by the owner in Brevo itself; nothing is sent in bulk from here.

The links carry a signed token, not the address: `<subscriber id>.<expiry>.<signature>`,
signed with a keyed hash that also names the purpose, so a confirm link can never
unsubscribe anyone and the other way round. Nothing is stored and nothing is logged.
Confirm links last 7 days; unsubscribe links never expire.

Every Brevo call is plain HTTPS inside the request with a short timeout (serverless
cannot be trusted to finish background work). When Brevo cannot be reached the person is
told so (502), never that it worked.
"""

import hashlib
import hmac
import html
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import settings
from app.models.newsletter_subscriber import NewsletterSubscriber

logger = logging.getLogger(__name__)

BREVO_URL = "https://api.brevo.com/v3"
BREVO_TIMEOUT = 8.0
CONSENT_VERSION = "2026-11-v1"  # bump when the sign-up wording changes
CONFIRM_DAYS = 7
RESEND_AFTER = timedelta(hours=1)
BAD_LINK = "This link is not valid or has expired."
NOT_SET_UP = "The newsletter is not set up yet."
SEND_FAILED = "We could not send the email. Please try again."
BREVO_FAILED = "We could not reach the newsletter service. Please try again."
STORE_NAME = "Vriksha Studios"


class BrevoError(RuntimeError):
    """Brevo was unreachable or refused the request."""


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _configured() -> bool:
    return bool(
        settings.BREVO_API_KEY
        and settings.BREVO_LIST_ID
        and settings.BREVO_SENDER_EMAIL
        and settings.STOREFRONT_URL
    )


def _log_only() -> bool:
    """Local development without Brevo: log the link instead of failing."""
    return settings.ENV == "dev" and not settings.IS_PROD


def _base_url() -> str:
    return settings.STOREFRONT_URL or "http://localhost:3000"


# --- signed links --------------------------------------------------------------------


def _sign(purpose: str, sub_id: str, expiry: int) -> str:
    key = (settings.NEWSLETTER_TOKEN_SECRET or security.SECRET_KEY).encode()
    msg = f"newsletter:{purpose}:{sub_id}:{expiry}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:32]


def make_token(purpose: str, sub: NewsletterSubscriber) -> str:
    sub_id = sub.id.hex
    expiry = 0  # unsubscribe links do not expire
    if purpose == "confirm":
        expiry = int(
            (_now() + timedelta(days=CONFIRM_DAYS))
            .replace(tzinfo=timezone.utc)
            .timestamp()
        )
    return f"{sub_id}.{expiry}.{_sign(purpose, sub_id, expiry)}"


def _subscriber_for(db: Session, purpose: str, token: str) -> NewsletterSubscriber:
    """The row a valid, unexpired token belongs to, or 400."""
    try:
        sub_id, exp, sig = token.split(".")
        expiry = int(exp)
        row_id = uuid.UUID(sub_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=BAD_LINK) from None
    if not hmac.compare_digest(sig, _sign(purpose, sub_id, expiry)):
        raise HTTPException(status_code=400, detail=BAD_LINK)
    now = _now().replace(tzinfo=timezone.utc).timestamp()
    if purpose == "confirm" and expiry < now:
        raise HTTPException(status_code=400, detail=BAD_LINK)
    row = db.get(NewsletterSubscriber, row_id)
    if row is None:
        raise HTTPException(status_code=400, detail=BAD_LINK)
    return row


# --- Brevo ---------------------------------------------------------------------------


def _brevo(method: str, path: str, body: dict, ok_400: tuple = ()) -> None:
    """One Brevo call. Raises BrevoError on a network failure or an error answer."""
    try:
        r = httpx.request(
            method,
            f"{BREVO_URL}{path}",
            json=body,
            headers={"api-key": settings.BREVO_API_KEY, "accept": "application/json"},
            timeout=BREVO_TIMEOUT,
        )
    except httpx.HTTPError as e:
        raise BrevoError(f"{path}: {type(e).__name__}") from None
    if r.is_success:
        return
    try:
        code = str(r.json().get("code", ""))
    except Exception:
        code = ""
    if r.status_code == 400 and code in ok_400:
        return
    raise BrevoError(f"{path}: HTTP {r.status_code} {code}")


def _add_to_list(email: str) -> None:
    # updateEnabled: an address that is already a Brevo contact is updated, not refused
    _brevo(
        "POST",
        "/contacts",
        {"email": email, "listIds": [settings.BREVO_LIST_ID], "updateEnabled": True},
    )


def _remove_from_list(email: str) -> None:
    # a contact Brevo does not know is already off the list
    _brevo(
        "POST",
        f"/contacts/lists/{settings.BREVO_LIST_ID}/contacts/remove",
        {"emails": [email]},
        ok_400=("document_not_found", "Contact email not found"),
    )


def _confirmation_email(sub: NewsletterSubscriber) -> tuple:
    base = _base_url()
    confirm = f"{base}/newsletter/confirm?token={make_token('confirm', sub)}"
    leave = f"{base}/newsletter/unsubscribe?token={make_token('unsubscribe', sub)}"
    subject = f"Please confirm your {STORE_NAME} newsletter sign-up"
    text = (
        "Hello,\n\n"
        f"Someone, hopefully you, asked for the {STORE_NAME} newsletter to be sent to "
        "this email address. It is a short note now and then about new pieces and "
        "what is happening in our workshop.\n\n"
        f"To say yes, please confirm here (the link works for {CONFIRM_DAYS} days):\n"
        f"{confirm}\n\n"
        "If this was not you, just ignore this email. Nothing will be sent to you "
        "unless you confirm.\n\n"
        f"Warmly,\n{STORE_NAME}\n\n"
        f"Changed your mind? You can opt out any time: {leave}\n"
    )
    page = (
        '<div style="font-family:Georgia,serif;font-size:16px;line-height:1.6;'
        'color:#2b2118;max-width:520px">'
        "<p>Hello,</p>"
        f"<p>Someone, hopefully you, asked for the {STORE_NAME} newsletter to be sent "
        "to this email address. It is a short note now and then about new pieces and "
        "what is happening in our workshop.</p>"
        f'<p><a href="{html.escape(confirm)}" style="display:inline-block;'
        "padding:12px 22px;background:#5a3a1e;color:#ffffff;text-decoration:none;"
        'border-radius:4px">Yes, send me the newsletter</a></p>'
        f"<p>The button works for {CONFIRM_DAYS} days. If this was not you, just ignore "
        "this email. Nothing will be sent to you unless you confirm.</p>"
        f"<p>Warmly,<br>{STORE_NAME}</p>"
        '<p style="font-size:12px;color:#7a6a5a">Changed your mind? '
        f'<a href="{html.escape(leave)}" style="color:#7a6a5a">Opt out any time</a>.</p>'
        "</div>"
    )
    return subject, text, page, confirm


def _send_confirmation(sub: NewsletterSubscriber) -> None:
    subject, text, page, confirm = _confirmation_email(sub)
    if not _configured():  # only reached in development (see subscribe)
        logger.warning(
            "NEWSLETTER confirmation not sent (Brevo not set up): %s", confirm
        )
        return
    _brevo(
        "POST",
        "/smtp/email",
        {
            "sender": {
                "email": settings.BREVO_SENDER_EMAIL,
                "name": settings.BREVO_SENDER_NAME,
            },
            "to": [{"email": sub.email}],
            "subject": subject,
            "textContent": text,
            "htmlContent": page,
        },
    )


# --- the three actions ---------------------------------------------------------------


def _clean_source(source: Optional[str]) -> Optional[str]:
    return (source or "").strip()[:30] or None


def subscribe(
    db: Session, email: str, source: Optional[str], website: Optional[str]
) -> None:
    """
    Save the address as pending and email the confirmation link. Quietly does nothing for
    the spam-trap field or an address that is already confirmed, so the caller can never
    tell who is subscribed. A pending address is emailed again at most once an hour.
    """
    if website and website.strip():
        return
    if not _configured() and not _log_only():
        raise HTTPException(status_code=503, detail=NOT_SET_UP)

    email = email.strip().lower()
    now = _now()
    sub = db.query(NewsletterSubscriber).filter_by(email=email).one_or_none()
    if sub is None:
        sub = NewsletterSubscriber(
            email=email,
            status="pending",
            source=_clean_source(source),
            consent_version=CONSENT_VERSION,
            subscribed_at=now,
        )
        db.add(sub)
        try:
            db.commit()
        except IntegrityError:  # the same address signed up a moment ago
            db.rollback()
            sub = db.query(NewsletterSubscriber).filter_by(email=email).one()
    elif sub.status == "confirmed":
        return
    elif sub.status == "unsubscribed":  # a new round of consent
        sub.status = "pending"
        sub.source = _clean_source(source) or sub.source
        sub.consent_version = CONSENT_VERSION
        sub.subscribed_at = now
        sub.confirmed_at = None
        sub.unsubscribed_at = None
        sub.last_confirmation_sent_at = None
        db.commit()
    elif (
        sub.last_confirmation_sent_at
        and now - sub.last_confirmation_sent_at < RESEND_AFTER
    ):
        return

    try:
        _send_confirmation(sub)
    except BrevoError as e:
        logger.error("newsletter confirmation email failed: %s", e)
        raise HTTPException(status_code=502, detail=SEND_FAILED) from None
    # only now: a failed send must not stop the visitor trying again
    sub.last_confirmation_sent_at = now
    db.commit()


def confirm(db: Session, token: str) -> None:
    """The confirm link: mark confirmed and add to the Brevo list. Safe to repeat."""
    sub = _subscriber_for(db, "confirm", token)
    if sub.status == "unsubscribed":  # an old link must not undo a later opt-out
        raise HTTPException(status_code=400, detail=BAD_LINK)
    if not _configured():
        if not _log_only():
            raise HTTPException(status_code=503, detail=NOT_SET_UP)
        logger.warning("NEWSLETTER not added to Brevo (not set up) in development")
    else:
        try:
            _add_to_list(sub.email)
        except BrevoError as e:
            logger.error("newsletter list add failed: %s", e)
            raise HTTPException(status_code=502, detail=BREVO_FAILED) from None
    if sub.status != "confirmed":
        sub.status = "confirmed"
        sub.confirmed_at = _now()
        db.commit()


def unsubscribe(db: Session, token: str) -> None:
    """The unsubscribe link: take off the Brevo list and mark. Safe to repeat."""
    sub = _subscriber_for(db, "unsubscribe", token)
    if not _configured():
        if not _log_only():
            raise HTTPException(status_code=503, detail=NOT_SET_UP)
        logger.warning("NEWSLETTER not removed from Brevo (not set up) in development")
    else:
        try:
            _remove_from_list(sub.email)
        except BrevoError as e:
            logger.error("newsletter list removal failed: %s", e)
            raise HTTPException(status_code=502, detail=BREVO_FAILED) from None
    if sub.status != "unsubscribed":
        sub.status = "unsubscribed"
        sub.unsubscribed_at = _now()
        db.commit()
