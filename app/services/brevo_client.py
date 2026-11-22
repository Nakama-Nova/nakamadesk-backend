"""
The one place that talks to Brevo's HTTP API, shared by the newsletter and the order
emails. Plain `httpx` with a short timeout; every failure becomes a BrevoError that
carries no secret, no address and no response body.
"""

import httpx

from app.core.config import settings

BREVO_URL = "https://api.brevo.com/v3"
BREVO_TIMEOUT = 8.0


class BrevoError(RuntimeError):
    """Brevo was unreachable or refused the request."""


def call(method: str, path: str, body: dict, ok_400: tuple = ()) -> None:
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


def send_email(to: str, subject: str, text: str, html: str) -> None:
    """A transactional email from the configured sender (POST /smtp/email)."""
    call(
        "POST",
        "/smtp/email",
        {
            "sender": {
                "email": settings.BREVO_SENDER_EMAIL,
                "name": settings.BREVO_SENDER_NAME,
            },
            "to": [{"email": to}],
            "subject": subject,
            "textContent": text,
            "htmlContent": html,
        },
    )
