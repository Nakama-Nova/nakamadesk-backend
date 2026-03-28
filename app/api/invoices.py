import os
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db.deps import check_role, get_db
from app.models.enums import UserRole
from app.models.sale import Sale
from app.models.user import User
from app.schemas.invoice import InvoiceResponse
from app.services.invoice_pdf_service import generate_invoice_pdf
from app.services.invoice_service import get_all_invoices, get_invoice_by_number

router = APIRouter(prefix="/invoices", tags=["Invoices"])


@router.get("/", response_model=List[InvoiceResponse])
def list_invoices(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(
        check_role([UserRole.OWNER, UserRole.MANAGER, UserRole.SALES])
    ),
    db: Session = Depends(get_db),
):
    """
    List all invoices with pagination.

    RBAC: Restricted to OWNER, MANAGER, and SALES roles.

    Args:
        limit (int): Maximum number of invoices to return.
        offset (int): Number of invoices to skip.
        current_user (User): Authenticated user.
        db (Session): Database session.

    Returns:
        List[InvoiceResponse]: List of invoices.
    """
    return get_all_invoices(db, limit, offset)


@router.get("/{invoice_number}", response_model=InvoiceResponse)
def get_invoice(
    invoice_number: str,
    current_user: User = Depends(
        check_role([UserRole.OWNER, UserRole.MANAGER, UserRole.SALES])
    ),
    db: Session = Depends(get_db),
):
    """
    Retrieve a specific invoice by its invoice number.

    RBAC: Restricted to OWNER, MANAGER, and SALES roles.

    Args:
        invoice_number (str): The unique invoice number.
        current_user (User): Authenticated user.
        db (Session): Database session.

    Returns:
        InvoiceResponse: Detailed invoice information.

    Raises:
        HTTPException: If invoice is not found.
    """
    invoice = get_invoice_by_number(db, invoice_number)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return invoice


@router.get("/{invoice_number}/pdf")
def get_invoice_pdf(
    invoice_number: str,
    current_user: User = Depends(
        check_role([UserRole.OWNER, UserRole.MANAGER, UserRole.SALES])
    ),
    db: Session = Depends(get_db),
):
    """
    Generate and retrieve the PDF version of a specific invoice.

    RBAC: Restricted to OWNER, MANAGER, and SALES roles.

    Args:
        invoice_number (str): The unique invoice number.
        current_user (User): Authenticated user.
        db (Session): Database session.

    Returns:
        FileResponse: The generated PDF file.

    Raises:
        HTTPException: If invoice is not found or PDF generation fails.
    """
    sale = db.query(Sale).filter(Sale.invoice_number == invoice_number).first()
    if not sale:
        raise HTTPException(status_code=404, detail="Invoice not found")

    pdf_path = generate_invoice_pdf(sale)
    if not os.path.exists(pdf_path):
        raise HTTPException(status_code=500, detail="Failed to generate PDF")

    return FileResponse(
        pdf_path, media_type="application/pdf", filename=os.path.basename(pdf_path)
    )
