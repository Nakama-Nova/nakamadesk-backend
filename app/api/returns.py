import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.iam.service import require
from app.models.user import User
from app.services import letter_service, sale_returns_service
from app.services.invoice_pdf_service import render_credit_note_pdf

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/returns", tags=["Returns"])


# {credit_note_number:path}: the number itself has slashes (CN/26-27/0001)
@router.get("/{credit_note_number:path}/pdf")
def get_credit_note_pdf(
    credit_note_number: str,
    current_user: User = Depends(require("sales:credit_note:read")),
    db: Session = Depends(get_db),
):
    """The PDF credit note, drawn in memory. RBAC: sales:credit_note:read."""
    ret = sale_returns_service.get_by_number(db, credit_note_number)
    if not ret:
        raise HTTPException(status_code=404, detail="Credit note not found")
    try:
        pdf = render_credit_note_pdf(ret, letter_service.get_profile(db))
    except Exception as exc:
        logger.exception("Could not draw the credit note PDF for %s", credit_note_number)
        raise HTTPException(
            status_code=500, detail="Could not draw the credit note PDF"
        ) from exc
    filename = credit_note_number.replace("/", "-")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}.pdf"'},
    )
