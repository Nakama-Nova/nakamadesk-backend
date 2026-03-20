import logging
from datetime import datetime
from typing import List

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.models.sale import Sale
from app.schemas.invoice import InvoiceItemResponse, InvoiceResponse

logger = logging.getLogger(__name__)


def generate_invoice_number(db: Session) -> str:
    """
    Generate a unique invoice number in the format NTD-YYYY-[UUID]
    Example: NTD-2026-A1B2C3D4
    """
    current_year = datetime.now().year
    import uuid

    suffix = uuid.uuid4().hex[:8].upper()
    return f"NTD-{current_year}-{suffix}"


def format_invoice_response(sale: Sale) -> InvoiceResponse:
    """Helper to map a Sale model to an InvoiceResponse schema."""
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
    """Retrieve paginated sales that have an invoice number."""
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
    """Retrieve an invoice by its number."""
    sale = db.query(Sale).filter(Sale.invoice_number == invoice_number).first()
    if not sale:
        return None
    return format_invoice_response(sale)
