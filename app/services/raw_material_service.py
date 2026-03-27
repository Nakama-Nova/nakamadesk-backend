from sqlalchemy.orm import Session
from app.models.raw_material import RawMaterial
from app.models.price_history import RawMaterialPriceHistory
from app.models.bom import BillOfMaterials
from app.schemas.raw_material import RawMaterialCreate, RawMaterialUpdate
from app.services.bom_service import update_item_production_cost
from uuid import UUID


def create_raw_material(db: Session, material: RawMaterialCreate) -> RawMaterial:
    """
    Create a new raw material record and initialize its price history.

    Args:
        db (Session): Database session.
        material (RawMaterialCreate): Payload containing material name, unit, and price.

    Returns:
        RawMaterial: The newly created material object.
    """
    db_material = RawMaterial(**material.model_dump())
    db.add(db_material)
    db.commit()
    db.refresh(db_material)

    # Initial price history
    history = RawMaterialPriceHistory(
        material_id=db_material.id, price=db_material.current_price, source="MANUAL"
    )
    db.add(history)
    db.commit()

    return db_material


def get_raw_materials(db: Session, skip: int = 0, limit: int = 100):
    """
    Retrieve a list of all raw materials with pagination.

    Args:
        db (Session): Database session.
        skip (int): Number of records to skip.
        limit (int): Maximum number of records to return.

    Returns:
        List[RawMaterial]: List of raw material records.
    """
    return db.query(RawMaterial).offset(skip).limit(limit).all()


def get_raw_material(db: Session, material_id: UUID) -> RawMaterial:
    """
    Retrieve a single raw material by its unique identifier.

    Args:
        db (Session): Database session.
        material_id (UUID): Unique ID of the raw material.

    Returns:
        RawMaterial: The material record, or None if not found.
    """
    return db.query(RawMaterial).filter(RawMaterial.id == material_id).first()


def update_raw_material(
    db: Session, material_id: UUID, material_update: RawMaterialUpdate
) -> RawMaterial:
    """
    Update a raw material's attributes and handle price changes.

    If the price has changed, it records a new entry in price history
    and triggers an update for all items whose production cost depends
    on this material.

    Args:
        db (Session): Database session.
        material_id (UUID): ID of the material to update.
        material_update (RawMaterialUpdate): Updated fields.

    Returns:
        RawMaterial: The updated material object.
    """
    db_material = get_raw_material(db, material_id)
    if not db_material:
        return None

    update_data = material_update.model_dump(exclude_unset=True)

    price_changed = False
    if (
        "current_price" in update_data
        and update_data["current_price"] != db_material.current_price
    ):
        price_changed = True

    for key, value in update_data.items():
        setattr(db_material, key, value)

    db.commit()
    db.refresh(db_material)

    if price_changed:
        history = RawMaterialPriceHistory(
            material_id=db_material.id, price=db_material.current_price, source="MANUAL"
        )
        db.add(history)
        db.commit()

        # Update all items that use this material
        affected_items = (
            db.query(BillOfMaterials.item_id)
            .filter(BillOfMaterials.material_id == material_id)
            .distinct()
            .all()
        )
        for (item_id,) in affected_items:
            update_item_production_cost(db, item_id)

    return db_material


def delete_raw_material(db: Session, material_id: UUID) -> bool:
    """
    Delete a raw material from the system.

    Args:
        db (Session): Database session.
        material_id (UUID): Unique ID of the material to delete.

    Returns:
        bool: True if deleted successfully, False if not found.
    """
    db_material = get_raw_material(db, material_id)
    if not db_material:
        return False
    db.delete(db_material)
    db.commit()
    return True
