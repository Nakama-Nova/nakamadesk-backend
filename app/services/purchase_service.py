"""
Service layer for Purchase management — Day 2 enhancement.

Adds confirm_purchase() which is the key Day 2 operation:
  - Updates raw material stock via inventory movement engine
  - Creates ITC placeholder for GST-registered purchases
  - Marks purchase as confirmed

Existing purchase CRUD will be added here as the system grows.
"""

import logging
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import joinedload

from app.models.purchase import Purchase
from app.models.purchase_item import PurchaseItem
from app.repositories.base import AbstractUnitOfWork
from app.schemas.purchase import PurchaseCreate
from app.services import inventory_service

logger = logging.getLogger(__name__)


def create_purchase(
    uow: AbstractUnitOfWork, purchase_data: PurchaseCreate
) -> Purchase:
    """
    Create a new purchase record with line items.

    Args:
        uow: Unit of Work.
        purchase_data: Validated purchase payload.

    Returns:
        Purchase: The created purchase record.
    """
    with uow:
        # Calculate totals
        total_amount = Decimal("0")
        tax_total = Decimal("0")

        new_purchase = Purchase(
            supplier_id=purchase_data.supplier_id,
            invoice_number=purchase_data.invoice_number,
            purchase_date=purchase_data.purchase_date,
            purchase_type=purchase_data.purchase_type,
            is_itc_eligible=purchase_data.is_itc_eligible,
            notes=purchase_data.notes,
            payment_status=purchase_data.payment_status,
            status="pending",
        )
        uow.session.add(new_purchase)
        uow.flush()  # Get ID

        for item_data in purchase_data.items:
            # Basic validation: must have either item or raw material
            if not item_data.item_id and not item_data.raw_material_id:
                raise HTTPException(
                    status_code=400,
                    detail="Each purchase item must reference an item or raw material",
                )

            line_base = item_data.quantity * item_data.unit_price
            line_tax = line_base * (item_data.gst_percent / Decimal("100"))
            line_total = line_base + line_tax

            total_amount += line_total
            tax_total += line_tax

            new_item = PurchaseItem(
                purchase_id=new_purchase.id,
                item_id=item_data.item_id,
                raw_material_id=item_data.raw_material_id,
                quantity=item_data.quantity,
                unit_price=item_data.unit_price,
                gst_percent=item_data.gst_percent,
                line_total=line_total,
            )
            uow.session.add(new_item)

        new_purchase.total_amount = total_amount
        new_purchase.tax_total = tax_total
        uow.commit()

    logger.info(
        "Purchase created: %s (Invoice: %s)",
        new_purchase.id,
        new_purchase.invoice_number,
    )
    return get_purchase(uow, new_purchase.id)


def get_purchase(uow: AbstractUnitOfWork, purchase_id: UUID):
    """
    Retrieve a purchase by ID with items eagerly loaded.

    Args:
        uow: Unit of Work for database access.
        purchase_id: UUID of the purchase.

    Returns:
        Purchase | None: The purchase record with items, or None.
    """
    return (
        uow.session.query(Purchase)
        .options(joinedload(Purchase.items))
        .filter(Purchase.id == purchase_id)
        .first()
    )


def list_purchases(uow: AbstractUnitOfWork, limit: int = 50, offset: int = 0):
    """
    List all purchases with pagination.

    Args:
        uow: Unit of Work.
        limit: Max records to return.
        offset: Records to skip.

    Returns:
        List[Purchase]: Matching purchase records.
    """
    return (
        uow.session.query(Purchase)
        .options(joinedload(Purchase.items))
        .order_by(Purchase.created_at.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )


def confirm_purchase(
    uow: AbstractUnitOfWork,
    purchase_id: UUID,
    confirmed_by: UUID,
) -> Purchase:
    """
    Confirm a purchase and update raw material stock atomically.

    Workflow:
      1. Validate purchase exists and is in 'pending' state.
      2. For each purchase_item with a raw_material_id:
           → call add_raw_material_stock() which increments stock
             and logs an inventory_movement (raw_in).
      3. If purchase is a tax_invoice and is_itc_eligible:
           → log ITC placeholder (basic — full ITC module is a future task).
      4. Mark purchase.status = 'confirmed'.

    All operations are atomic within a single DB transaction.

    Args:
        uow: Unit of Work.
        purchase_id: UUID of the purchase to confirm.
        confirmed_by: UUID of the authenticated user confirming.

    Returns:
        Purchase: Updated purchase with 'confirmed' status.

    Raises:
        HTTPException 404: If purchase not found.
        HTTPException 400: If already confirmed or cancelled.
        HTTPException 400: If a purchase item references an unknown material.
    """
    with uow:
        purchase = (
            uow.session.query(Purchase)
            .filter(Purchase.id == purchase_id)
            .with_for_update()
            .first()
        )

        if not purchase:
            raise HTTPException(
                status_code=404, detail=f"Purchase {purchase_id} not found"
            )

        if purchase.status == "confirmed":
            raise HTTPException(status_code=400, detail="Purchase is already confirmed")

        if purchase.status == "cancelled":
            raise HTTPException(
                status_code=400, detail="Cannot confirm a cancelled purchase"
            )

        # Process each line item and update stock
        for line in purchase.items:
            if line.raw_material_id:
                qty = Decimal(str(line.quantity))
                cost = Decimal(str(line.unit_price)) if line.unit_price else None

                logger.info(
                    "confirm_purchase | purchase=%s | material=%s | qty=%s",
                    purchase_id,
                    line.raw_material_id,
                    qty,
                )

                inventory_service.add_raw_material_stock(
                    uow=uow,
                    material_id=line.raw_material_id,
                    quantity=qty,
                    unit_cost=cost,
                    reference_type="purchase",
                    reference_id=purchase_id,
                    created_by=confirmed_by,
                    notes=f"Purchase confirmation: {purchase.invoice_number or purchase_id}",
                )

        # ITC placeholder — log to logger for now; full ITC table is a future sprint
        if purchase.purchase_type == "tax_invoice" and purchase.is_itc_eligible:
            logger.info(
                "ITC_ELIGIBLE | purchase=%s | tax_total=%s | supplier=%s",
                purchase_id,
                purchase.tax_total,
                purchase.supplier_id,
            )
            # TODO: insert into itc_register table when that migration is added

        purchase.status = "confirmed"
        uow.commit()

    logger.info(
        "Purchase %s confirmed by user %s. Stock updated for %d items.",
        purchase_id,
        confirmed_by,
        sum(1 for li in purchase.items if li.raw_material_id),
    )

    return get_purchase(uow, purchase_id)
