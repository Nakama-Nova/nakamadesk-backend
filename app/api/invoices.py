from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.db.deps import get_db
from app.models.sale import Sale
from app.models.user import User
from app.schemas.invoice import InvoiceItemResponse, InvoiceResponse
from app.services.invoice_service import (get_all_invoices,
                                          get_invoice_by_number)

router = APIRouter(prefix="/invoices", tags=["Invoices"])


@router.get("/", response_model=List[InvoiceResponse])
def list_invoices(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_all_invoices(db, limit, offset)


@router.get("/{invoice_number}", response_model=InvoiceResponse)
def get_invoice(
    invoice_number: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    invoice = get_invoice_by_number(db, invoice_number)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return invoice


import os

from fastapi.responses import FileResponse

from app.services.invoice_pdf_service import generate_invoice_pdf


@router.get("/{invoice_number}/pdf")
def get_invoice_pdf(
    invoice_number: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sale = db.query(Sale).filter(Sale.invoice_number == invoice_number).first()
    if not sale:
        raise HTTPException(status_code=404, detail="Invoice not found")

    pdf_path = generate_invoice_pdf(sale)
    if not os.path.exists(pdf_path):
        raise HTTPException(status_code=500, detail="Failed to generate PDF")

    return FileResponse(
        pdf_path, media_type="application/pdf", filename=os.path.basename(pdf_path)
    )
