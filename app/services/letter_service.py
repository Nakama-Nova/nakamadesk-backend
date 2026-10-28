"""
Issuing official letters: the number (issued once, per financial year), the letterhead and
the snapshot each letter is drawn from. The functions here only build and add rows; the
caller commits (inside `with uow:`), so a number is used only if the letter is saved.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.customer import Customer
from app.models.enums import UserRole
from app.models.item import Item
from app.models.letter import KINDS, BusinessProfile, Letter
from app.models.order import Order
from app.models.restoration import RestorationProject
from app.models.supplier import Supplier
from app.models.user import User

# who may issue (and therefore see) each kind of letter
ROLES = {
    "quotation": [UserRole.OWNER, UserRole.MANAGER, UserRole.SALES],
    "order_confirmation": [UserRole.OWNER, UserRole.MANAGER, UserRole.SALES],
    "purchase_receipt": [UserRole.OWNER, UserRole.MANAGER],
    "completion_certificate": [UserRole.OWNER],
}
ALL_ROLES = sorted(
    {r for roles in ROLES.values() for r in roles}, key=lambda r: r.value
)

DEFAULT_PROFILE = {
    "name": "Vriksha Studios",
    "tagline": "Handcrafted wood furniture and temple carvings",
    "address": None,
    "phone": None,
    "email": None,
    "gstin": None,
    "signatory_name": None,
    "signatory_title": "Proprietor",
    "footer_note": None,
}
PROFILE_FIELDS = tuple(DEFAULT_PROFILE)
QUOTATION_TERMS = [
    "An advance is taken to begin work; the balance is due on delivery.",
    "Prices are for the items and sizes listed. Changes are quoted separately.",
]


def kinds_for(role) -> List[str]:
    return [k for k, roles in ROLES.items() if role in roles]


def money(value) -> str:
    return str(Decimal(str(value)).quantize(Decimal("0.01")))


# ----------------------------------------------------------------- letterhead
def get_profile(db: Session) -> Dict[str, Optional[str]]:
    """The saved letterhead, or the defaults until the owner saves one."""
    row = db.get(BusinessProfile, 1)
    if not row:
        return dict(DEFAULT_PROFILE)
    return {f: getattr(row, f) for f in PROFILE_FIELDS}


def save_profile(db: Session, values: Dict[str, Optional[str]]) -> Dict:
    row = db.get(BusinessProfile, 1)
    if not row:
        row = BusinessProfile(id=1, **DEFAULT_PROFILE)
        db.add(row)
    for field in PROFILE_FIELDS:
        if field in values:
            value = values[field]
            value = value.strip() if isinstance(value, str) else value
            setattr(row, field, value or None)
    if not row.name:
        raise HTTPException(status_code=422, detail="The letterhead needs a name")
    db.flush()
    return get_profile(db)


# -------------------------------------------------------------------- numbers
def financial_year(d: date) -> str:
    """Indian financial year: 1 April 2026 to 31 March 2027 is 2026-27."""
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def next_number(db: Session, kind: str, d: date) -> str:
    """QTN-2026-27-0001. The counter row stays locked until the caller commits, so two
    letters saved at the same moment never share a number."""
    prefix = f"{KINDS[kind][0]}-{financial_year(d)}"
    n = db.execute(
        text(
            "INSERT INTO sku_counters (prefix, last_no) VALUES (:p, 1) "
            "ON CONFLICT (prefix) DO UPDATE SET last_no = sku_counters.last_no + 1 "
            "RETURNING last_no"
        ),
        {"p": prefix},
    ).scalar_one()
    return f"{prefix}-{n:04d}"


# -------------------------------------------------------------------- issuing
def _issue(
    db: Session,
    user: User,
    kind: str,
    *,
    issued_on: date,
    subject: str,
    party: Dict[str, Optional[str]],
    amount: Optional[Decimal],
    payload: Dict,
    **links,
) -> Letter:
    payload = {"letterhead": get_profile(db), "party": party, **payload}
    letter = Letter(
        number=next_number(db, kind, issued_on),
        kind=kind,
        issued_on=issued_on,
        subject=subject.strip(),
        party_name=party["name"],
        amount=amount,
        payload=payload,
        created_by=user.id,
        **links,
    )
    db.add(letter)
    db.flush()
    return letter


def _lines(rows) -> tuple:
    """[(description, quantity, unit_price)] -> JSON lines and their total."""
    lines, total = [], Decimal("0")
    for description, quantity, unit_price in rows:
        amount = Decimal(unit_price) * quantity
        total += amount
        lines.append(
            {
                "description": description.strip(),
                "quantity": quantity,
                "unit_price": money(unit_price),
                "amount": money(amount),
            }
        )
    return lines, total


def _customer(db: Session, customer_id) -> Optional[Customer]:
    if not customer_id:
        return None
    customer = db.get(Customer, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


def _order(db: Session, order_id) -> Order:
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


def _order_rows(order: Order):
    return [
        (
            line.item_name or (line.item.name if line.item else "Item"),
            line.quantity,
            line.unit_price,
        )
        for line in order.items
    ]


def issue_quotation(db, user, data) -> Letter:
    order = _order(db, data.order_id) if data.order_id else None
    customer = _customer(db, data.customer_id or (order.customer_id if order else None))
    rows = [(x.description, x.quantity, x.unit_price) for x in data.lines]
    if not rows and order:
        rows = _order_rows(order)
    if not rows:
        raise HTTPException(status_code=422, detail="Add at least one line")
    party = {
        "name": data.party_name or (customer.name if customer else None),
        "phone": data.party_phone or (customer.phone if customer else None),
        "address": data.party_address or (customer.address if customer else None),
    }
    if not party["name"]:
        raise HTTPException(status_code=422, detail="Say who the quotation is for")
    today = data.issued_on or date.today()
    lines, total = _lines(rows)
    return _issue(
        db,
        user,
        "quotation",
        issued_on=today,
        subject=data.subject or f"Quotation for {lines[0]['description']}",
        party=party,
        amount=total,
        payload={
            "lines": lines,
            "total": money(total),
            "valid_until": (today + timedelta(days=data.valid_days)).isoformat(),
            "notes": data.notes,
            "terms": data.terms if data.terms is not None else QUOTATION_TERMS,
        },
        order_id=order.id if order else None,
    )


def issue_order_confirmation(db, user, data) -> Letter:
    order = _order(db, data.order_id)
    if order.status == "cancelled":
        raise HTTPException(status_code=409, detail="This order is cancelled")
    customer = order.customer
    if not customer:
        raise HTTPException(
            status_code=422, detail="Add the customer to the order first"
        )
    lines, lines_total = _lines(_order_rows(order))
    total = order.final_amount if (order.final_amount or 0) > 0 else order.estimated_amount  # fmt: skip
    total = Decimal(total or lines_total)
    advance = Decimal(order.advance_paid or 0)
    payments = [
        {
            "paid_on": x.paid_on.isoformat(),
            "method": x.method,
            "reference": x.reference,
            "amount": money(x.amount),
        }
        for x in order.payments
    ]
    return _issue(
        db,
        user,
        "order_confirmation",
        issued_on=data.issued_on or date.today(),
        subject=f"Order {order.order_number}",
        party={
            "name": customer.name,
            "phone": customer.phone,
            "address": customer.address,
        },
        amount=advance,
        payload={
            "order_number": order.order_number,
            "expected_delivery": order.expected_delivery.isoformat() if order.expected_delivery else None,  # fmt: skip
            "lines": lines,
            "total": money(total),
            "advance": money(advance),
            "balance": money(max(total - advance, Decimal("0"))),
            "payments": payments,
            "notes": data.notes,
        },
        order_id=order.id,
    )


def issue_purchase_receipt(db, user, data) -> Letter:
    supplier = db.get(Supplier, data.supplier_id) if data.supplier_id else None
    if data.supplier_id and not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")
    item = db.get(Item, data.item_id) if data.item_id else None
    if data.item_id and not item:
        raise HTTPException(status_code=404, detail="Item not found")
    party = {
        "name": data.seller_name or (supplier.name if supplier else None),
        "phone": data.seller_phone or (supplier.phone if supplier else None),
        "address": data.seller_address or (supplier.address if supplier else None),
    }
    if not party["name"]:
        raise HTTPException(status_code=422, detail="Say who the piece was bought from")
    paid_on = data.paid_on or date.today()
    return _issue(
        db,
        user,
        "purchase_receipt",
        issued_on=paid_on,
        subject=f"Purchase of {data.description.strip()}",
        party=party,
        amount=data.amount,
        payload={
            "description": data.description.strip(),
            "quantity": data.quantity,
            "amount": money(data.amount),
            "item_sku": item.sku if item else None,
            "payment": {
                "paid_on": paid_on.isoformat(),
                "method": data.method,
                "reference": data.reference,
            },
            "notes": data.notes,
        },
        item_id=item.id if item else None,
        supplier_id=supplier.id if supplier else None,
    )


def issue_completion_certificate(db, user, data) -> Letter:
    project = db.get(RestorationProject, data.restoration_id)
    if not project:
        raise HTTPException(status_code=404, detail="Restoration not found")
    if project.kind != "temple":
        raise HTTPException(
            status_code=422, detail="A certificate is for a temple restoration"
        )
    if project.status != "restored":
        raise HTTPException(
            status_code=409,
            detail="Mark the restoration as restored before issuing its certificate",
        )
    lead = db.get(User, project.lead_artisan_id) if project.lead_artisan_id else None
    done = data.completed_on or date.today()
    return _issue(
        db,
        user,
        "completion_certificate",
        issued_on=date.today(),
        subject=f"Completion of {project.title}",
        party={
            "name": project.temple_name or project.title,
            "phone": None,
            "address": project.location,
        },  # fmt: skip
        amount=None,
        payload={
            "restoration": {
                "title": project.title,
                "piece": project.piece,
                "location": project.location,
                "condition_notes": project.condition_notes,
                "started_on": project.created_at.date().isoformat(),
                "lead_artisan": lead.username if lead else None,
            },
            "work_done": data.work_done,
            "completed_on": done.isoformat(),
            "notes": data.notes,
        },
        restoration_id=project.id,
    )
