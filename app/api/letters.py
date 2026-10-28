"""
Official letters: quotation, order confirmation with its advance receipt, purchase
receipt (a piece bought from another artisan) and the certificate of a finished temple
restoration. Each is numbered once (QTN-2026-27-0001), saved as a snapshot, and printed as
a PDF on the shop's letterhead. A wrong letter is voided, never deleted.

Who may issue and see each kind is in letter_service.ROLES: the owner all of them, a
manager all but certificates, sales only quotations and order confirmations.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import or_

from app.db.deps import check_role, get_uow
from app.models.enums import UserRole
from app.models.letter import KINDS, Letter
from app.models.user import User
from app.repositories.base import AbstractUnitOfWork
from app.services import letter_pdf_service, letter_service

router = APIRouter(prefix="/letters", tags=["Letters"])

Kind = Literal["quotation", "order_confirmation", "purchase_receipt", "completion_certificate"]  # fmt: skip
assert set(Kind.__args__) == set(KINDS)


class ProfileIn(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    tagline: Optional[str] = Field(default=None, max_length=160)
    address: Optional[str] = Field(default=None, max_length=300)
    phone: Optional[str] = Field(default=None, max_length=60)
    email: Optional[str] = Field(default=None, max_length=120)
    gstin: Optional[str] = Field(default=None, max_length=20)
    signatory_name: Optional[str] = Field(default=None, max_length=120)
    signatory_title: Optional[str] = Field(default=None, max_length=120)
    footer_note: Optional[str] = Field(default=None, max_length=240)


class QuotationLine(BaseModel):
    description: str = Field(min_length=1, max_length=300)
    quantity: int = Field(default=1, ge=1, le=10000)
    unit_price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)


class QuotationIn(BaseModel):
    customer_id: Optional[UUID] = None
    party_name: Optional[str] = Field(default=None, max_length=120)
    party_phone: Optional[str] = Field(default=None, max_length=40)
    party_address: Optional[str] = Field(default=None, max_length=300)
    order_id: Optional[UUID] = (
        None  # links the quotation; supplies the lines if none given
    )
    subject: Optional[str] = Field(default=None, max_length=200)
    lines: List[QuotationLine] = Field(default_factory=list, max_length=50)
    valid_days: int = Field(default=15, ge=1, le=365)
    issued_on: Optional[date] = None
    notes: Optional[str] = Field(default=None, max_length=1000)
    terms: Optional[List[str]] = Field(default=None, max_length=10)


class OrderConfirmationIn(BaseModel):
    order_id: UUID
    issued_on: Optional[date] = None
    notes: Optional[str] = Field(default=None, max_length=1000)


class PurchaseReceiptIn(BaseModel):
    supplier_id: Optional[UUID] = None
    seller_name: Optional[str] = Field(default=None, max_length=120)
    seller_phone: Optional[str] = Field(default=None, max_length=40)
    seller_address: Optional[str] = Field(default=None, max_length=300)
    item_id: Optional[UUID] = None
    description: str = Field(min_length=1, max_length=300)
    quantity: int = Field(default=1, ge=1, le=10000)
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    paid_on: Optional[date] = None
    method: str = Field(default="cash", min_length=1, max_length=30)
    reference: Optional[str] = Field(default=None, max_length=120)
    notes: Optional[str] = Field(default=None, max_length=1000)


class CompletionIn(BaseModel):
    restoration_id: UUID
    work_done: str = Field(min_length=1, max_length=2000)
    completed_on: Optional[date] = None
    notes: Optional[str] = Field(default=None, max_length=1000)


class VoidIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


class LetterOut(BaseModel):
    id: UUID
    number: str
    kind: str
    kind_label: str
    status: str
    issued_on: date
    subject: str
    party_name: str
    amount: Optional[Decimal]
    order_id: Optional[UUID]
    item_id: Optional[UUID]
    restoration_id: Optional[UUID]
    supplier_id: Optional[UUID]
    created_at: datetime
    voided_at: Optional[datetime]
    void_reason: Optional[str]


class LetterDetail(LetterOut):
    payload: Dict[str, Any]


def _out(letter: Letter) -> dict:
    return {**{c.name: getattr(letter, c.name) for c in Letter.__table__.columns}, "kind_label": KINDS[letter.kind][1]}  # fmt: skip


def _letter(uow: AbstractUnitOfWork, user: User, letter_id: UUID) -> Letter:
    """A letter this user may see; one they may not is simply not found."""
    letter = uow.session.get(Letter, letter_id)
    if not letter or user.role not in letter_service.ROLES[letter.kind]:
        raise HTTPException(status_code=404, detail="Letter not found")
    return letter


def _issue(uow, user, kind, builder, data) -> dict:
    with uow:
        letter = builder(uow.session, user, data)
        uow.commit()
    uow.refresh(letter)
    return _out(letter)


# ---------------------------------------------------------------- letterhead
@router.get("/profile", response_model=ProfileIn)
def get_profile(
    user: User = Depends(check_role(letter_service.ALL_ROLES)),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    return letter_service.get_profile(uow.session)


@router.put("/profile", response_model=ProfileIn)
def put_profile(
    req: ProfileIn,
    user: User = Depends(check_role([UserRole.OWNER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    with uow:
        profile = letter_service.save_profile(uow.session, req.model_dump(exclude_unset=True))  # fmt: skip
        uow.commit()
    return profile


# ------------------------------------------------------------------- issuing
@router.post("/quotation", response_model=LetterOut, status_code=201)
def issue_quotation(
    req: QuotationIn,
    user: User = Depends(check_role(letter_service.ROLES["quotation"])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    return _issue(uow, user, "quotation", letter_service.issue_quotation, req)


@router.post("/order-confirmation", response_model=LetterOut, status_code=201)
def issue_order_confirmation(
    req: OrderConfirmationIn,
    user: User = Depends(check_role(letter_service.ROLES["order_confirmation"])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    return _issue(uow, user, "order_confirmation", letter_service.issue_order_confirmation, req)  # fmt: skip


@router.post("/purchase-receipt", response_model=LetterOut, status_code=201)
def issue_purchase_receipt(
    req: PurchaseReceiptIn,
    user: User = Depends(check_role(letter_service.ROLES["purchase_receipt"])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    return _issue(uow, user, "purchase_receipt", letter_service.issue_purchase_receipt, req)  # fmt: skip


@router.post("/completion-certificate", response_model=LetterOut, status_code=201)
def issue_completion_certificate(
    req: CompletionIn,
    user: User = Depends(check_role(letter_service.ROLES["completion_certificate"])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    return _issue(uow, user, "completion_certificate", letter_service.issue_completion_certificate, req)  # fmt: skip


# ------------------------------------------------------------------- reading
@router.get("", response_model=List[LetterOut])
def list_letters(
    kind: Optional[Kind] = Query(default=None),
    status: Optional[Literal["issued", "void"]] = Query(default=None),
    order_id: Optional[UUID] = Query(default=None),
    restoration_id: Optional[UUID] = Query(default=None),
    search: Optional[str] = Query(
        default=None, description="number, name or subject contains this"
    ),  # fmt: skip
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(check_role(letter_service.ALL_ROLES)),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    query = uow.session.query(Letter).filter(Letter.kind.in_(letter_service.kinds_for(user.role)))  # fmt: skip
    if kind:
        query = query.filter(Letter.kind == kind)
    if status:
        query = query.filter(Letter.status == status)
    if order_id:
        query = query.filter(Letter.order_id == order_id)
    if restoration_id:
        query = query.filter(Letter.restoration_id == restoration_id)
    if search and search.strip():
        like = f"%{search.strip()}%"
        query = query.filter(or_(Letter.number.ilike(like), Letter.party_name.ilike(like), Letter.subject.ilike(like)))  # fmt: skip
    rows = query.order_by(Letter.created_at.desc()).offset(offset).limit(limit).all()
    return [_out(x) for x in rows]


@router.get("/{letter_id}", response_model=LetterDetail)
def get_letter(
    letter_id: UUID,
    user: User = Depends(check_role(letter_service.ALL_ROLES)),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    letter = _letter(uow, user, letter_id)
    return {**_out(letter), "payload": letter.payload}


@router.get("/{letter_id}/pdf")
def letter_pdf(
    letter_id: UUID,
    user: User = Depends(check_role(letter_service.ALL_ROLES)),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    letter = _letter(uow, user, letter_id)
    return Response(
        content=letter_pdf_service.render(letter),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{letter.number}.pdf"'},
    )


@router.post("/{letter_id}/void", response_model=LetterOut)
def void_letter(
    letter_id: UUID,
    req: VoidIn,
    user: User = Depends(check_role([UserRole.OWNER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    letter = _letter(uow, user, letter_id)
    if letter.status == "void":
        raise HTTPException(status_code=409, detail="This letter is already void")
    with uow:
        letter.status = "void"
        letter.voided_at = datetime.now(timezone.utc)
        letter.voided_by = user.id
        letter.void_reason = req.reason.strip()
        uow.commit()
    uow.refresh(letter)
    return _out(letter)
