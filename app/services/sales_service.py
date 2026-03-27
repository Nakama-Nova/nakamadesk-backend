import logging
import time
import uuid
from typing import Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, InternalError
from sqlalchemy.orm import joinedload
from sqlalchemy.orm.exc import StaleDataError

from app.db.session import SessionLocal
from app.models.sale import Sale
from app.models.sale_item import SaleItem
from app.repositories.base import AbstractUnitOfWork
from app.repositories.sqlalchemy_repo import SQLAlchemyUnitOfWork
from app.schemas.sale import SaleCreate, SaleItemCreate
from app.services.invoice_service import generate_invoice_number
from app.utils.money import to_decimal

logger = logging.getLogger(__name__)


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

    if not sale_data.items:
        raise HTTPException(
            status_code=400, detail="Sale must contain at least one item"
        )

    # Group requested items for validation
    item_requests: Dict[UUID, SaleItemCreate] = {}
    for item_data in sale_data.items:
        if item_data.quantity <= 0:
            raise HTTPException(
                status_code=400, detail="Quantity must be greater than zero"
            )
        if item_data.item_id in item_requests:
            item_requests[item_data.item_id].quantity += item_data.quantity
        else:
            item_requests[item_data.item_id] = item_data

    max_retries = 5
    base_delay = 0.1

    for attempt in range(max_retries):
        # 1. Fresh session and UoW per attempt - CRITICAL for retry stability
        session = SessionLocal()
        local_uow = SQLAlchemyUnitOfWork(session)

        try:
            # 2. Idempotency Check INSIDE transaction boundary
            if sale_data.client_id:
                existing_sale = local_uow.sales.get_by_client_id_eager(
                    str(sale_data.client_id)
                )
                if existing_sale:
                    logger.info(
                        f"Duplicate sale found for client_id {sale_data.client_id}"
                    )
                    return existing_sale

            # 3. Create Sale object (Wait to link items until they are validated)
            new_sale = Sale(
                id=uuid.uuid4(),
                invoice_number=generate_invoice_number(session),
                customer_id=sale_data.customer_id,
                user_id=current_user_id,
                client_id=sale_data.client_id,
                order_type=sale_data.order_type,
                discount=to_decimal(sale_data.discount),
                payment_method=sale_data.payment_method,
                payment_status=sale_data.payment_status,
                order_status=sale_data.order_status,
            )

            # 4. Atomic Stock update and calculation preparation
            sub_total = to_decimal(0)
            tax_total = to_decimal(0)
            sale_items = []

            for item_id, req_data in item_requests.items():
                # Perform atomic decrement at the database level to prevent lost updates
                result = session.execute(
                    text(
                        "UPDATE items SET current_stock = current_stock - :qty "
                        "WHERE id = :id AND current_stock >= :qty"
                    ),
                    {"qty": req_data.quantity, "id": item_id},
                )

                if result.rowcount == 0:
                    # Conflict or insufficient stock - trigger retry or error
                    item = local_uow.items.get_by_id(item_id)
                    if not item:
                        raise HTTPException(
                            status_code=404, detail=f"Item {item_id} not found"
                        )

                    # If stock is fine but update failed, it's a concurrency conflict
                    if item.current_stock >= req_data.quantity:
                        raise StaleDataError(
                            f"Concurrency conflict on item {item.name}"
                        )

                    raise HTTPException(
                        status_code=400,
                        detail=f"Insufficient stock for item '{item.name}'",
                    )

                # Fetch item details for calculations after locking/updating
                item = local_uow.items.get_by_id(item_id)
                price_at_sale = to_decimal(item.selling_price)
                gst_percent = to_decimal(item.gst_percent)
                quantity = to_decimal(req_data.quantity)

                line_base = price_at_sale * quantity
                line_tax = line_base * (gst_percent / to_decimal(100))

                sub_total += line_base
                tax_total += line_tax

                sale_item = SaleItem(
                    id=uuid.uuid4(),
                    sale_id=new_sale.id,  # Link by ID to avoid accidental relationship triggers during flush
                    item_id=item.id,
                    quantity=req_data.quantity,
                    price_at_sale=price_at_sale,
                    gst_percent=gst_percent,
                    cgst_amount=line_tax / to_decimal(2),
                    sgst_amount=line_tax / to_decimal(2),
                    total_price=line_base + line_tax,
                )
                sale_items.append(sale_item)

            new_sale.sub_total = sub_total
            new_sale.tax_total = tax_total
            new_sale.total_amount = (
                sub_total + tax_total - to_decimal(sale_data.discount)
            )

            # 5. Attach items to relationship BEFORE commit
            # This ensures they are all part of the same flush cycle
            new_sale.items = sale_items

            # 6. Add to session and commit
            local_uow.sales.add(new_sale)
            local_uow.commit()

            # Success - return refreshed object
            return local_uow.sales.get_by_id_eager(new_sale.id)

        except (StaleDataError, InternalError, IntegrityError) as e:
            # Handle potential concurrency issues with rollback and retry
            local_uow.rollback()
            logger.warning(
                f"Transaction conflict on attempt {attempt + 1}/{max_retries}: {str(e)}"
            )
            if attempt == max_retries - 1:
                raise HTTPException(
                    status_code=409,
                    detail="High concurrency error. Please retry later.",
                ) from e

            # Exponential backoff: sleep(2^attempt * base_delay)
            time.sleep(base_delay * (2**attempt))

        except HTTPException:
            local_uow.rollback()
            raise
        except Exception as e:
            local_uow.rollback()
            logger.error(f"Unexpected error in create_sale_transaction: {str(e)}")
            raise
        finally:
            # Always close session to prevent connection leaks
            session.close()


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
    return uow.sales.session.query(SaleItem).filter(SaleItem.sale_id == sale_id).all()
