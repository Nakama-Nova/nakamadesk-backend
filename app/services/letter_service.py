"""
Issuing official letters: the number (issued once, per financial year), the letterhead and
the snapshot each letter is drawn from. The functions here only build and add rows; the
caller commits (inside `with uow:`), so a number is used only if the letter is saved.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.iam import service as iam
from app.models.customer import Customer
from app.models.item import Item
from app.models.letter import KINDS, BusinessProfile, Letter
from app.models.order import Order
from app.models.restoration import RestorationProject
from app.models.supplier import Supplier
from app.models.user import User

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


def kinds_for(db, user) -> List[str]:
    """The kinds of letter this person may issue, and so see (letters:<kind>:issue)."""
    return [k for k in KINDS if iam.allowed(db, user, f"letters:{k}:issue")]


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
    existing: Optional[Letter] = None,
    **links,
) -> Letter:
    if existing is not None:
        # An edit: the number, the issue date and the letterhead it was printed on stay.
        if existing.status != "issued":
            raise HTTPException(
                status_code=409, detail="A void letter cannot be edited"
            )
        existing.subject = subject.strip()
        existing.party_name = party["name"]
        existing.amount = amount
        existing.payload = {
            "letterhead": existing.payload.get("letterhead") or get_profile(db),
            "party": party,
            **payload,
        }
        for field, value in links.items():
            setattr(existing, field, value)
        db.flush()
        return existing
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


def issue_quotation(db, user, data, existing=None) -> Letter:
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
        existing=existing,
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


def issue_order_confirmation(db, user, data, existing=None) -> Letter:
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
        existing=existing,
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


def issue_purchase_receipt(db, user, data, existing=None) -> Letter:
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
        existing=existing,
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


def issue_completion_certificate(db, user, data, existing=None) -> Letter:
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
        existing=existing,
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


# ------------------------------------------------------------- HR letters
OFFER_TERMS = [
    "This offer is subject to a satisfactory probation period.",
    "Pay is given after taking back any advance, as recorded at each pay-out.",
    "Either side may end the employment by giving the notice period above.",
]
WEEKDAY_NAMES = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]
PAY_UNIT = {
    "daily": "per day",
    "weekly": "per week",
    "monthly": "per month",
    "hourly": "per hour",
}


def _staff(db: Session, user_id) -> User:
    person = db.get(User, user_id)
    if not person:
        raise HTTPException(status_code=404, detail="Staff member not found")
    return person


def _employee(db: Session, person: User, designation: Optional[str]) -> Dict:
    from app.services import hr_service

    return {
        "name": person.full_name or person.username,
        "phone": person.phone,
        "address": person.address,
        "staff_code": hr_service.assign_staff_code(db, person),
        "designation": (
            designation or str(person.role).replace("_", " ").title()
        ).strip(),
    }


def _party(emp: Dict) -> Dict:
    return {"name": emp["name"], "phone": emp["phone"], "address": emp["address"]}


def issue_offer_letter(db, user, data, existing=None) -> Letter:
    from app.models.hr import LeaveType, Shift

    person = _staff(db, data.user_id)
    pay_type = data.pay_type or person.pay_type or "daily"
    amount = data.pay_amount
    if amount is None:
        amount = (
            person.pay_amount
            if person.pay_amount is not None
            else person.base_daily_wage
        )
    if not amount:
        raise HTTPException(
            status_code=422, detail="Set what this person is paid first"
        )
    joining = data.joining_date or person.joining_date
    if not joining:
        raise HTTPException(status_code=422, detail="Give the joining date")
    shift = db.get(Shift, person.shift_id) if person.shift_id else None
    leave = [
        {
            "name": t.name,
            "paid": t.paid,
            "yearly_days": money(t.yearly_days) if t.yearly_days is not None else None,
        }
        for t in db.query(LeaveType)
        .filter(LeaveType.active.is_(True))
        .order_by(LeaveType.name)
    ]
    emp = _employee(db, person, data.designation)
    return _issue(
        db,
        user,
        "offer_letter",
        existing=existing,
        issued_on=data.issued_on or date.today(),
        subject=f"Offer of employment: {emp['designation']}",
        party=_party(emp),
        amount=Decimal(str(amount)),
        payload={
            "employee": emp,
            "joining_date": joining.isoformat(),
            "pay_type": pay_type,
            "pay_amount": money(amount),
            "pay_unit": PAY_UNIT[pay_type],
            "employment": person.employment or "full_time",
            "probation_months": data.probation_months,
            "notice_days": data.notice_days,
            "shift": shift
            and {
                "name": shift.name,
                "start_time": shift.start_time.strftime("%H:%M"),
                "end_time": shift.end_time.strftime("%H:%M"),
                "weekly_off": [WEEKDAY_NAMES[d] for d in (shift.weekly_off or [])],
            },
            "leave": leave,
            "notes": data.notes,
            "terms": data.terms if data.terms is not None else OFFER_TERMS,
        },
    )


def issue_payslip(db, user, data, existing=None) -> Letter:
    from app.models.attendance import Attendance
    from app.models.daily_wage import DailyWage
    from app.models.payroll import PayrollPayment, WorkEntry

    pay = db.get(PayrollPayment, data.payment_id)
    if not pay:
        raise HTTPException(status_code=404, detail="Pay-out not found")
    if existing is None:  # one payslip a pay-out: asking again returns it
        have = (
            db.query(Letter)
            .filter(
                Letter.kind == "payslip",
                Letter.status == "issued",
                Letter.payload["payment"]["id"].astext == str(pay.id),
            )
            .first()
        )
        if have:
            return have
    person = _staff(db, pay.user_id)
    before = (
        db.query(func.max(PayrollPayment.through))
        .filter(
            PayrollPayment.user_id == person.id, PayrollPayment.through < pay.through
        )
        .scalar()
    )
    period_from = (
        before + timedelta(days=1)
        if before
        else (person.joining_date or pay.through.replace(day=1))
    )
    counts = dict(
        db.query(Attendance.status, func.count())
        .filter(
            Attendance.user_id == person.id,
            Attendance.date >= period_from,
            Attendance.date <= pay.through,
        )
        .group_by(Attendance.status)
        .all()
    )
    entries = (
        db.query(WorkEntry)
        .filter(WorkEntry.payment_id == pay.id)
        .order_by(WorkEntry.work_date)
        .all()
    )
    wage_days = (
        db.query(func.count(DailyWage.id))
        .filter(DailyWage.payment_id == pay.id)
        .scalar()
    )
    earnings = [
        {
            "label": "Wages",
            "detail": f"{person.pay_type or 'daily'} pay",
            "amount": money(pay.gross_daily),
        }
    ]
    earnings += [
        {
            "label": e.description,
            "detail": f"{e.kind}, {e.work_date.isoformat()}",
            "amount": money(e.amount),
        }
        for e in entries
    ]
    deductions = (
        [{"label": "Advance taken back", "amount": money(pay.deductions)}]
        if pay.deductions
        else []
    )
    emp = _employee(db, person, None)
    gross = Decimal(pay.gross_daily) + Decimal(pay.gross_extras)
    return _issue(
        db,
        user,
        "payslip",
        existing=existing,
        issued_on=pay.paid_on,
        subject=(
            f"Payslip for {period_from.strftime('%d %b')} to "
            f"{pay.through.strftime('%d %b %Y')}"
        ),
        party=_party(emp),
        amount=Decimal(pay.net_paid),
        payload={
            "employee": {
                **emp,
                "pay_type": person.pay_type or "daily",
                "employment": person.employment,
                "pay_upi": person.pay_upi,
            },
            "payment": {
                "id": str(pay.id),
                "paid_on": pay.paid_on.isoformat(),
                "method": pay.method,
                "reference": pay.reference,
            },
            "period": {"from": period_from.isoformat(), "to": pay.through.isoformat()},
            "attendance": {k: int(v) for k, v in counts.items()}
            | {"paid_wage_days": int(wage_days or 0)},
            "earnings": earnings,
            "gross": money(gross),
            "deductions": deductions,
            "net": money(pay.net_paid),
        },
    )


def issue_experience_letter(db, user, data, existing=None) -> Letter:
    person = _staff(db, data.user_id)
    if not person.last_working_date:
        raise HTTPException(
            status_code=409,
            detail="Offboard this person first (record the last working day)",
        )
    if not person.joining_date:
        raise HTTPException(status_code=422, detail="Set the joining date first")
    emp = _employee(db, person, data.designation)
    return _issue(
        db,
        user,
        "experience_letter",
        existing=existing,
        issued_on=data.issued_on or date.today(),
        subject=f"Experience and relieving letter: {emp['name']}",
        party=_party(emp),
        amount=None,
        payload={
            "employee": emp,
            "joining_date": person.joining_date.isoformat(),
            "last_working_date": person.last_working_date.isoformat(),
            "remarks": data.remarks,
            "notes": data.notes,
        },
    )


ISSUERS = {
    "offer_letter": issue_offer_letter,
    "payslip": issue_payslip,
    "experience_letter": issue_experience_letter,
    "quotation": issue_quotation,
    "order_confirmation": issue_order_confirmation,
    "purchase_receipt": issue_purchase_receipt,
    "completion_certificate": issue_completion_certificate,
}


def edit_letter(db: Session, user: User, letter: Letter, data) -> Letter:
    """
    Change an issued letter by sending it again with the new details (the same body as
    when it was issued). The number, the issue date and the letterhead stay; the content
    is rebuilt, so an order confirmation picks up the order as it is now.
    """
    if getattr(data, "issued_on", None) is None and hasattr(data, "issued_on"):
        data.issued_on = letter.issued_on  # a quotation's validity counts from then
    return ISSUERS[letter.kind](db, user, data, existing=letter)
