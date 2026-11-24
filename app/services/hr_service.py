"""Shifts, holidays, staff documents, offboarding, and the extra rows on attendance."""

import re
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.hr import Holiday, Shift, UserDocument
from app.models.user import User
from app.schemas.hr import DOC_TYPES, DocumentUploadRequest, HolidayCreate, ShiftCreate
from app.services import audit, payroll_service, wage_service

# ------------------------------------------------------------------- shifts


def create_shift(db: Session, data: ShiftCreate) -> Shift:
    if db.query(Shift).filter(Shift.name == data.name).first():
        raise HTTPException(status_code=409, detail="A shift with that name exists")
    row = Shift(**data.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_shift(db: Session, shift_id: UUID, data: ShiftCreate) -> Shift:
    row = db.get(Shift, shift_id)
    if not row:
        raise HTTPException(status_code=404, detail="Shift not found")
    if db.query(Shift).filter(Shift.name == data.name, Shift.id != shift_id).first():
        raise HTTPException(status_code=409, detail="A shift with that name exists")
    for key, value in data.model_dump().items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row


def delete_shift(db: Session, shift_id: UUID) -> None:
    row = db.get(Shift, shift_id)
    if not row:
        raise HTTPException(status_code=404, detail="Shift not found")
    if db.query(User.id).filter(User.shift_id == shift_id).first():
        raise HTTPException(
            status_code=409, detail="People are on this shift; move them first"
        )
    db.delete(row)
    db.commit()


def assign_shift(db: Session, user_id: UUID, shift_id: Optional[UUID]) -> User:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Staff member not found")
    if shift_id is not None:
        shift = db.get(Shift, shift_id)
        if not shift or not shift.active:
            raise HTTPException(status_code=404, detail="Shift not found")
    user.shift_id = shift_id
    db.commit()
    db.refresh(user)
    return user


# ----------------------------------------------------------------- holidays


def create_holiday(db: Session, data: HolidayCreate) -> Holiday:
    if db.query(Holiday).filter(Holiday.date == data.date).first():
        raise HTTPException(
            status_code=409, detail="There is already a holiday that day"
        )
    row = Holiday(**data.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def delete_holiday(db: Session, holiday_id: UUID) -> None:
    row = db.get(Holiday, holiday_id)
    if not row:
        raise HTTPException(status_code=404, detail="Holiday not found")
    db.delete(row)
    db.commit()


# ---------------------------------------------- attendance: holiday rows


def holiday_rows(
    db: Session, start: date, end: date, user_ids: Optional[List[UUID]], seen: set
) -> List[dict]:
    """
    Public holidays as attendance-shaped rows (status "holiday", no check-in) for
    active people, for days they have no record of their own. `seen` holds the
    (user_id, date) pairs already on the sheet. The rows are built, never stored.
    """
    if (end - start).days > 92:
        raise HTTPException(
            status_code=422, detail="Ask for three months of attendance at most"
        )
    holidays = (
        db.query(Holiday).filter(Holiday.date >= start, Holiday.date <= end).all()
    )
    if not holidays:
        return []
    people = db.query(User).filter(User.is_active.is_(True))
    if user_ids is not None:
        people = people.filter(User.id.in_(user_ids))
    out = []
    for person in people.all():
        for h in holidays:
            if (person.id, h.date) in seen:
                continue
            if person.joining_date and h.date < person.joining_date:
                continue
            if person.last_working_date and h.date > person.last_working_date:
                continue
            out.append(
                {
                    "id": uuid.uuid5(h.id, str(person.id)),
                    "user_id": person.id,
                    "date": h.date,
                    "status": "holiday",
                    "daily_wage": Decimal("0"),
                    "payment_status": "pending",
                    "recorded_by": person.id,
                    "paid": bool(h.paid),
                    "holiday_name": h.name,
                    "created_at": h.created_at,
                }
            )
    return out


# ---------------------------------------------------------------- documents


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.")[:80] or "file"


def _person(db: Session, user_id: UUID) -> User:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Staff member not found")
    return user


def new_document(
    db: Session, actor: User, user_id: UUID, req: DocumentUploadRequest, store
) -> dict:
    _person(db, user_id)
    doc_id = uuid.uuid4()
    key = f"staff/{user_id}/{doc_id}{DOC_TYPES[req.content_type]}"
    row = UserDocument(
        id=doc_id,
        user_id=user_id,
        kind=req.kind,
        filename=_safe_name(req.filename),
        content_type=req.content_type,
        size=req.size,
        storage_key=key,
        confirmed=False,
        uploaded_by=actor.id,
    )
    db.add(row)
    db.commit()
    signed = store.presign_put(key, req.content_type, req.size)
    return {
        "document_id": doc_id,
        "upload_url": signed["url"],
        "headers": signed["headers"],
    }


def _doc(db: Session, user_id: UUID, doc_id: UUID) -> UserDocument:
    row = db.get(UserDocument, doc_id)
    if not row or row.user_id != user_id:
        raise HTTPException(status_code=404, detail="Document not found")
    return row


def confirm_document(db: Session, user_id: UUID, doc_id: UUID, store) -> UserDocument:
    row = _doc(db, user_id, doc_id)
    stored = store.head(row.storage_key)
    if stored is None:
        raise HTTPException(
            status_code=409, detail="The file has not been uploaded yet"
        )
    if stored != row.size:
        store.delete(row.storage_key)
        db.delete(row)
        db.commit()
        raise HTTPException(
            status_code=422,
            detail="The uploaded file is not the size that was declared",
        )
    row.confirmed = True
    db.commit()
    db.refresh(row)
    return row


def document_out(row: UserDocument, store) -> dict:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "kind": row.kind,
        "filename": row.filename,
        "content_type": row.content_type,
        "size": row.size,
        "uploaded_by": row.uploaded_by,
        "uploaded_at": row.uploaded_at,
        # a private object: only ever reachable through a link that expires in minutes
        "url": (
            store.presign_get(row.storage_key, expires=300, filename=row.filename)
            if store is not None
            else None
        ),
    }


def list_documents(db: Session, user_id: UUID, store) -> List[dict]:
    _person(db, user_id)
    rows = (
        db.query(UserDocument)
        .filter(UserDocument.user_id == user_id, UserDocument.confirmed.is_(True))
        .order_by(UserDocument.uploaded_at.desc())
        .all()
    )
    return [document_out(r, store) for r in rows]


def delete_document(db: Session, user_id: UUID, doc_id: UUID, store) -> None:
    row = _doc(db, user_id, doc_id)
    key = row.storage_key
    db.delete(row)
    db.commit()
    if store is not None:
        store.delete(key)


# ----------------------------------------------------------------- offboard


def offboard(
    db: Session, actor: User, user_id: UUID, last_day: date, note: Optional[str]
) -> dict:
    """
    Record the last working day. The account is switched off from the day after (at
    once when that day has already come; otherwise get_current_user refuses it then).
    Returns the final settlement; the owner pays it with the normal pay-out.
    """
    user = db.query(User).filter(User.id == user_id).with_for_update().first()
    if not user:
        raise HTTPException(status_code=404, detail="Staff member not found")
    if user.id == actor.id:
        raise HTTPException(status_code=400, detail="You cannot offboard yourself")
    if user.joining_date and last_day < user.joining_date:
        raise HTTPException(
            status_code=422, detail="The last day is before the joining date"
        )
    old = {"last_working_date": user.last_working_date, "is_active": user.is_active}
    user.last_working_date = last_day
    switch_off_on = last_day + timedelta(days=1)
    if switch_off_on <= date.today() and user.is_active:
        user.is_active = False
        user.status = "inactive"
        user.token_version = (user.token_version or 0) + 1
    audit.record(
        db,
        actor.id,
        "users",
        user.id,
        "OFFBOARD",
        old=old,
        new={"last_working_date": last_day, "note": note},
    )
    db.flush()
    wage_service.refresh_current(db, user)
    daily = payroll_service.pending_daily(db, user.id, last_day)
    if wage_service.has_salaried(db, user):
        daily += wage_service.salaried_period(db, user, last_day, cap_today=False)[
            "amount"
        ]
    extras = payroll_service.pending_extras(db, user.id, last_day)
    advance = payroll_service.advance_balance(db, user.id)
    gross = daily + extras
    db.commit()
    return {
        "user_id": user.id,
        "last_working_date": last_day,
        "deactivates_on": switch_off_on,
        "is_active": bool(user.is_active),
        "pending_daily": daily,
        "pending_extras": extras,
        "advance_balance": advance,
        "gross": gross,
        "net_to_pay": gross - min(advance, gross),
    }
