"""A person's wage history, shift, documents and exit. Staff accounts stay under /auth/users."""

from datetime import date
from typing import List, Optional, get_args
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.deps import get_current_user, get_db
from app.iam.service import require
from app.models.hr import Holiday, LeaveType, Shift, UserWage
from app.models.user import User
from app.schemas.hr import (
    DOC_TYPES,
    MAX_DOC_BYTES,
    DocKind,
    DocumentResponse,
    DocumentUploadRequest,
    DocumentUploadResponse,
    OffboardRequest,
    Settlement,
    ShiftAssign,
    WageCreate,
    WageResponse,
)
from app.schemas.user import (
    Employment,
    IdProofType,
    PayType,
    UserResponse,
)
from app.services import hr_service, wage_service
from app.services.object_store import S3Store, get_store, get_store_or_none

router = APIRouter(prefix="/people/users", tags=["People"])
options_router = APIRouter(prefix="/people", tags=["People"])

LABELS = {
    "daily": ("Daily wage", "per day"),
    "weekly": ("Weekly wage", "per week"),
    "monthly": ("Monthly salary", "per month"),
    "hourly": ("Hourly wage", "per hour"),
    "full_time": ("Full time", None),
    "part_time": ("Part time", None),
    "aadhaar": ("Aadhaar", None),
    "pan": ("PAN", None),
    "voter": ("Voter ID", None),
    "driving": ("Driving licence", None),
    "other": ("Other", None),
    "id_proof": ("ID proof", None),
    "contract": ("Contract", None),
    "photo": ("Photo", None),
}
WEEKDAYS = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


def _choices(values):
    return [
        {
            "value": v,
            "label": LABELS.get(v, (v.replace("_", " ").title(), None))[0],
            "unit": LABELS.get(v, ("", None))[1],
        }
        for v in values
    ]


@options_router.get("/options")
def hr_options(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """
    Every choice list the staff, leave, shift and holiday screens need, so no client
    hardcodes them: the fixed lists (pay types, employment, ID proof types, document
    kinds and limits, weekdays, leave statuses) and the live ones (active leave types,
    active shifts, holidays from this year on). Anyone signed in.
    """
    return {
        "pay_types": _choices(get_args(PayType)),
        "employment": _choices(get_args(Employment)),
        "id_proof_types": _choices(get_args(IdProofType)),
        "document_kinds": _choices(get_args(DocKind)),
        "document_content_types": list(DOC_TYPES),
        "document_max_bytes": MAX_DOC_BYTES,
        "id_proof_last_chars": 4,
        "weekdays": [{"value": i, "label": n} for i, n in enumerate(WEEKDAYS)],
        "leave_statuses": ["pending", "approved", "rejected", "cancelled"],
        "leave_types": [
            {"id": t.id, "name": t.name, "paid": t.paid, "yearly_days": t.yearly_days}
            for t in db.query(LeaveType)
            .filter(LeaveType.active.is_(True))
            .order_by(LeaveType.name)
        ],
        "shifts": [
            {
                "id": s.id,
                "name": s.name,
                "start_time": s.start_time.isoformat(),
                "end_time": s.end_time.isoformat(),
                "grace_minutes": s.grace_minutes,
                "weekly_off": s.weekly_off or [],
            }
            for s in db.query(Shift).filter(Shift.active.is_(True)).order_by(Shift.name)
        ],
        "holidays": [
            {"id": h.id, "date": h.date, "name": h.name, "paid": h.paid}
            for h in db.query(Holiday)
            .filter(Holiday.date >= date(date.today().year, 1, 1))
            .order_by(Holiday.date)
        ],
    }


def _exists(db: Session, user_id: UUID) -> User:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Staff member not found")
    return user


@router.get("/{user_id}/wages", response_model=List[WageResponse])
def wage_history(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require("people:wage:history_read")),
):
    """Every rate this person has had, newest first."""
    _exists(db, user_id)
    return (
        db.query(UserWage)
        .filter(UserWage.user_id == user_id)
        .order_by(UserWage.effective_from.desc(), UserWage.created_at.desc())
        .all()
    )


@router.post("/{user_id}/wages", response_model=WageResponse, status_code=201)
def add_wage(
    user_id: UUID,
    data: WageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require("people:wage:history_write")),
):
    """
    A new pay type / rate from a date. Today or earlier takes effect at once; later
    dates arrive on their day. 409 when it would reach back into days already paid out.
    """
    user = db.query(User).filter(User.id == user_id).with_for_update().first()
    if not user:
        raise HTTPException(status_code=404, detail="Staff member not found")
    row = wage_service.add_wage(
        db,
        current_user,
        user,
        data.pay_type,
        data.amount,
        data.effective_from,
        data.note,
    )
    db.commit()
    db.refresh(row)
    return row


@router.put("/{user_id}/shift", response_model=UserResponse)
def set_shift(
    user_id: UUID,
    body: ShiftAssign,
    db: Session = Depends(get_db),
    current_user: User = Depends(require("shifts:shift:manage")),
):
    return hr_service.assign_shift(db, user_id, body.shift_id)


@router.post(
    "/{user_id}/documents/upload-url",
    response_model=DocumentUploadResponse,
    status_code=201,
)
def document_upload_url(
    user_id: UUID,
    body: DocumentUploadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require("people:user:write_documents")),
    store: S3Store = Depends(get_store),
):
    """Step 1: a signed link to PUT the file to (private bucket, 10 MB, photo or PDF)."""
    return hr_service.new_document(db, current_user, user_id, body, store)


@router.post(
    "/{user_id}/documents/{document_id}/confirm", response_model=DocumentResponse
)
def confirm_document(
    user_id: UUID,
    document_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require("people:user:write_documents")),
    store: S3Store = Depends(get_store),
):
    """Step 3: after the PUT, check the file is in the bucket and keep it."""
    row = hr_service.confirm_document(db, user_id, document_id, store)
    return hr_service.document_out(row, store)


@router.get("/{user_id}/documents", response_model=List[DocumentResponse])
def list_documents(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require("people:user:read_documents")),
    store: Optional[S3Store] = Depends(get_store_or_none),
):
    """Each with a signed link that stops working in a few minutes."""
    return hr_service.list_documents(db, user_id, store)


@router.delete("/{user_id}/documents/{document_id}", status_code=204)
def delete_document(
    user_id: UUID,
    document_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require("people:user:write_documents")),
    store: Optional[S3Store] = Depends(get_store_or_none),
):
    hr_service.delete_document(db, user_id, document_id, store)


@router.post("/{user_id}/offboard", response_model=Settlement)
def offboard(
    user_id: UUID,
    body: OffboardRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require("people:user:offboard")),
):
    """Set the last working day; the account goes off the day after. Returns what is owed."""
    return hr_service.offboard(
        db, current_user, user_id, body.last_working_date, body.note
    )
