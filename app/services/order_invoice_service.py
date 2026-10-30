"""
Turn an order into a bill (invoice), once.

The order is the agreement with the customer: its pieces, the prices agreed (GST included,
the same figure Stock and the website show), the advance paid. The bill takes those as
they are, strips the GST back out so the bill and Profit agree with every other bill,
takes stock off for catalogue pieces, carries the payments over and links the two
(orders.sale_id). Calling it again returns the same bill.
"""

import logging
import uuid
from decimal import Decimal
from typing import Optional, Tuple
from uuid import UUID

from fastapi import HTTPException

from app.models.order import Order
from app.models.sale import Sale
from app.models.sale_item import SaleItem
from app.repositories.base import AbstractUnitOfWork
from app.services import inventory_service, pricing
from app.services.invoice_service import generate_invoice_number

logger = logging.getLogger(__name__)

BILLABLE = ("confirmed", "in_production", "ready", "delivered")
CENT = Decimal("0.01")


def agreed_total(order: Order) -> Decimal:
    """What the customer owes in all: the final amount once set, otherwise the estimate."""
    final = Decimal(order.final_amount or 0)
    return final if final > 0 else Decimal(order.estimated_amount or 0)


def payment_status(paid: Decimal, total: Decimal) -> str:
    if paid <= 0:
        return "pending"
    return "paid" if paid >= total else "partial"


def invoice_order(
    uow: AbstractUnitOfWork,
    order_id: UUID,
    user_id: Optional[UUID],
    allow_negative_stock: bool = False,
) -> Tuple[Sale, bool]:
    """Returns (sale, created). created is False when the order already had its bill."""
    with uow:
        order = (
            uow.session.query(Order)
            .filter(Order.id == order_id)
            .with_for_update()
            .first()
        )
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        if order.sale_id:
            return uow.sales.get_by_id_eager(order.sale_id), False
        if order.status == "cancelled":
            raise HTTPException(
                status_code=409, detail="A cancelled order cannot be billed"
            )
        if order.status not in BILLABLE:
            raise HTTPException(
                status_code=409,
                detail="Confirm the order with the customer before billing it",
            )
        if not order.items:
            raise HTTPException(
                status_code=422, detail="The order has no pieces to bill"
            )
        total = agreed_total(order)
        if total <= 0:
            raise HTTPException(
                status_code=409, detail="Set the order's amount before billing it"
            )

        sale_id = uuid.uuid4()
        lines = []  # (item or None, description, quantity, gross, gst_percent)
        for line in order.items:
            gross = (Decimal(line.unit_price or 0) * line.quantity).quantize(CENT)
            if line.item_id:
                item = inventory_service.remove_finished_goods(
                    uow=uow,
                    item_id=line.item_id,
                    quantity=line.quantity,
                    sale_id=sale_id,
                    created_by=user_id,
                    allow_negative=allow_negative_stock,
                )
                lines.append(
                    (item, None, line.quantity, gross, Decimal(item.gst_percent or 0))
                )
            else:
                lines.append(
                    (
                        None,
                        line.item_name or "Custom piece",
                        line.quantity,
                        gross,
                        Decimal("0"),
                    )
                )

        pieces = sum((x[3] for x in lines), Decimal("0"))
        if total > pieces:  # delivery, finishing... agreed on top of the pieces
            lines.append((None, "Other charges", 1, total - pieces, Decimal("0")))
            pieces = total
        discount = pieces - total  # the customer was given a lower total

        sale_items, sub_total, tax_total = [], Decimal("0"), Decimal("0")
        for item, description, qty, gross, gst in lines:
            net = pricing.net_from_gross(gross, gst)
            tax = gross - net
            sub_total += net
            tax_total += tax
            sale_items.append(
                SaleItem(
                    id=uuid.uuid4(),
                    sale_id=sale_id,
                    item_id=item.id if item else None,
                    description=description,
                    quantity=qty,
                    price_at_sale=(net / qty).quantize(CENT),
                    gst_percent=gst,
                    cgst_amount=(tax / 2).quantize(CENT),
                    sgst_amount=(tax / 2).quantize(CENT),
                    total_price=gross,
                )
            )

        paid = Decimal(order.advance_paid or 0)
        last = order.payments[-1] if order.payments else None
        sale = Sale(
            id=sale_id,
            invoice_number=generate_invoice_number(uow.session),
            customer_id=order.customer_id,
            user_id=user_id,
            client_id=order.id,  # one bill per order, even if two people press the button
            order_type="order",
            sub_total=sub_total,
            tax_total=tax_total,
            discount=discount,
            total_amount=total,
            payment_status=payment_status(paid, total),
            payment_method=last.method if last else None,
            order_status="delivered" if order.status == "delivered" else "confirmed",
        )
        sale.items = sale_items
        uow.sales.add(sale)
        order.sale_id = sale_id
        uow.commit()
    logger.info("Order %s billed as %s", order.order_number, sale.invoice_number)
    return uow.sales.get_by_id_eager(sale_id), True
