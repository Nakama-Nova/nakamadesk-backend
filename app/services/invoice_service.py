import logging
from datetime import datetime, timezone
from typing import List

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.models.sale import Sale
from app.schemas.invoice import InvoiceItemResponse, InvoiceResponse

logger = logging.getLogger(__name__)


def generate_invoice_number(db: Session) -> str:
    """
    Generate a unique, business-aligned invoice number for a new sale.

    Format: NTD-YYYY-[8-char random suffix]

    Args:
        db (Session): Database session.

    Returns:
        str: A unique invoice identifier.
    """
    current_year = datetime.now(timezone.utc).year
    import uuid

    suffix = uuid.uuid4().hex[:8].upper()
    return f"NTD-{current_year}-{suffix}"


def format_invoice_response(sale: Sale) -> InvoiceResponse:
    """
    Map a internal Sale database model to a external InvoiceResponse schema.

    Processes line items and customer information for API consumption.

    Args:
        sale (Sale): The source sale record.

    Returns:
        InvoiceResponse: DTO representation of the invoice.
    """
    invoice_items = []
    for si in sale.items:
        invoice_items.append(
            InvoiceItemResponse(
                item_id=si.item_id,
                item_name=si.item.name if si.item else "Unknown Item",
                quantity=si.quantity,
                price_at_sale=si.price_at_sale,
                gst_percent=si.gst_percent,
                cgst_amount=si.cgst_amount,
                sgst_amount=si.sgst_amount,
                total_price=si.total_price,
            )
        )

    return InvoiceResponse(
        id=sale.id,
        invoice_number=sale.invoice_number,
        invoice_date=sale.invoice_date,
        total_amount=sale.total_amount,
        customer=sale.customer,
        items=invoice_items,
    )


def get_all_invoices(
    db: Session, limit: int = 50, offset: int = 0
) -> List[InvoiceResponse]:
    """
    Retrieve all sales records that have an associated invoice number.

    Args:
        db (Session): Database session.
        limit (int): Pagination limit.
        offset (int): Pagination offset.

    Returns:
        List[InvoiceResponse]: List of formatted invoice records.
    """
    sales = (
        db.query(Sale)
        .filter(Sale.invoice_number.isnot(None))
        .order_by(desc(Sale.created_at))
        .limit(limit)
        .offset(offset)
        .all()
    )
    invoices = [format_invoice_response(sale) for sale in sales]
    return invoices


def get_invoice_by_number(db: Session, invoice_number: str) -> InvoiceResponse:
    """
    Search for and retrieve a specific invoice by its unique number.

    Args:
        db (Session): Database session.
        invoice_number (str): The unique invoice identifier.

    Returns:
        InvoiceResponse: The requested invoice, or None if not found.
    """
    sale = db.query(Sale).filter(Sale.invoice_number == invoice_number).first()
    if not sale:
        return None
    return format_invoice_response(sale)
