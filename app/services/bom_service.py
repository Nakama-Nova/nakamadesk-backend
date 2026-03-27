from sqlalchemy.orm import Session
from app.models.bom import BillOfMaterials
from app.models.item import Item
from app.models.raw_material import RawMaterial
from app.schemas.bom import BOMCreate, BOMResponse
from uuid import UUID
from decimal import Decimal


from fastapi import HTTPException


def create_bom_entry(db: Session, bom_entry: BOMCreate) -> BillOfMaterials:
    """
    Create a new Bill of Materials (BOM) entry for an item.

    Validates that the item and material exist before creation.
    Triggers an update of the item's total production cost.

    Args:
        db (Session): Database session.
        bom_entry (BOMCreate): BOM data including item ID, material ID, and quantity.

    Returns:
        BillOfMaterials: The newly created BOM record.

    Raises:
        HTTPException: If the item or material is not found.
    """
    # Validate Item and Material exist
    item = db.query(Item).filter(Item.id == bom_entry.item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    material = (
        db.query(RawMaterial).filter(RawMaterial.id == bom_entry.material_id).first()
    )
    if not material:
        raise HTTPException(status_code=404, detail="Material not found")

    db_entry = BillOfMaterials(**bom_entry.model_dump())
    db.add(db_entry)
    db.commit()
    db.refresh(db_entry)

    # Update item production cost
    update_item_production_cost(db, db_entry.item_id)

    return db_entry


def get_bom_for_item(db: Session, item_id: UUID):
    """
    Retrieve all BOM entries associated with a specific item.

    Args:
        db (Session): Database session.
        item_id (UUID): Unique ID of the item.

    Returns:
        List[BillOfMaterials]: List of BOM entries for the item.
    """
    return db.query(BillOfMaterials).filter(BillOfMaterials.item_id == item_id).all()


def delete_bom_entry(db: Session, bom_id: UUID) -> bool:
    """
    Delete a BOM entry and update the associated item's production cost.

    Args:
        db (Session): Database session.
        bom_id (UUID): Unique ID of the BOM entry to delete.

    Returns:
        bool: True if deleted successfully, False if not found.
    """
    db_entry = db.query(BillOfMaterials).filter(BillOfMaterials.id == bom_id).first()
    if not db_entry:
        return False

    item_id = db_entry.item_id
    db.delete(db_entry)
    db.commit()

    # Update item production cost
    update_item_production_cost(db, item_id)

    return True


def calculate_item_cost(db: Session, item_id: UUID) -> dict:
    """
    Calculate the total material cost for an item based on its BOM.

    Includes calculations for base material price and wastage percentages.

    Args:
        db (Session): Database session.
        item_id (UUID): Unique ID of the item.

    Returns:
        dict: Breakdown of material cost, total cost, and detailed BOM entries.
    """
    from sqlalchemy.orm import joinedload

    # Use JOIN to fetch BOM entries and Material details in one go
    bom_entries = (
        db.query(BillOfMaterials)
        .options(joinedload(BillOfMaterials.material))
        .filter(BillOfMaterials.item_id == item_id)
        .all()
    )

    total_material_cost = Decimal("0.00")
    total_cost_with_wastage = Decimal("0.00")

    detailed_entries = []

    for entry in bom_entries:
        material = entry.material
        if not material:
            continue

        base_cost = Decimal(str(entry.required_qty)) * Decimal(
            str(material.current_price)
        )
        wastage_multiplier = Decimal("1") + (
            Decimal(str(entry.wastage_pct)) / Decimal("100")
        )
        final_cost = base_cost * wastage_multiplier

        total_material_cost += base_cost
        total_cost_with_wastage += final_cost

        detailed_entries.append(
            BOMResponse(
                id=entry.id,
                item_id=entry.item_id,
                material_id=entry.material_id,
                required_qty=entry.required_qty,
                wastage_pct=entry.wastage_pct,
                material_name=material.name,
                material_unit=material.unit,
                material_price=material.current_price,
            )
        )

    return {
        "item_id": item_id,
        "material_cost": total_material_cost.quantize(Decimal("0.01")),
        "total_cost": total_cost_with_wastage.quantize(Decimal("0.01")),
        "entries": detailed_entries,
    }


def update_item_production_cost(db: Session, item_id: UUID):
    """
    Recalculate and update the production cost stored on an item.

    Fetches current BOM data and updates the `production_cost` field of the item.

    Args:
        db (Session): Database session.
        item_id (UUID): Unique ID of the item to update.
    """
    cost_data = calculate_item_cost(db, item_id)
    item = db.query(Item).filter(Item.id == item_id).first()
    if item:
        item.production_cost = cost_data["total_cost"]
        db.commit()
