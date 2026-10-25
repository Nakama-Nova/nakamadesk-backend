import logging
import time
import uuid
from typing import Dict, List, Optional, Tuple
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError, InternalError
from sqlalchemy.orm import joinedload
from sqlalchemy.orm.exc import StaleDataError

from app.models.sale import Sale
from app.models.sale_item import SaleItem
from app.repositories.base import AbstractUnitOfWork
from app.schemas.sale import SaleCreate
from app.services import inventory_service
from app.services.invoice_service import generate_invoice_number
from app.utils.money import to_decimal

logger = logging.getLogger(__name__)


def build_sale(
    uow: AbstractUnitOfWork,
    sale_data: SaleCreate,
    current_user_id: UUID,
    allow_negative_stock: bool = False,
    sale_id: Optional[UUID] = None,
) -> Tuple[Sale, bool]:
    """
    Build a sale and apply its stock movements WITHOUT committing.

    Shared by the REST path (create_sale_transaction, which commits and
    retries) and the offline sync path (which runs inside a nested
    transaction and is committed by the sync dispatcher).

    Args:
        uow: Unit of Work for database operations.
        sale_data: Items, discount and payment info.
        current_user_id: User creating the sale.
        allow_negative_stock: Record the sale even if stock is short (an
            offline counter sale has already handed the goods over).
        sale_id: Keep a client-chosen sale id (offline sales), else generate one.

    Returns:
        (sale, created). created is False when `client_id` matched an
        existing sale (idempotent replay): nothing was added.

    Raises:
        HTTPException: If the sale is empty, a quantity is invalid, an item is
            missing, or stock is short (unless allow_negative_stock).
    """
    if not sale_data.items:
        raise HTTPException(
            status_code=400, detail="Sale must contain at least one item"
        )

    # Total requested quantity per item. Built fresh on every call so a
    # retry can never double-count.
    item_requests: Dict[UUID, int] = {}
    for item_data in sale_data.items:
        if item_data.quantity <= 0:
            raise HTTPException(
                status_code=400, detail="Quantity must be greater than zero"
            )
        item_requests[item_data.item_id] = (
            item_requests.get(item_data.item_id, 0) + item_data.quantity
        )

    # 2. Idempotency Check INSIDE transaction boundary
    if sale_data.client_id:
        existing_sale = uow.sales.get_by_client_id_eager(str(sale_data.client_id))
        if existing_sale:
            logger.info(f"Duplicate sale found for client_id {sale_data.client_id}")
            return existing_sale, False

    # 3. Create Sale object (Wait to link items until they are validated)
    new_sale = Sale(
        id=sale_id or uuid.uuid4(),
        invoice_number=generate_invoice_number(uow.session),
        customer_id=sale_data.customer_id,
        user_id=current_user_id,
        client_id=sale_data.client_id,
        order_type=sale_data.order_type,
        discount=to_decimal(sale_data.discount),
        payment_method=sale_data.payment_method,
        payment_status=sale_data.payment_status,
        order_status=sale_data.order_status,
    )

    # 4. Stock update (via movement engine) and calculation preparation
    sub_total = to_decimal(0)
    tax_total = to_decimal(0)
    sale_items = []

    for item_id, qty in item_requests.items():
        # Deduction via inventory_service — creates 'finished_out' movement
        # If stock insufficient, raises 400. If concurrent update, uow.commit()
        # will raise StaleDataError, which is caught and retried below.
        item = inventory_service.remove_finished_goods(
            uow=uow,
            item_id=item_id,
            quantity=qty,
            sale_id=new_sale.id,
            created_by=current_user_id,
            allow_negative=allow_negative_stock,
        )

        # Calculations using the fetched item
        price_at_sale = to_decimal(item.selling_price)
        gst_percent = to_decimal(item.gst_percent)
        quantity = to_decimal(qty)

        line_base = price_at_sale * quantity
        line_tax = line_base * (gst_percent / to_decimal(100))

        sub_total += line_base
        tax_total += line_tax

        sale_item = SaleItem(
            id=uuid.uuid4(),
            sale_id=new_sale.id,
            item_id=item.id,
            quantity=qty,
            price_at_sale=price_at_sale,
            gst_percent=gst_percent,
            cgst_amount=line_tax / to_decimal(2),
            sgst_amount=line_tax / to_decimal(2),
            total_price=line_base + line_tax,
        )
        sale_items.append(sale_item)

    new_sale.sub_total = sub_total
    new_sale.tax_total = tax_total
    new_sale.total_amount = sub_total + tax_total - to_decimal(sale_data.discount)

    # 5. Attach items and persist
    new_sale.items = sale_items
    uow.sales.add(new_sale)

    return new_sale, True


def create_sale_transaction(
    uow: AbstractUnitOfWork, sale_data: SaleCreate, current_user_id: UUID
) -> Sale:
    """
    Execute a complex sale transaction with concurrency protection.

    This method performs the following:
    1. Validates quantities and items.
    2. Checks for idempotency using `client_id`.
    3. Performs atomic database-level stock decrements with retries.
    4. Calculates line totals, taxes (CGST/SGST), and grand totals.
    5. Persists the sale and its items.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database operations.
        sale_data (SaleCreate): Payload including items, discount, and payment info.
        current_user_id (UUID): ID of the user creating the sale.

    Returns:
        Sale: The fully created and refreshed sale object.

    Raises:
        HTTPException: If stock is insufficient, items are missing, or a conflict persists.
    """

    max_retries = 5
    base_delay = 0.1

    for attempt in range(max_retries):
        try:
            sale, created = build_sale(uow, sale_data, current_user_id)
            if not created:
                return sale
            uow.commit()

            # Success - return refreshed object
            return uow.sales.get_by_id_eager(sale.id)

        except (StaleDataError, InternalError, IntegrityError) as e:
            # Handle potential concurrency issues with rollback and retry
            uow.rollback()
            logger.warning(
                f"Transaction conflict on attempt {attempt + 1}/{max_retries}: {str(e)}"
            )

            # If this was a UniqueViolation on client_id, a concurrent thread already
            # created the sale. Re-query immediately to honor idempotency.
            if sale_data.client_id and isinstance(e, IntegrityError):
                try:
                    existing_sale = uow.sales.get_by_client_id_eager(
                        str(sale_data.client_id)
                    )
                    if existing_sale:
                        return existing_sale
                except Exception:
                    pass  # Session might still be dirty; fall through to retry

            if attempt == max_retries - 1:
                raise HTTPException(
                    status_code=409,
                    detail="High concurrency error after multiple retries. Please try again.",
                ) from e

            # Exponential backoff
            time.sleep(base_delay * (2**attempt))

        except HTTPException:
            uow.rollback()
            raise
        except Exception as e:
            uow.rollback()
            logger.error(f"Unexpected error in create_sale_transaction: {str(e)}")
            raise


def get_all_sales(
    uow: AbstractUnitOfWork,
    limit: int = 50,
    offset: int = 0,
    customer_id: Optional[UUID] = None,
    date: Optional[str] = None,
) -> List[Sale]:
    """
    Retrieve a list of sales with optional filtering and pagination.

    Loads related items and item details efficiently (eager loading).

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database access.
        limit (int): Maximum records to return.
        offset (int): Records to skip.
        customer_id (Optional[UUID]): Filter by customer.
        date (Optional[str]): Filter by date (YYYY-MM-DD).

    Returns:
        List[Sale]: List of sale records.
    """
    query = uow.sales.session.query(Sale).options(
        joinedload(Sale.items).joinedload(SaleItem.item)
    )

    if customer_id:
        query = query.filter(Sale.customer_id == customer_id)

    if date:
        from datetime import datetime

        from sqlalchemy import func

        try:
            target_date = datetime.strptime(date, "%Y-%m-%d").date()
            query = query.filter(func.date(Sale.created_at) == target_date)
        except ValueError:
            pass

    sales = query.limit(limit).offset(offset).all()
    return sales


def get_sale_by_id(uow: AbstractUnitOfWork, sale_id: UUID) -> Optional[Sale]:
    """
    Retrieve a specific sale by its unique ID, including all line items.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database access.
        sale_id (UUID): Unique identifier of the sale.

    Returns:
        Optional[Sale]: The sale object with items, or None if not found.
    """
    sale = (
        uow.sales.session.query(Sale)
        .options(joinedload(Sale.items).joinedload(SaleItem.item))
        .filter(Sale.id == sale_id)
        .first()
    )
    return sale


def get_sale_items_by_id(uow: AbstractUnitOfWork, sale_id: UUID) -> List[SaleItem]:
    """
    Retrieve only the line items associated with a specific sale.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database access.
        sale_id (UUID): Unique identifier of the sale.

    Returns:
        List[SaleItem]: List of items in the sale.
    """
    return (
        uow.sales.session.query(SaleItem)
        .options(joinedload(SaleItem.item))
        .filter(SaleItem.sale_id == sale_id)
        .all()
    )
