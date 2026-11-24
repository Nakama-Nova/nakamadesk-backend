"""A person's wage history, shift, documents and exit. Staff accounts stay under /auth/users."""

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.iam.service import require
from app.models.hr import UserWage
from app.models.user import User
from app.schemas.hr import (
    DocumentResponse,
    DocumentUploadRequest,
    DocumentUploadResponse,
    OffboardRequest,
    Settlement,
    ShiftAssign,
    WageCreate,
    WageResponse,
)
from app.schemas.user import UserResponse
from app.services import hr_service, wage_service
from app.services.object_store import S3Store, get_store, get_store_or_none

router = APIRouter(prefix="/people/users", tags=["People"])


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
