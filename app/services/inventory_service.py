"""
Inventory Movement Engine — Core service for Vriksha Studio Day 2.

All stock changes MUST flow through this module. Never update
raw_material.stock or item.current_stock directly from other services.
This keeps the audit trail complete and business rules centralised.

Three entry-points:
  - add_raw_material_stock()   → purchase confirmation path
  - consume_raw_material()     → production job start path
  - add_finished_goods()       → production job complete path

Existing CRUD functions (get_item, create_item, etc.) are kept below
the Movement Engine section — do NOT remove them.
"""

import logging
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException

from app.models.inventory_movement import InventoryMovement
from app.models.item import Item
from app.models.raw_material import RawMaterial
from app.repositories.base import AbstractUnitOfWork
from app.schemas.item import ItemCreate, ItemUpdate
from app.schemas.stock import StockUpdate

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# INVENTORY MOVEMENT ENGINE (Day 2 additions)
# ---------------------------------------------------------------------------


def add_raw_material_stock(
    uow: AbstractUnitOfWork,
    material_id: UUID,
    quantity: Decimal,
    unit_cost: Optional[Decimal],
    reference_type: str,
    reference_id: UUID,
    created_by: Optional[UUID] = None,
    notes: Optional[str] = None,
) -> RawMaterial:
    """
    Increase raw material stock and log the movement.

    Called by: purchase_service.confirm_purchase()

    Args:
        uow: Active Unit of Work (caller manages the transaction boundary).
        material_id: UUID of the raw material to stock.
        quantity: Positive quantity to add.
        unit_cost: Cost per unit at time of receipt.
        reference_type: Source type, e.g. 'purchase'.
        reference_id: UUID of the source record.
        created_by: UUID of the user triggering the movement.
        notes: Optional human-readable note.

    Returns:
        RawMaterial: Updated material with new stock level.

    Raises:
        HTTPException 404: If material not found.
        HTTPException 400: If quantity is not positive.
    """
    if quantity <= 0:
        raise HTTPException(
            status_code=400, detail="quantity must be positive for stock addition"
        )

    # Pessimistic lock — prevents concurrent lost updates
    material = (
        uow.session.query(RawMaterial)
        .filter(RawMaterial.id == material_id)
        .with_for_update()
        .first()
    )
    if not material:
        raise HTTPException(
            status_code=404, detail=f"Raw material {material_id} not found"
        )

    stock_before = material.stock or Decimal("0")
    material.stock = stock_before + quantity

    movement = InventoryMovement(
        movement_type="raw_in",
        reference_type=reference_type,
        reference_id=reference_id,
        raw_material_id=material_id,
        quantity=quantity,
        unit_cost=unit_cost,
        notes=notes or f"Stock receipt via {reference_type}",
        created_by=created_by,
    )
    uow.session.add(movement)

    logger.info(
        "raw_in | material=%s | %.4f (was %.4f → now %.4f) | ref=%s:%s",
        material_id,
        quantity,
        stock_before,
        material.stock,
        reference_type,
        reference_id,
    )
    return material


def consume_raw_material(
    uow: AbstractUnitOfWork,
    material_id: UUID,
    quantity: Decimal,
    job_id: UUID,
    created_by: Optional[UUID] = None,
) -> RawMaterial:
    """
    Deduct raw material stock and log the consumption movement.

    Called by: production_service.start_job()

    Args:
        uow: Active Unit of Work (caller manages the transaction boundary).
        material_id: UUID of the raw material to consume.
        quantity: Positive quantity to consume (stored as negative movement).
        job_id: UUID of the production job consuming the material.
        created_by: UUID of the user triggering the movement.

    Returns:
        RawMaterial: Updated material with reduced stock.

    Raises:
        HTTPException 404: If material not found.
        HTTPException 400: If insufficient stock.
    """
    if quantity <= 0:
        raise HTTPException(
            status_code=400, detail="Consumption quantity must be positive"
        )

    # Pessimistic lock — prevents concurrent over-deduction
    material = (
        uow.session.query(RawMaterial)
        .filter(RawMaterial.id == material_id)
        .with_for_update()
        .first()
    )
    if not material:
        raise HTTPException(
            status_code=404, detail=f"Raw material {material_id} not found"
        )

    stock_before = material.stock or Decimal("0")
    if stock_before < quantity:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Insufficient stock for material '{material.name}'. "
                f"Required: {quantity}, Available: {stock_before}"
            ),
        )

    material.stock = stock_before - quantity

    movement = InventoryMovement(
        movement_type="raw_consumed",
        reference_type="production_job",
        reference_id=job_id,
        raw_material_id=material_id,
        quantity=-quantity,  # Negative = stock going out
        unit_cost=material.current_price,
        notes="Consumed by production job",
        created_by=created_by,
    )
    uow.session.add(movement)

    logger.info(
        "raw_consumed | material=%s | %.4f (was %.4f → now %.4f) | job=%s",
        material_id,
        quantity,
        stock_before,
        material.stock,
        job_id,
    )
    return material


def add_finished_goods(
    uow: AbstractUnitOfWork,
    item_id: UUID,
    quantity: int,
    job_id: UUID,
    unit_cost: Optional[Decimal] = None,
    created_by: Optional[UUID] = None,
) -> Item:
    """
    Increment finished goods stock and log the movement.

    Called by: production_service.complete_job()

    Args:
        uow: Active Unit of Work (caller manages the transaction boundary).
        item_id: UUID of the catalogue item produced.
        quantity: Number of units completed.
        job_id: UUID of the production job that produced them.
        unit_cost: Optional computed production cost per unit.
        created_by: UUID of the user triggering the movement.

    Returns:
        Item: Updated item with increased current_stock.

    Raises:
        HTTPException 404: If item not found.
        HTTPException 400: If quantity is not positive.
    """
    if quantity <= 0:
        raise HTTPException(
            status_code=400, detail="Produced quantity must be positive"
        )

    # Optimistic locking handled by SQLAlchemy mapper, but we still fetch
    item = uow.items.get_by_id(item_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")

    stock_before = item.current_stock or 0
    item.current_stock = stock_before + quantity

    movement = InventoryMovement(
        movement_type="finished_in",
        reference_type="production_job",
        reference_id=job_id,
        item_id=item_id,
        quantity=quantity,
        unit_cost=unit_cost,
        notes="Finished goods added from production",
        created_by=created_by,
    )
    uow.session.add(movement)

    logger.info(
        "finished_in | item=%s | %d units (was %d → now %d) | job=%s",
        item_id,
        quantity,
        stock_before,
        item.current_stock,
        job_id,
    )
    return item


def remove_finished_goods(
    uow: AbstractUnitOfWork,
    item_id: UUID,
    quantity: int,
    sale_id: UUID,
    created_by: Optional[UUID] = None,
) -> Item:
    """
    Deduct finished goods stock (sales path) and log the movement.

    Called by: sales_service.create_sale_transaction()

    Args:
        uow: Unit of Work.
        item_id: UUID of the item sold.
        quantity: Positive number of units sold.
        sale_id: UUID of the sale record.
        created_by: UUID of the user.

    Returns:
        Item: Updated item record.

    Raises:
        HTTPException 400: If insufficient stock.
    """
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="Sold quantity must be positive")

    # Item uses version_id for optimistic locking
    item = uow.items.get_by_id(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    stock_before = item.current_stock or 0
    if stock_before < quantity:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient stock for '{item.name}'. Available: {stock_before}",
        )

    item.current_stock = stock_before - quantity

    movement = InventoryMovement(
        movement_type="finished_out",
        reference_type="sale",
        reference_id=sale_id,
        item_id=item_id,
        quantity=-quantity,
        unit_cost=item.selling_price,
        notes="Deducted for sale",
        created_by=created_by,
    )
    uow.session.add(movement)

    logger.info(
        "finished_out | item=%s | %d units (was %d → now %d) | sale=%s",
        item_id,
        quantity,
        stock_before,
        item.current_stock,
        sale_id,
    )
    return item


# ---------------------------------------------------------------------------
# EXISTING ITEM CRUD (unchanged from Day 1)
# ---------------------------------------------------------------------------


def get_items(uow: AbstractUnitOfWork, limit: int = 50, offset: int = 0) -> List[Item]:
    """
    Retrieve a list of inventory items with pagination.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database access.
        limit (int): Maximum number of items to return.
        offset (int): Number of records to skip.

    Returns:
        List[Item]: List of item records.
    """
    return uow.items.session.query(Item).limit(limit).offset(offset).all()


def get_item(uow: AbstractUnitOfWork, item_id: UUID) -> Optional[Item]:
    """
    Retrieve a single inventory item by its unique ID.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database access.
        item_id (UUID): Unique ID of the item.

    Returns:
        Optional[Item]: The item record if found, else None.
    """
    return uow.items.get_by_id(item_id)


def create_item(uow: AbstractUnitOfWork, item_data: ItemCreate) -> Item:
    """
    Create a new inventory item and persist it to the database.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database operations.
        item_data (ItemCreate): Payload containing item details (SKU, name, etc.).

    Returns:
        Item: The newly created item object.
    """
    new_item = Item(**item_data.model_dump())
    with uow:
        uow.items.add(new_item)
        uow.commit()
    uow.refresh(new_item)
    logger.info(f"Item created: {new_item.sku} (ID: {new_item.id})")
    return new_item


def update_item(
    uow: AbstractUnitOfWork, item_id: UUID, item_data: ItemUpdate
) -> Optional[Item]:
    """
    Update an existing inventory item's attributes.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database operations.
        item_id (UUID): Unique ID of the item to update.
        item_data (ItemUpdate): Updated data fields.

    Returns:
        Optional[Item]: The updated item record, or None if not found.
    """
    with uow:
        item = uow.items.get_by_id(item_id)
        if not item:
            return None

        update_dict = item_data.model_dump(exclude_unset=True)
        for key, value in update_dict.items():
            setattr(item, key, value)

        uow.commit()
    uow.refresh(item)
    logger.info(f"Item updated: {item.sku} (ID: {item.id})")
    return item


def delete_item(uow: AbstractUnitOfWork, item_id: UUID) -> bool:
    """
    Delete an inventory item from the system.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database operations.
        item_id (UUID): Unique ID of the item to delete.

    Returns:
        bool: True if deleted successfully, False if not found.
    """
    with uow:
        item = uow.items.get_by_id(item_id)
        if not item:
            return False

        uow.items.delete(item)
        uow.commit()
    logger.info(f"Item deleted: {item_id}")
    return True


def update_item_stock(
    uow: AbstractUnitOfWork,
    item_id: UUID,
    stock_update: StockUpdate,
    created_by: Optional[UUID] = None,
) -> Item:
    """
    Adjust the current stock level for an inventory item.

    Supports positive (addition) and negative (deduction) adjustments.
    Ensures that stock level does not fall below zero.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database operations.
        item_id (UUID): Unique ID of the item.
        stock_update (StockUpdate): Data containing the adjustment quantity.
        created_by (Optional[UUID]): User making the adjustment.

    Returns:
        Item: The updated item with the new stock level.

    Raises:
        HTTPException: If the item is not found or resulting stock would be negative.
    """
    with uow:
        # Fetch with lock (optimistic locking handled by mapper)
        item = uow.items.get_by_id(item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")

        stock_before = item.current_stock or 0
        new_stock = stock_before + stock_update.quantity
        if new_stock < 0:
            raise HTTPException(status_code=400, detail="Stock cannot go below zero")

        item.current_stock = new_stock

        movement = InventoryMovement(
            movement_type="adjustment",
            reference_type="manual",
            item_id=item_id,
            quantity=stock_update.quantity,
            unit_cost=item.selling_price,
            notes=f"Manual adjustment: {stock_update.notes or 'No reason provided'}",
            created_by=created_by,
        )
        uow.session.add(movement)
        uow.commit()

    uow.refresh(item)
    logger.info(
        "adjustment | item=%s | %+d (was %d → now %d)",
        item_id,
        stock_update.quantity,
        stock_before,
        new_stock,
    )
    return item


def record_raw_material_adjustment(
    uow: AbstractUnitOfWork,
    material_id: UUID,
    quantity_change: Decimal,
    created_by: Optional[UUID] = None,
    notes: Optional[str] = None,
) -> RawMaterial:
    """
    Record a manual adjustment for a raw material.

    Args:
        uow: Unit of Work.
        material_id: Material UUID.
        quantity_change: Positive or negative change.
        created_by: User UUID.
        notes: Reason.

    Returns:
        RawMaterial: Updated material.
    """
    # Use pessimistic lock for raw materials
    material = (
        uow.session.query(RawMaterial)
        .filter(RawMaterial.id == material_id)
        .with_for_update()
        .first()
    )
    if not material:
        raise HTTPException(status_code=404, detail="Raw material not found")

    stock_before = material.stock or Decimal("0")
    new_stock = stock_before + quantity_change
    if new_stock < 0:
        raise HTTPException(status_code=400, detail="Stock cannot go below zero")

    material.stock = new_stock

    movement = InventoryMovement(
        movement_type="adjustment",
        reference_type="manual",
        raw_material_id=material_id,
        quantity=quantity_change,
        unit_cost=material.current_price,
        notes=notes or "Manual adjustment",
        created_by=created_by,
    )
    uow.session.add(movement)

    logger.info(
        "adjustment | material=%s | %+.4f (was %.4f → now %.4f)",
        material_id,
        quantity_change,
        stock_before,
        new_stock,
    )
    return material
