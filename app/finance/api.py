from datetime import date
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.deps import check_role, get_db
from app.finance import service
from app.finance.schemas import (
    AccountIn,
    AccountOut,
    AccountUpdate,
    Closing,
    ClosingIn,
    EntryOut,
    JournalIn,
    Ledger,
    ReverseIn,
    TrialBalance,
)
from app.models.enums import UserRole
from app.models.user import User

router = APIRouter(prefix="/finance", tags=["Finance ledger"])

READ = [UserRole.OWNER, UserRole.MANAGER]
WRITE = [UserRole.OWNER]


@router.get("/accounts", response_model=List[AccountOut])
def list_accounts(
    include_inactive: bool = False,
    _: User = Depends(check_role(READ)),
    db: Session = Depends(get_db),
):
    """The chart of accounts."""
    return service.list_accounts(db, include_inactive)


@router.post("/accounts", response_model=AccountOut, status_code=201)
def create_account(
    body: AccountIn,
    current_user: User = Depends(check_role(WRITE)),
    db: Session = Depends(get_db),
):
    """Owner only. Add an account; accounts are never deleted, only switched off."""
    return service.create_account(db, body, current_user.id)


@router.patch("/accounts/{code}", response_model=AccountOut)
def update_account(
    code: str,
    body: AccountUpdate,
    current_user: User = Depends(check_role(WRITE)),
    db: Session = Depends(get_db),
):
    """Owner only. Rename an account or switch it off (nothing more can be posted to it)."""
    return service.update_account(db, code, body, current_user.id)


@router.get("/trial-balance", response_model=TrialBalance)
def trial_balance(
    as_of: Optional[date] = None,
    _: User = Depends(check_role(READ)),
    db: Session = Depends(get_db),
):
    """Every account's totals and balance up to `as_of` (today by default)."""
    return service.trial_balance(db, as_of)


@router.get("/ledger/{code}", response_model=Ledger)
def account_ledger(
    code: str,
    date_from: Optional[date] = Query(None, alias="from"),
    date_to: Optional[date] = Query(None, alias="to"),
    _: User = Depends(check_role(READ)),
    db: Session = Depends(get_db),
):
    """One account's entries with a running balance, after an opening balance."""
    return service.ledger(db, code, date_from, date_to)


@router.get("/entries", response_model=List[EntryOut])
def list_entries(
    date_from: Optional[date] = Query(None, alias="from"),
    date_to: Optional[date] = Query(None, alias="to"),
    kind: Optional[str] = Query(None, pattern="^(manual|posting|reversal)$"),
    source_type: Optional[str] = Query(None, max_length=40),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(check_role(READ)),
    db: Session = Depends(get_db),
):
    return service.list_entries(
        db, date_from, date_to, kind, source_type, limit, offset
    )


@router.get("/entries/{entry_id}", response_model=EntryOut)
def get_entry(
    entry_id: UUID,
    _: User = Depends(check_role(READ)),
    db: Session = Depends(get_db),
):
    return service.get_entry(db, entry_id)


@router.post("/journal", response_model=EntryOut, status_code=201)
def post_journal(
    body: JournalIn,
    current_user: User = Depends(check_role(WRITE)),
    db: Session = Depends(get_db),
):
    """
    Owner only. A manual entry (opening balances, money the owner puts in or takes out,
    a correction). It must balance and needs a reason; it cannot be dated in the future
    or inside a closed month.
    """
    return service.post_journal(db, body, current_user.id)


@router.post("/entries/{entry_id}/reverse", response_model=EntryOut, status_code=201)
def reverse_entry(
    entry_id: UUID,
    body: ReverseIn,
    current_user: User = Depends(check_role(WRITE)),
    db: Session = Depends(get_db),
):
    """Owner only. Undo an entry by posting its mirror image. The original stays."""
    return service.reverse(db, entry_id, body, current_user.id)


@router.get("/closing", response_model=Closing)
def get_closing(_: User = Depends(check_role(READ)), db: Session = Depends(get_db)):
    """The date the books are closed through (nothing can be posted on or before it)."""
    return service.get_closing(db)


@router.put("/closing", response_model=Closing)
def set_closing(
    body: ClosingIn,
    current_user: User = Depends(check_role(WRITE)),
    db: Session = Depends(get_db),
):
    """Owner only. Close the books through a date, or open them again. Always audited."""
    return service.set_closing(db, body, current_user.id)
