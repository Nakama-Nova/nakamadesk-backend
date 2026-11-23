"""
Public website API: no login, read-mostly, rate limited.

Staff never call these; the storefront does. Costs, stock counts and other
customers' data are never returned.
"""

from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query
from sqlalchemy.orm import Session

from app.core.ratelimit import public_rate_limit
from app.db.deps import get_db, get_uow
from app.repositories.base import AbstractUnitOfWork
from app.schemas.public import (
    BrevoWebhookEvent,
    CheckoutRequest,
    CommentReceived,
    CommentSubmit,
    DeliveryCheck,
    EnquiryRequest,
    NewsletterStatus,
    NewsletterSubscribe,
    NewsletterToken,
    PaymentInfo,
    PublicComment,
    PublicOrder,
    PublicProduct,
)
from app.services import comment_service, newsletter_service, public_service

router = APIRouter(prefix="/public", tags=["Public website"])


@router.get("/catalog", response_model=List[PublicProduct])
def catalog(
    q: Optional[str] = Query(
        None, max_length=100, description="Search name/description"
    ),
    category: Optional[str] = Query(None, max_length=100),
    limit: int = Query(24, ge=1, le=50),
    offset: int = Query(0, ge=0),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """Published products, A to Z."""
    return public_service.list_catalog(uow, q, category, limit, offset)


@router.get("/catalog/{slug}", response_model=PublicProduct)
def product(slug: str, uow: AbstractUnitOfWork = Depends(get_uow)):
    """One published product with its photos and (if any) 3D model."""
    return public_service.get_product(uow, slug)


@router.get("/payment-info", response_model=PaymentInfo)
def payment_info():
    """How to pay: confirm with staff by phone, then cash or UPI."""
    return public_service.payment_info()


@router.get("/delivery-check", response_model=DeliveryCheck)
def delivery_check(pincode: str = Query(..., pattern=r"^\d{6}$")):
    """Do we deliver to this pincode?"""
    return DeliveryCheck(
        pincode=pincode, serviceable=public_service.is_serviceable(pincode)
    )


@router.post(
    "/checkout",
    response_model=PublicOrder,
    status_code=201,
    dependencies=[Depends(public_rate_limit("checkout"))],
)
def checkout(
    req: CheckoutRequest,
    background: BackgroundTasks,
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Guest checkout. Creates a draft order; nothing is charged. Staff confirm by
    phone, then the customer pays an advance by UPI. Prices come from the server.
    """
    return public_service.checkout(uow, req, background=background)


@router.post(
    "/enquiry",
    response_model=PublicOrder,
    status_code=201,
    dependencies=[Depends(public_rate_limit("enquiry"))],
)
def enquiry(req: EnquiryRequest, uow: AbstractUnitOfWork = Depends(get_uow)):
    """Custom piece request. Creates a draft custom order for staff to quote."""
    return public_service.enquiry(uow, req)


@router.get(
    "/orders/{order_number}",
    response_model=PublicOrder,
    dependencies=[Depends(public_rate_limit("track"))],
)
def track_order(
    order_number: str,
    phone: str = Query(..., max_length=20),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """Order status. Needs the order number AND the phone it was placed with."""
    return public_service.track(uow, order_number, phone)


@router.get("/comments", response_model=List[PublicComment])
def comments(uow: AbstractUnitOfWork = Depends(get_uow)):
    """What customers said, only the ones staff approved."""
    return comment_service.list_public(uow)


@router.post(
    "/comments",
    response_model=CommentReceived,
    status_code=201,
    dependencies=[Depends(public_rate_limit("comments"))],
)
def send_comment(body: CommentSubmit, uow: AbstractUnitOfWork = Depends(get_uow)):
    """
    A customer's comment about their order. Needs the order number AND the phone it was
    placed with, and the order must be confirmed. It is held until staff approve it.
    """
    comment_service.submit(uow, body)
    return CommentReceived(status="received")


@router.post(
    "/newsletter/subscribe",
    response_model=NewsletterStatus,
    status_code=202,
    dependencies=[Depends(public_rate_limit("newsletter"))],
)
def newsletter_subscribe(body: NewsletterSubscribe, db: Session = Depends(get_db)):
    """Ask for the newsletter: emails a confirmation link. Same reply for every address."""
    newsletter_service.subscribe(db, body.email, body.source, body.website)
    return NewsletterStatus(status="check_your_email")


@router.post(
    "/newsletter/confirm",
    response_model=NewsletterStatus,
    dependencies=[Depends(public_rate_limit("newsletter"))],
)
def newsletter_confirm(body: NewsletterToken, db: Session = Depends(get_db)):
    """The link in the confirmation email: adds the address to the mailing list."""
    newsletter_service.confirm(db, body.token)
    return NewsletterStatus(status="confirmed")


@router.post(
    "/newsletter/unsubscribe",
    response_model=NewsletterStatus,
    dependencies=[Depends(public_rate_limit("newsletter"))],
)
def newsletter_unsubscribe(body: NewsletterToken, db: Session = Depends(get_db)):
    """The link in every email: takes the address off the mailing list."""
    newsletter_service.unsubscribe(db, body.token)
    return NewsletterStatus(status="unsubscribed")


def _brevo_webhook_auth(
    secret: Optional[str] = Query(None, max_length=200),
    x_webhook_secret: Optional[str] = Header(None, max_length=200),
) -> None:
    """The shared secret, in the `X-Webhook-Secret` header or the `?secret=` of the URL."""
    newsletter_service.check_webhook_secret(x_webhook_secret, secret)


# Called by Brevo's servers, not a browser: no CORS. Deliberately NOT behind
# public_rate_limit: that limiter is per IP and Brevo sends bursts from a handful of IPs
# (and retries), so it would answer 429 to real events. The secret is the gate instead.
@router.post(
    "/newsletter/brevo-webhook",
    dependencies=[Depends(_brevo_webhook_auth)],
)
def newsletter_brevo_webhook(body: BrevoWebhookEvent, db: Session = Depends(get_db)):
    """Brevo tells us someone unsubscribed, hard-bounced or marked a campaign as spam."""
    newsletter_service.apply_brevo_event(db, body.event, body.email, body.list_id)
    return {"status": "ok"}
