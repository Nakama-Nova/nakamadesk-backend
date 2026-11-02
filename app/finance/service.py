"""
The ledger's operations. The database enforces the hard rules (balanced, never edited,
closed months); this layer checks the same things first so the person gets a clear
message instead of a database error, and does the sums for the reports.

Money is Decimal with two places. A balance is shown in the account's normal direction:
positive when it is on the side that makes that kind of account grow.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.finance import chart
from app.finance.models import (
    FinanceAccount,
    FinanceEntry,
    FinanceLine,
    FinanceSettings,
)
from app.finance.schemas import (
    AccountIn,
    AccountUpdate,
    Closing,
    ClosingIn,
    EntryOut,
    JournalIn,
    Ledger,
    LedgerLine,
    LineOut,
    ReverseIn,
    TrialBalance,
    TrialBalanceRow,
)
from app.services import audit

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def _today() -> date:
    return date.today()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _refused(e: Exception) -> HTTPException:
    """The database said no (a backstop: the checks above should catch it first)."""
    text = str(getattr(e, "orig", e))
    detail = text.splitlines()[0] if text else "The ledger refused this entry"
    return HTTPException(status_code=409, detail=detail)


# --------------------------------------------------------------------------- chart
def ensure_chart(session: Session) -> None:
    """Add any starting account that is missing. Idempotent."""
    have = {c for (c,) in session.query(FinanceAccount.code)}
    for code, name, type_, side in chart.CHART:
        if code not in have:
            session.add(
                FinanceAccount(code=code, name=name, type=type_, normal_side=side)
            )
    session.commit()


def list_accounts(session: Session, include_inactive: bool) -> List[FinanceAccount]:
    q = session.query(FinanceAccount)
    if not include_inactive:
        q = q.filter(FinanceAccount.is_active.is_(True))
    return q.order_by(FinanceAccount.code).all()


def _account(session: Session, code: str) -> FinanceAccount:
    account = session.query(FinanceAccount).filter(FinanceAccount.code == code).first()
    if account is None:
        raise HTTPException(status_code=404, detail=f"No account {code}")
    return account


def create_account(session: Session, data: AccountIn, user_id: UUID) -> FinanceAccount:
    side = data.normal_side or chart.NORMAL_SIDE[data.type]
    account = FinanceAccount(
        code=data.code, name=data.name.strip(), type=data.type, normal_side=side
    )
    session.add(account)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=409, detail=f"Account {data.code} already exists"
        ) from None
    audit.record(
        session,
        user_id,
        "finance_accounts",
        account.id,
        "INSERT",
        new={"code": account.code, "name": account.name, "type": account.type},
    )
    session.commit()
    session.refresh(account)
    return account


def update_account(
    session: Session, code: str, data: AccountUpdate, user_id: UUID
) -> FinanceAccount:
    account = _account(session, code)
    old = {"name": account.name, "is_active": account.is_active}
    if data.name is not None:
        account.name = data.name.strip()
    if data.is_active is not None:
        account.is_active = data.is_active
    audit.record(
        session,
        user_id,
        "finance_accounts",
        account.id,
        "UPDATE",
        old=old,
        new={"name": account.name, "is_active": account.is_active},
    )
    session.commit()
    session.refresh(account)
    return account


# ------------------------------------------------------------------------- closing
def _settings(session: Session) -> FinanceSettings:
    row = session.get(FinanceSettings, 1)
    if row is None:  # a fresh database that never ran the migration's insert
        row = FinanceSettings(id=1)
        session.add(row)
        session.flush()
    return row


def closed_through(session: Session) -> Optional[date]:
    row = session.get(FinanceSettings, 1)
    return row.closed_through if row else None


def get_closing(session: Session) -> Closing:
    row = _settings(session)
    session.commit()
    return Closing(
        closed_through=row.closed_through,
        updated_by=row.updated_by,
        updated_at=row.updated_at,
    )


def set_closing(session: Session, data: ClosingIn, user_id: UUID) -> Closing:
    if data.closed_through and data.closed_through > _today():
        raise HTTPException(status_code=400, detail="Cannot close a date in the future")
    row = _settings(session)
    old = row.closed_through
    row.closed_through = data.closed_through
    row.updated_by = user_id
    audit.record(
        session,
        user_id,
        "finance_settings",
        1,
        "UPDATE",
        old={"closed_through": old},
        new={"closed_through": data.closed_through, "reason": data.reason},
    )
    session.commit()
    session.refresh(row)
    return Closing(
        closed_through=row.closed_through,
        updated_by=row.updated_by,
        updated_at=row.updated_at,
    )


def _check_open(session: Session, entry_date: date) -> None:
    closed = closed_through(session)
    if closed is not None and entry_date <= closed:
        raise HTTPException(
            status_code=400, detail=f"The books are closed up to {closed}"
        )


# --------------------------------------------------------------------------- entries
def post_entry(
    session: Session,
    *,
    entry_date: date,
    memo: str,
    lines: list,
    kind: str,
    created_by: Optional[UUID],
    reverses_id: Optional[UUID] = None,
    source_type: Optional[str] = None,
    source_id: Optional[str] = None,
    source_fingerprint: Optional[str] = None,
) -> FinanceEntry:
    """
    Write one balanced entry. `lines` are dicts: account (code), debit, credit and
    optionally location_id, party_type, party_id, note. Commits.
    """
    if entry_date > _today():
        raise HTTPException(
            status_code=400, detail="An entry cannot be dated in the future"
        )
    _check_open(session, entry_date)

    codes = {ln["account"] for ln in lines}
    accounts = {
        a.code: a
        for a in session.query(FinanceAccount).filter(FinanceAccount.code.in_(codes))
    }
    debit_total = credit_total = ZERO
    prepared = []
    for ln in lines:
        account = accounts.get(ln["account"])
        if account is None:
            raise HTTPException(status_code=400, detail=f"No account {ln['account']}")
        if not account.is_active:
            raise HTTPException(
                status_code=400,
                detail=f"Account {account.code} ({account.name}) is switched off",
            )
        debit = Decimal(ln.get("debit") or 0)
        credit = Decimal(ln.get("credit") or 0)
        if debit.quantize(CENT) != debit or credit.quantize(CENT) != credit:
            raise HTTPException(
                status_code=400, detail="Amounts have at most two decimals"
            )
        debit_total += debit
        credit_total += credit
        prepared.append(
            FinanceLine(
                account_id=account.id,
                debit=debit,
                credit=credit,
                location_id=ln.get("location_id"),
                party_type=ln.get("party_type"),
                party_id=ln.get("party_id"),
                note=ln.get("note"),
            )
        )
    if debit_total != credit_total:
        raise HTTPException(
            status_code=400,
            detail=f"The entry does not balance: debits {debit_total}, credits {credit_total}",
        )

    entry = FinanceEntry(
        entry_date=entry_date,
        memo=memo.strip(),
        kind=kind,
        reverses_id=reverses_id,
        source_type=source_type,
        source_id=source_id,
        source_fingerprint=source_fingerprint,
        created_by=created_by,
    )
    session.add(entry)
    session.flush()
    for line in prepared:
        line.entry_id = entry.id
        session.add(line)
    try:
        session.commit()  # the database checks the balance here, once all lines are in
    except (IntegrityError, DBAPIError) as e:
        session.rollback()
        raise _refused(e) from None
    session.refresh(entry)
    return entry


def post_journal(session: Session, data: JournalIn, user_id: UUID) -> EntryOut:
    entry = post_entry(
        session,
        entry_date=data.entry_date,
        memo=data.memo,
        kind="manual",
        created_by=user_id,
        lines=[
            {
                "account": ln.account,
                "debit": ln.debit,
                "credit": ln.credit,
                "location_id": ln.location_id,
                "note": ln.note,
            }
            for ln in data.lines
        ],
    )
    return to_out(session, entry)


def reverse(
    session: Session, entry_id: UUID, data: ReverseIn, user_id: UUID
) -> EntryOut:
    """
    Undo an entry by posting its mirror image, dated today unless a date is given. The
    original stays exactly as it was. An entry is reversed once, and a reversal is not
    itself reversed (post a fresh entry instead).
    """
    original = session.get(FinanceEntry, entry_id)
    if original is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    if original.kind == "reversal":
        raise HTTPException(
            status_code=409, detail="A reversal cannot be reversed; post a new entry"
        )
    if (
        session.query(FinanceEntry.id)
        .filter(FinanceEntry.reverses_id == entry_id)
        .first()
        is not None
    ):
        raise HTTPException(status_code=409, detail="This entry was already reversed")
    mirrored = [
        {
            "account": ln.account.code,
            "debit": ln.credit,
            "credit": ln.debit,
            "location_id": ln.location_id,
            "party_type": ln.party_type,
            "party_id": ln.party_id,
            "note": ln.note,
        }
        for ln in _lines_with_accounts(session, entry_id)
    ]
    reversal = post_entry(
        session,
        entry_date=data.entry_date or _today(),
        memo=f"Reversal of {original.entry_date}: {data.reason.strip()}",
        kind="reversal",
        created_by=user_id,
        reverses_id=entry_id,
        lines=mirrored,
        # a reversal belongs to the same business record, so reports by source see both
        source_type=original.source_type,
        source_id=original.source_id,
        source_fingerprint=original.source_fingerprint,
    )
    return to_out(session, reversal)


class _L:
    """A line with its account, for building mirror entries."""

    def __init__(self, line: FinanceLine, account: FinanceAccount):
        self.account = account
        self.debit, self.credit = line.debit, line.credit
        self.location_id, self.party_type = line.location_id, line.party_type
        self.party_id, self.note = line.party_id, line.note


def _lines_with_accounts(session: Session, entry_id: UUID) -> List[_L]:
    rows = (
        session.query(FinanceLine, FinanceAccount)
        .join(FinanceAccount, FinanceAccount.id == FinanceLine.account_id)
        .filter(FinanceLine.entry_id == entry_id)
        .order_by(FinanceLine.id)
        .all()
    )
    return [_L(line, account) for line, account in rows]


def to_out(session: Session, entry: FinanceEntry) -> EntryOut:
    lines = _lines_with_accounts(session, entry.id)
    reversed_by = (
        session.query(FinanceEntry.id)
        .filter(FinanceEntry.reverses_id == entry.id)
        .scalar()
    )
    return EntryOut(
        id=entry.id,
        entry_date=entry.entry_date,
        memo=entry.memo,
        kind=entry.kind,
        source_type=entry.source_type,
        source_id=entry.source_id,
        reverses_id=entry.reverses_id,
        reversed_by=reversed_by,
        created_by=entry.created_by,
        created_at=entry.created_at,
        lines=[
            LineOut(
                account=ln.account.code,
                account_name=ln.account.name,
                debit=ln.debit,
                credit=ln.credit,
                location_id=ln.location_id,
                note=ln.note,
            )
            for ln in lines
        ],
        total=sum((ln.debit for ln in lines), ZERO),
    )


def get_entry(session: Session, entry_id: UUID) -> EntryOut:
    entry = session.get(FinanceEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    return to_out(session, entry)


def list_entries(
    session: Session,
    date_from: Optional[date],
    date_to: Optional[date],
    kind: Optional[str],
    source_type: Optional[str],
    limit: int,
    offset: int,
) -> List[EntryOut]:
    q = session.query(FinanceEntry)
    if date_from:
        q = q.filter(FinanceEntry.entry_date >= date_from)
    if date_to:
        q = q.filter(FinanceEntry.entry_date <= date_to)
    if kind:
        q = q.filter(FinanceEntry.kind == kind)
    if source_type:
        q = q.filter(FinanceEntry.source_type == source_type)
    rows = (
        q.order_by(FinanceEntry.entry_date.desc(), FinanceEntry.created_at.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    return [to_out(session, e) for e in rows]


# --------------------------------------------------------------------------- reports
def trial_balance(session: Session, as_of: Optional[date]) -> TrialBalance:
    as_of = as_of or _today()
    sums = dict(
        (acct_id, (d, c))
        for acct_id, d, c in session.query(
            FinanceLine.account_id,
            func.coalesce(func.sum(FinanceLine.debit), 0),
            func.coalesce(func.sum(FinanceLine.credit), 0),
        )
        .join(FinanceEntry, FinanceEntry.id == FinanceLine.entry_id)
        .filter(FinanceEntry.entry_date <= as_of)
        .group_by(FinanceLine.account_id)
    )
    rows = []
    for account in session.query(FinanceAccount).order_by(FinanceAccount.code):
        d, c = (Decimal(x) for x in sums.get(account.id, (0, 0)))
        if d == 0 and c == 0 and not account.is_active:
            continue
        net = d - c
        rows.append(
            TrialBalanceRow(
                code=account.code,
                name=account.name,
                type=account.type,
                debit_total=d,
                credit_total=c,
                debit_balance=net if net > 0 else ZERO,
                credit_balance=-net if net < 0 else ZERO,
                balance=net if account.normal_side == "debit" else -net,
            )
        )
    debit_sum = sum((r.debit_balance for r in rows), ZERO)
    credit_sum = sum((r.credit_balance for r in rows), ZERO)
    return TrialBalance(
        as_of=as_of,
        rows=rows,
        debit_balance_total=debit_sum,
        credit_balance_total=credit_sum,
        balanced=debit_sum == credit_sum,
    )


def ledger(
    session: Session, code: str, date_from: Optional[date], date_to: Optional[date]
) -> Ledger:
    """One account's lines with a running balance, after an opening balance."""
    account = _account(session, code)
    sign = 1 if account.normal_side == "debit" else -1

    opening = ZERO
    if date_from:
        d, c = (
            session.query(
                func.coalesce(func.sum(FinanceLine.debit), 0),
                func.coalesce(func.sum(FinanceLine.credit), 0),
            )
            .join(FinanceEntry, FinanceEntry.id == FinanceLine.entry_id)
            .filter(
                FinanceLine.account_id == account.id,
                FinanceEntry.entry_date < date_from,
            )
            .one()
        )
        opening = sign * (Decimal(d) - Decimal(c))

    q = (
        session.query(FinanceLine, FinanceEntry)
        .join(FinanceEntry, FinanceEntry.id == FinanceLine.entry_id)
        .filter(FinanceLine.account_id == account.id)
    )
    if date_from:
        q = q.filter(FinanceEntry.entry_date >= date_from)
    if date_to:
        q = q.filter(FinanceEntry.entry_date <= date_to)
    running = opening
    lines = []
    for line, entry in q.order_by(
        FinanceEntry.entry_date, FinanceEntry.created_at, FinanceLine.id
    ):
        running += sign * (line.debit - line.credit)
        lines.append(
            LedgerLine(
                entry_id=entry.id,
                entry_date=entry.entry_date,
                memo=entry.memo,
                kind=entry.kind,
                debit=line.debit,
                credit=line.credit,
                balance=running,
            )
        )
    return Ledger(
        code=account.code,
        name=account.name,
        normal_side=account.normal_side,
        date_from=date_from,
        date_to=date_to,
        opening_balance=opening,
        closing_balance=running,
        lines=lines,
    )
