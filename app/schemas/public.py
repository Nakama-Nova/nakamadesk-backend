"""Schemas for the public (no-login) website API. Never include cost fields."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class PublicImage(BaseModel):
    url: str
    alt: Optional[str] = None


class PublicProduct(BaseModel):
    slug: str
    name: str
    item_type: str = "furniture"  # furniture or sculpture
    short_desc: Optional[str] = None
    price: Decimal
    made_to_order: bool
    lead_time_days: Optional[int] = None
    in_stock: bool
    wood_type: Optional[str] = None
    dimensions_mm: Optional[Dict[str, Any]] = None
    category: Optional[str] = None
    images: List[PublicImage] = []
    renders: List[PublicImage] = []  # finished-piece render pictures
    model_url: Optional[str] = None  # .glb for the 3D viewer, when there is one
    ribbon: Optional[str] = (
        None  # new, bestseller, few_left or offer: a label on the photo
    )


class CheckoutLine(BaseModel):
    slug: str
    quantity: int = Field(default=1, ge=1, le=20)


class CheckoutRequest(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    phone: str
    email: Optional[EmailStr] = None
    address: str = Field(min_length=5, max_length=300)
    town: str = Field(min_length=2, max_length=100)
    pincode: str = Field(pattern=r"^\d{6}$")
    note: Optional[str] = Field(default=None, max_length=500)
    # advance_upi: we confirm by phone, then the customer pays an advance by UPI.
    pay_option: Literal["advance_upi", "on_delivery"] = "advance_upi"
    items: List[CheckoutLine] = Field(min_length=1, max_length=20)


class EnquiryRequest(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    phone: str
    what: str = Field(min_length=2, max_length=200)
    description: str = Field(min_length=5, max_length=2000)
    wood: Optional[str] = Field(default=None, max_length=100)
    size: Optional[str] = Field(default=None, max_length=100)
    budget: Optional[str] = Field(default=None, max_length=100)


class PublicOrderLine(BaseModel):
    name: str
    quantity: int
    unit_price: Decimal


class PublicOrder(BaseModel):
    order_number: str
    status: str
    order_type: str
    placed_on: datetime
    expected_delivery: Optional[date] = None
    total: Decimal
    advance_paid: Decimal
    balance_due: Decimal
    # Once staff have confirmed the order and money is still due: a link that opens a
    # UPI app with the shop's id and this order number filled in. The amount is left
    # for the shopper to type (the advance is agreed by phone). Empty when no UPI id
    # is set up.
    upi_link: Optional[str] = None
    items: List[PublicOrderLine]


class PaymentInfo(BaseModel):
    """How to pay. There is no online gateway: staff confirm by phone first."""

    accepts: List[str]
    upi_id: Optional[str] = None
    payee_name: Optional[str] = None
    phone: Optional[str] = None
    steps: str


class DeliveryCheck(BaseModel):
    pincode: str
    serviceable: bool


class AssetResponse(BaseModel):
    id: UUID
    item_id: UUID
    kind: str
    url: str
    alt: Optional[str] = None
    sort: int
    bytes: int
    visibility: str = "public"  # public | staff
    storage: str = "local"  # local | public | s3

    model_config = ConfigDict(from_attributes=True)


class NewsletterSubscribe(BaseModel):
    email: EmailStr
    source: Optional[str] = Field(default=None, max_length=30)  # footer, journal, home
    website: Optional[str] = None  # hidden spam trap: real visitors leave it empty


class NewsletterToken(BaseModel):
    token: str = Field(max_length=500)  # garbage is a 400 from the service, not a 422


class NewsletterStatus(BaseModel):
    status: Literal["check_your_email", "confirmed", "unsubscribed"]


class BrevoWebhookEvent(BaseModel):
    """The few fields we use from Brevo's webhook body. Lenient on purpose: Brevo retries
    (and may disable the webhook) when it gets an error, so odd events are ignored, not 422.
    """

    event: Optional[str] = None
    email: Optional[str] = None
    list_id: Union[int, List[int], None] = None


class CommentSubmit(BaseModel):
    """A customer's comment. The order number AND the phone it was placed with are the proof they ordered."""

    order_number: str = Field(min_length=3, max_length=40)
    phone: str = Field(max_length=20)
    display_name: str = Field(min_length=2, max_length=60)
    place: Optional[str] = Field(default=None, max_length=60)
    text: str = Field(min_length=10, max_length=800)
    # They must agree to having it (and the name above) shown on the website
    consent: bool
    website: str = Field(default="", max_length=200)  # hidden spam trap, stays empty


class CommentReceived(BaseModel):
    status: str  # always "received": whether it was stored is not revealed to bots


class PublicComment(BaseModel):
    name: str
    place: Optional[str] = None
    piece: Optional[str] = None
    text: str


class PublicAnnouncement(BaseModel):
    """The line for the top bar of the website."""

    message: str
    link_url: Optional[str] = None
    link_label: Optional[str] = None


class PublicBanner(BaseModel):
    """The feature banner on the home page. `embed_url` is built by the server from a fixed template."""

    headline: str
    body: Optional[str] = None
    media_kind: str  # none, image, youtube or instagram
    image_url: Optional[str] = None
    embed_url: Optional[str] = None
    watch_url: Optional[str] = None  # the plain video page, for a "watch on ..." link
    button_url: Optional[str] = None
    button_label: Optional[str] = None


class HitIn(BaseModel):
    """One page view or action, for the daily counts. Carries no identifier of the visitor."""

    kind: Literal["view", "event"]
    key: str = Field(min_length=1, max_length=120)


class HitOut(BaseModel):
    ok: bool = True
