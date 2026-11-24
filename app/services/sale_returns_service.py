"""Returns and credit notes on a counter bill (the bill itself is never edited)."""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import joinedload, selectinload

from app.models.item import Item
from app.models.sale_return import SaleReturn, SaleReturnItem
from app.repositories.base import AbstractUnitOfWork
from app.schemas.sale import ReturnCreate
from app.services import audit, inventory_service
from app.services.invoice_service import generate_credit_note_number
from app.services.sales_service import locked_sale, q

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))


def _with_items():
    return selectinload(SaleReturn.items).joinedload(SaleReturnItem.sale_item)


def create_return(
    uow: AbstractUnitOfWork, sale_id: UUID, data: ReturnCreate, user_id: Optional[UUID]
) -> SaleReturn:
    """
    Take goods back from a bill and issue a credit note.

    Each line refunds quantity / sold quantity of what the customer paid for it: the
    line total less its share of the bill discount (shared out by line total), with
    the GST returned in the same proportion. Each line is rounded to the paisa and the
    refund is the sum of the rounded lines. Stock goes back to the bill's location
    unless restock is off (damaged goods).
    """
    wanted: Dict[UUID, int] = {}
    for line in data.items:
        wanted[line.sale_item_id] = wanted.get(line.sale_item_id, 0) + line.quantity

    with uow:
        sale = locked_sale(uow, sale_id)
        if (sale.order_status or "completed") == "cancelled":
            raise HTTPException(
                status_code=409, detail="A cancelled bill cannot take returns"
            )
        by_id = {ln.id: ln for ln in sale.items}
        done: Dict[UUID, int] = {}
        for ret in sale.returns:
            for r in ret.items:
                done[r.sale_item_id] = done.get(r.sale_item_id, 0) + r.quantity

        bill_lines = sum((Decimal(ln.total_price or 0) for ln in sale.items), Decimal(0))
        discount = Decimal(sale.discount or 0)
        rows: List[SaleReturnItem] = []
        for line_id, qty in wanted.items():
            line = by_id.get(line_id)
            if line is None:
                raise HTTPException(
                    status_code=422, detail="That line is not on this bill"
                )
            left = line.quantity - done.get(line_id, 0)
            if qty > left:
                raise HTTPException(
                    status_code=422,
                    detail=f"Only {max(left, 0)} left to return of {line.item_name}",
                )
            line_total = Decimal(line.total_price or 0)
            # what was really paid for the line, once its share of the discount is off
            paid = line_total
            if bill_lines > 0 and discount > 0:
                paid = line_total - discount * line_total / bill_lines
            share = Decimal(qty) / Decimal(line.quantity)
            ratio = paid / line_total if line_total > 0 else Decimal(0)
            amount = q(share * paid)
            cgst = q(share * Decimal(line.cgst_amount or 0) * ratio)
            sgst = q(share * Decimal(line.sgst_amount or 0) * ratio)
            rows.append(
                SaleReturnItem(
                    id=uuid.uuid4(),
                    sale_item_id=line.id,
                    item_id=line.item_id,
                    quantity=qty,
                    taxable_value=amount - cgst - sgst,
                    cgst_amount=cgst,
                    sgst_amount=sgst,
                    amount=amount,
                )
            )

        number = generate_credit_note_number(uow.session, datetime.now(IST).date())
        if data.restock:
            stock = sorted({r.item_id for r in rows if r.item_id})
            if stock:  # lock in a fixed order so two returns cannot deadlock
                uow.session.query(Item).filter(Item.id.in_(stock)).order_by(
                    Item.id
                ).with_for_update().all()
            for r in rows:
                if r.item_id:
                    inventory_service.return_finished_goods(
                        uow,
                        r.item_id,
                        r.quantity,
                        sale.id,
                        f"Sales return {number}",
                        created_by=user_id,
                        location_id=sale.location_id,
                    )
        ret = SaleReturn(
            id=uuid.uuid4(),
            credit_note_number=number,
            sale_id=sale.id,
            reason=data.reason,
            refund_method=data.refund_method,
            refund_amount=sum((r.amount for r in rows), Decimal("0.00")),
            restocked=data.restock,
            created_by=user_id,
            items=rows,
        )
        uow.session.add(ret)
        audit.record(
            uow.session,
            user_id,
            "sales",
            sale.id,
            "return",
            new={"credit_note": number, "refund": ret.refund_amount},
        )
        uow.commit()
    logger.info("Credit note %s issued on bill %s", number, sale.invoice_number)
    return (
        uow.session.query(SaleReturn)
        .options(_with_items())
        .filter(SaleReturn.id == ret.id)
        .one()
    )


def list_returns(uow: AbstractUnitOfWork, sale_id: UUID) -> List[SaleReturn]:
    """Credit notes on a bill, newest first."""
    from app.models.sale import Sale

    if not uow.session.get(Sale, sale_id):
        raise HTTPException(status_code=404, detail="Sale not found")
    return (
        uow.session.query(SaleReturn)
        .options(_with_items())
        .filter(SaleReturn.sale_id == sale_id)
        .order_by(SaleReturn.created_at.desc(), SaleReturn.credit_note_number.desc())
        .all()
    )


def get_by_number(db, credit_note_number: str) -> Optional[SaleReturn]:
    return (
        db.query(SaleReturn)
        .options(_with_items(), joinedload(SaleReturn.sale))
        .filter(SaleReturn.credit_note_number == credit_note_number)
        .first()
    )
