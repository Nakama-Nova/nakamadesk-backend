"""
Writes the ledger from the app's own records, without touching them
(docs/FINANCE_LEDGER_DESIGN.md, section 2). Safe to run any time and as often as you like:
each record is posted once per version of that record.

  * unchanged record  -> nothing happens
  * new record        -> one entry is posted
  * changed record    -> its old entry is reversed and the new version posted
  * record that should no longer count (a cancelled sale) -> its entry is reversed

Covered: counter sales, billed orders and customer payments on orders; confirmed
purchases and what is paid for them; expenses; pay-outs, old-style wage payments and
staff advances; machine purchases and repairs.

Rules (section 4 of the plan), all dated the day the record says:
  Counter sale, paid       Dr cash or bank/UPI            Cr Sales (and GST payable)
  Counter sale, not paid   Dr customers owe us            Cr Sales
  Order advance received   Dr cash or bank/UPI            Cr customer advances
  Order billed             Dr customer advances (what was received before the bill)
                           Dr customers owe us (the rest)  Cr Sales
  Payment after billing    Dr cash or bank/UPI            Cr customers owe us
  Purchase confirmed       Dr raw material bought         Cr we owe suppliers
  Purchase marked paid     Dr we owe suppliers            Cr unmatched payments
  Expense (not voided)     Dr the category's account      Cr cash or bank/UPI
  Pay-out to staff         Dr wages (gross)               Cr cash or bank/UPI (net paid)
                                                             and staff advances (taken back)
  Old-style wage payment   Dr wages                       Cr cash
  Staff advance given      Dr staff advances              Cr cash
  Advance paid back        Dr cash                        Cr staff advances
  Machine bought           Dr machines (at cost)          Cr unmatched payments
  Machine repair/service   Dr repairs and maintenance     Cr unmatched payments
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import Date, cast, text
from sqlalchemy.orm import Session

from app.finance import service
from app.finance.models import FinanceEntry
from app.finance.schemas import ReverseIn
from app.models.daily_wage import DailyWage
from app.models.expense import Expense
from app.models.machine import Machine, MachineMaintenance
from app.models.order import Order
from app.models.order_payment import OrderPayment
from app.models.payroll import PayrollPayment, WorkerAdvance
from app.models.purchase import Purchase
from app.models.sale import Sale

ZERO = Decimal("0.00")
CASH, BANK, CLEARING = "1000", "1010", "1090"
OWED_BY_CUSTOMERS, ADVANCES, SALES, GST = "1100", "2100", "4000", "2300"
STAFF_ADVANCES, MACHINES, OWED_TO_SUPPLIERS = "1300", "1400", "2000"
PURCHASES, WAGES, REPAIRS = "5000", "5100", "5200"
OTHER_COSTS, UNSORTED = "5400", "5900"
LOCK_KEY = 7420011  # keeps two runs from posting the same record at once

BANK_METHODS = {"upi", "card", "bank", "cheque", "online", "netbanking", "neft", "imps"}


@dataclass
class Plan:
    source_type: str
    source_id: str
    entry_date: date
    memo: str
    lines: List[dict]
    fingerprint: str
    exceptions: List[str] = field(default_factory=list)


@dataclass
class Problem:
    source_type: str
    source_id: str
    code: str
    detail: str


@dataclass
class RunResult:
    posted: int = 0
    reversed: int = 0
    unchanged: int = 0
    nothing_to_post: int = 0
    problems: List[Problem] = field(default_factory=list)
    dry_run: bool = False


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _money(value) -> Decimal:
    return Decimal(value or 0).quantize(ZERO)


def _fingerprint(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, default=str).encode()).hexdigest()[:32]


def method_account(method: Optional[str]) -> Tuple[str, Optional[str]]:
    """The account money paid by this method lands in, and a problem code if unclear."""
    m = (method or "").strip().lower()
    if m == "cash":
        return CASH, None
    if m in BANK_METHODS:
        return BANK, None
    # unknown or missing: held in clearing until a person says where it went
    return CLEARING, "payment_method_unclear"


# --------------------------------------------------------------------------- plans
def plan_sale(sale: Sale, order: Optional[Order], deposits: Decimal) -> Optional[Plan]:
    """The entry a sale should have, or None when it should not be in the books."""
    if (sale.order_status or "completed") == "cancelled":
        return None
    total = _money(sale.total_amount)
    tax = _money(sale.tax_total)
    if total <= 0:
        return None
    net = total - tax
    when = _utc_naive(sale.created_at).date()
    exceptions: List[str] = []
    customer = str(sale.customer_id) if sale.customer_id else None

    debits: List[dict] = []
    if sale.order_type == "order":
        if order is None:
            exceptions.append("order_missing")
        use = min(deposits, total) if order is not None else ZERO
        if use > 0:
            debits.append(
                {
                    "account": ADVANCES,
                    "debit": use,
                    "party_type": "customer",
                    "party_id": customer,
                }
            )
        if total - use > 0:
            debits.append(
                {
                    "account": OWED_BY_CUSTOMERS,
                    "debit": total - use,
                    "party_type": "customer",
                    "party_id": customer,
                }
            )
    else:
        status = sale.payment_status or "pending"
        if status == "paid":
            account, problem = method_account(sale.payment_method)
            if problem:
                exceptions.append(problem)
            debits.append({"account": account, "debit": total})
        else:
            if status == "partial":
                exceptions.append("partial_sale_amount_unknown")
            debits.append(
                {
                    "account": OWED_BY_CUSTOMERS,
                    "debit": total,
                    "party_type": "customer",
                    "party_id": customer,
                }
            )
    credits: List[dict] = []
    if net > 0:
        credits.append({"account": SALES, "credit": net})
    if tax > 0:
        credits.append({"account": GST, "credit": tax})

    lines = debits + credits
    for ln in lines:
        ln["location_id"] = sale.location_id
    return Plan(
        source_type="sale",
        source_id=str(sale.id),
        entry_date=when,
        memo=f"Sale {sale.invoice_number or sale.id}",
        lines=lines,
        fingerprint=_fingerprint(
            str(sale.id),
            total,
            tax,
            sale.order_status,
            sale.payment_status,
            sale.payment_method,
            str(sale.created_at),
            str(sale.location_id),
            sale.order_type,
            str(order.id) if order else None,
            [d["debit"] for d in debits],
            [x["account"] for x in lines],
        ),
        exceptions=exceptions,
    )


def plan_order_payment(
    payment: OrderPayment, order: Order, sale: Optional[Sale]
) -> Optional[Plan]:
    amount = _money(payment.amount)
    if amount <= 0 or payment.voided_at is not None:
        return None
    account, problem = method_account(payment.method)
    # Money received before the order was billed is held for it (a customer advance);
    # money received after is the customer paying what they owe on the bill.
    before_bill = sale is None or _utc_naive(payment.created_at) <= _utc_naive(
        sale.created_at
    )
    other = ADVANCES if before_bill else OWED_BY_CUSTOMERS
    customer = str(order.customer_id) if order.customer_id else None
    return Plan(
        source_type="order_payment",
        source_id=str(payment.id),
        entry_date=payment.paid_on,
        memo=(
            f"{'Advance' if before_bill else 'Payment'} on order {order.order_number}"
            + (f" ({payment.reference})" if payment.reference else "")
        ),
        lines=[
            {"account": account, "debit": amount},
            {
                "account": other,
                "credit": amount,
                "party_type": "customer",
                "party_id": customer,
            },
        ],
        fingerprint=_fingerprint(
            str(payment.id),
            amount,
            payment.method,
            str(payment.paid_on),
            str(order.id),
            before_bill,
        ),
        exceptions=[problem] if problem else [],
    )


def _day(value) -> date:
    return _utc_naive(value).date() if isinstance(value, datetime) else value


def plan_purchase(purchase: Purchase) -> Optional[Plan]:
    total = _money(purchase.total_amount)
    if purchase.status != "confirmed" or total <= 0:
        return None
    supplier = str(purchase.supplier_id) if purchase.supplier_id else None
    when = _day(purchase.purchase_date or purchase.created_at)
    return Plan(
        source_type="purchase",
        source_id=str(purchase.id),
        entry_date=when,
        memo=f"Purchase {purchase.invoice_number or purchase.id}",
        lines=[
            {"account": PURCHASES, "debit": total},
            {
                "account": OWED_TO_SUPPLIERS,
                "credit": total,
                "party_type": "supplier",
                "party_id": supplier,
            },
        ],
        fingerprint=_fingerprint(str(purchase.id), total, str(when), supplier),
    )


def plan_purchase_payment(purchase: Purchase) -> Optional[Plan]:
    """The app records no payment date or method for suppliers, so a paid purchase is
    settled on the purchase date out of unmatched payments, and flagged."""
    total = _money(purchase.total_amount)
    if purchase.status != "confirmed" or total <= 0:
        return None
    status = purchase.payment_status or "pending"
    if status not in ("paid", "partial"):
        return None
    when = _day(purchase.purchase_date or purchase.created_at)
    memo = f"Paid for purchase {purchase.invoice_number or purchase.id}"
    if status == "partial":  # how much was paid is not recorded
        return Plan(
            "purchase_payment",
            str(purchase.id),
            when,
            memo,
            [],
            "",
            ["partial_purchase_amount_unknown"],
        )
    supplier = str(purchase.supplier_id) if purchase.supplier_id else None
    return Plan(
        source_type="purchase_payment",
        source_id=str(purchase.id),
        entry_date=when,
        memo=memo,
        lines=[
            {
                "account": OWED_TO_SUPPLIERS,
                "debit": total,
                "party_type": "supplier",
                "party_id": supplier,
            },
            {"account": CLEARING, "credit": total},
        ],
        fingerprint=_fingerprint(str(purchase.id), total, str(when), supplier, "paid"),
        exceptions=["supplier_payment_unrecorded"],
    )


def expense_account(category: Optional[str]) -> str:
    c = (category or "").strip().lower()
    if any(k in c for k in ("repair", "maint", "servic")):
        return REPAIRS
    if c in ("", "other", "others", "misc", "miscellaneous"):
        return UNSORTED
    return OTHER_COSTS


def plan_expense(expense: Expense) -> Optional[Plan]:
    amount = _money(expense.amount)
    if expense.voided_at is not None or amount <= 0:
        return None
    account, problem = method_account(expense.method)
    return Plan(
        source_type="expense",
        source_id=str(expense.id),
        entry_date=expense.spent_on,
        memo=f"{expense.category}" + (f" - {expense.payee}" if expense.payee else ""),
        lines=[
            {"account": expense_account(expense.category), "debit": amount},
            {"account": account, "credit": amount},
        ],
        fingerprint=_fingerprint(
            str(expense.id),
            amount,
            str(expense.spent_on),
            expense.category,
            expense.method,
        ),
        exceptions=[problem] if problem else [],
    )


def plan_payout(pay: PayrollPayment) -> Optional[Plan]:
    gross = _money(pay.gross_daily) + _money(pay.gross_extras)
    net = _money(pay.net_paid)
    taken_back = gross - net
    if gross <= 0:
        return None
    account, problem = method_account(pay.method)
    staff = str(pay.user_id)
    exceptions = [problem] if problem else []
    lines = [
        {"account": WAGES, "debit": gross, "party_type": "staff", "party_id": staff}
    ]
    if net > 0:
        lines.append({"account": account, "credit": net})
    if taken_back > 0:
        lines.append(
            {
                "account": STAFF_ADVANCES,
                "credit": taken_back,
                "party_type": "staff",
                "party_id": staff,
            }
        )
    if taken_back < 0:  # paid more than was earned: a person must look
        lines = []
        exceptions.append("payout_does_not_add_up")
    return Plan(
        source_type="payroll_payment",
        source_id=str(pay.id),
        entry_date=pay.paid_on,
        memo=f"Pay-out to staff {staff[:8]}",
        lines=lines,
        fingerprint=_fingerprint(
            str(pay.id), gross, net, str(pay.paid_on), pay.method, staff
        ),
        exceptions=exceptions,
    )


def plan_legacy_wage(wage: DailyWage) -> Optional[Plan]:
    """A wage paid on its own (not through a pay-out), always in cash."""
    amount = _money(wage.amount)
    if (
        wage.payment_status != "paid"
        or wage.payment_id is not None
        or wage.paid_at is None
        or amount <= 0
    ):
        return None
    when = _day(wage.paid_at)
    return Plan(
        source_type="daily_wage",
        source_id=str(wage.id),
        entry_date=when,
        memo="Daily wage paid",
        lines=[
            {"account": WAGES, "debit": amount},
            {"account": CASH, "credit": amount},
        ],
        fingerprint=_fingerprint(str(wage.id), amount, str(when)),
    )


def plan_staff_advance(adv: WorkerAdvance) -> Optional[Plan]:
    """Cash given to staff, or paid back on its own. Money taken back inside a pay-out
    is already part of that pay-out."""
    amount = abs(_money(adv.amount))
    if amount <= 0 or (adv.kind == "recovery" and adv.payment_id is not None):
        return None
    staff = {"party_type": "staff", "party_id": str(adv.user_id)}
    if adv.kind == "advance":
        lines = [
            {"account": STAFF_ADVANCES, "debit": amount, **staff},
            {"account": CASH, "credit": amount},
        ]
        memo = f"Advance given to staff {str(adv.user_id)[:8]}"
    else:
        lines = [
            {"account": CASH, "debit": amount},
            {"account": STAFF_ADVANCES, "credit": amount, **staff},
        ]
        memo = f"Advance paid back by staff {str(adv.user_id)[:8]}"
    return Plan(
        source_type="staff_advance",
        source_id=str(adv.id),
        entry_date=adv.given_on,
        memo=memo,
        lines=lines,
        fingerprint=_fingerprint(
            str(adv.id), amount, adv.kind, str(adv.given_on), str(adv.payment_id)
        ),
    )


def plan_machine(machine: Machine) -> Optional[Plan]:
    cost = _money(machine.purchase_cost)
    if cost <= 0:
        return None
    memo = f"Machine {machine.tag} bought"
    if machine.purchase_date is None:  # cannot be dated, so only reported
        return Plan(
            "machine",
            str(machine.id),
            date.today(),
            memo,
            [],
            "",
            ["machine_date_missing"],
        )
    place = machine.location_id
    return Plan(
        source_type="machine",
        source_id=str(machine.id),
        entry_date=machine.purchase_date,
        memo=memo,
        lines=[
            {"account": MACHINES, "debit": cost, "location_id": place},
            {"account": CLEARING, "credit": cost, "location_id": place},
        ],
        fingerprint=_fingerprint(
            str(machine.id), cost, str(machine.purchase_date), str(place)
        ),
        exceptions=["machine_payment_unrecorded"],
    )


def plan_maintenance(
    job: MachineMaintenance, machine: Optional[Machine]
) -> Optional[Plan]:
    cost = _money(job.cost)
    if cost <= 0:
        return None
    place = machine.location_id if machine else None
    return Plan(
        source_type="machine_maintenance",
        source_id=str(job.id),
        entry_date=job.performed_on,
        memo=f"{job.kind.title()} on machine {machine.tag if machine else job.machine_id}",
        lines=[
            {"account": REPAIRS, "debit": cost, "location_id": place},
            {"account": CLEARING, "credit": cost, "location_id": place},
        ],
        fingerprint=_fingerprint(str(job.id), cost, str(job.performed_on), str(place)),
        exceptions=["machine_payment_unrecorded"],
    )


# ------------------------------------------------------------------------ the run
def _live_postings(
    session: Session, source_type: str, ids: List[str]
) -> Dict[str, FinanceEntry]:
    """Each source's current posting: the latest one that has not been reversed."""
    if not ids:
        return {}
    reversed_ids = session.query(FinanceEntry.reverses_id).filter(
        FinanceEntry.reverses_id.isnot(None)
    )
    rows = (
        session.query(FinanceEntry)
        .filter(
            FinanceEntry.kind == "posting",
            FinanceEntry.source_type == source_type,
            FinanceEntry.source_id.in_(ids),
            FinanceEntry.id.notin_(reversed_ids),
        )
        .order_by(FinanceEntry.created_at)
        .all()
    )
    return {r.source_id: r for r in rows}


def _reverse_live(
    session: Session, live: FinanceEntry, why: str, user_id: Optional[UUID]
):
    """Reverse on the original date when that month is open, else today."""
    for when in (live.entry_date, None):
        try:
            service.reverse(
                session, live.id, ReverseIn(reason=why, entry_date=when), user_id
            )
            return
        except HTTPException as e:
            if e.status_code != 400 or when is None:
                raise


def _apply(
    session: Session,
    source_type: str,
    plans: Dict[str, Optional[Plan]],
    result: RunResult,
    user_id: Optional[UUID],
    dry_run: bool,
):
    live = _live_postings(session, source_type, list(plans))
    for source_id, plan in plans.items():
        current = live.get(source_id)
        if plan is not None and not plan.lines:  # nothing to post, only to report
            for code in plan.exceptions:
                result.problems.append(Problem(source_type, source_id, code, plan.memo))
            plan = None
        if plan is None:
            if current is None:
                result.nothing_to_post += 1
                continue
            result.reversed += 1
            if not dry_run:
                _reverse_live(session, current, "no longer counts", user_id)
            continue
        for code in plan.exceptions:
            result.problems.append(Problem(source_type, source_id, code, plan.memo))
        if current is not None and current.source_fingerprint == plan.fingerprint:
            result.unchanged += 1
            continue
        if dry_run:
            result.posted += 1
            result.reversed += 1 if current is not None else 0
            continue
        try:
            if current is not None:
                _reverse_live(session, current, "record changed", user_id)
                result.reversed += 1
            service.post_entry(
                session,
                entry_date=plan.entry_date,
                memo=plan.memo,
                lines=plan.lines,
                kind="posting",
                created_by=user_id,
                source_type=plan.source_type,
                source_id=plan.source_id,
                source_fingerprint=plan.fingerprint,
            )
            result.posted += 1
        except HTTPException as e:
            session.rollback()
            if e.status_code == 409:  # another run got there first
                result.unchanged += 1
                continue
            result.problems.append(
                Problem(source_type, source_id, "cannot_post", str(e.detail))
            )


def project(
    session: Session,
    user_id: Optional[UUID] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    dry_run: bool = False,
) -> RunResult:
    """Post (or, with dry_run, just count) everything in the window. No window = all."""
    result = RunResult(dry_run=dry_run)
    # The lock lives on its own connection: the session hands its connection back to
    # the pool on every commit or rollback, so unlocking through it could reach a
    # different connection and leave this lock held for good.
    with session.get_bind().connect() as lock_conn:
        lock_conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": LOCK_KEY})
        try:
            _sales(session, result, user_id, date_from, date_to, dry_run)
            _order_payments(session, result, user_id, date_from, date_to, dry_run)
            _costs(session, result, user_id, date_from, date_to, dry_run)
        finally:
            session.rollback()
            lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
    return result


def _window(query, column, date_from, date_to):
    day = cast(column, Date)
    if date_from:
        query = query.filter(day >= date_from)
    if date_to:
        query = query.filter(day <= date_to)
    return query


def _sales(session, result, user_id, date_from, date_to, dry_run):
    sales = _window(session.query(Sale), Sale.created_at, date_from, date_to).all()
    order_sales = [s.id for s in sales if s.order_type == "order"]
    orders = {
        o.sale_id: o
        for o in session.query(Order).filter(Order.sale_id.in_(order_sales or [None]))
    }
    plans: Dict[str, Optional[Plan]] = {}
    for sale in sales:
        order = orders.get(sale.id)
        deposits = ZERO
        if order is not None:
            deposits = sum(
                (
                    _money(p.amount)
                    for p in session.query(OrderPayment).filter(
                        OrderPayment.order_id == order.id,
                        OrderPayment.voided_at.is_(None),
                    )
                    if _utc_naive(p.created_at) <= _utc_naive(sale.created_at)
                ),
                ZERO,
            )
        plans[str(sale.id)] = plan_sale(sale, order, deposits)
    _apply(session, "sale", plans, result, user_id, dry_run)


def _order_payments(session, result, user_id, date_from, date_to, dry_run):
    q = session.query(OrderPayment)
    if date_from:
        q = q.filter(OrderPayment.paid_on >= date_from)
    if date_to:
        q = q.filter(OrderPayment.paid_on <= date_to)
    payments = q.all()
    orders = {
        o.id: o
        for o in session.query(Order).filter(
            Order.id.in_({p.order_id for p in payments} or [None])
        )
    }
    sale_ids = {o.sale_id for o in orders.values() if o.sale_id}
    sales = {
        s.id: s for s in session.query(Sale).filter(Sale.id.in_(sale_ids or [None]))
    }
    plans: Dict[str, Optional[Plan]] = {}
    for payment in payments:
        order = orders[payment.order_id]
        plans[str(payment.id)] = plan_order_payment(
            payment, order, sales.get(order.sale_id)
        )
    _apply(session, "order_payment", plans, result, user_id, dry_run)


def _costs(session, result, user_id, date_from, date_to, dry_run):
    def run(source_type, rows, plan):
        plans = {str(r.id): plan(r) for r in rows}
        _apply(session, source_type, plans, result, user_id, dry_run)

    def rows(model, column):
        return _window(session.query(model), column, date_from, date_to).all()

    purchases = rows(Purchase, Purchase.purchase_date)
    run("purchase", purchases, plan_purchase)
    run("purchase_payment", purchases, plan_purchase_payment)
    run("expense", rows(Expense, Expense.spent_on), plan_expense)
    run("payroll_payment", rows(PayrollPayment, PayrollPayment.paid_on), plan_payout)
    # ponytail: a wage flipped back to unpaid with its paid_at cleared drops out of the
    # window and is not reversed; wages are never un-paid in the app today
    wages = _window(
        session.query(DailyWage).filter(DailyWage.payment_id.is_(None)),
        DailyWage.paid_at,
        date_from,
        date_to,
    ).all()
    run("daily_wage", wages, plan_legacy_wage)
    run(
        "staff_advance", rows(WorkerAdvance, WorkerAdvance.given_on), plan_staff_advance
    )
    run("machine", rows(Machine, Machine.purchase_date), plan_machine)

    jobs = rows(MachineMaintenance, MachineMaintenance.performed_on)
    machines = {
        m.id: m
        for m in session.query(Machine).filter(
            Machine.id.in_({j.machine_id for j in jobs} or [None])
        )
    }
    plans = {str(j.id): plan_maintenance(j, machines.get(j.machine_id)) for j in jobs}
    _apply(session, "machine_maintenance", plans, result, user_id, dry_run)
