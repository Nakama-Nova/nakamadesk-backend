from typing import List, Optional
from uuid import UUID

from app.models.bom import BillOfMaterials
from app.models.price_history import RawMaterialPriceHistory
from app.models.raw_material import RawMaterial
from app.repositories.base import AbstractUnitOfWork
from app.schemas.raw_material import RawMaterialCreate, RawMaterialUpdate


def create_raw_material(
    uow: AbstractUnitOfWork, material: RawMaterialCreate
) -> RawMaterial:
    """
    Create a new raw material record and initialize its price history.

    Args:
        uow (AbstractUnitOfWork): Unit of Work.
        material (RawMaterialCreate): Payload containing material details.

    Returns:
        RawMaterial: The newly created material object.
    """
    with uow:
        db_material = RawMaterial(**material.model_dump())
        uow.raw_materials.add(db_material)
        uow.flush()

        # Initial price history
        history = RawMaterialPriceHistory(
            material_id=db_material.id, price=db_material.current_price, source="MANUAL"
        )
        uow.session.add(history)
        uow.commit()

    uow.refresh(db_material)
    return db_material


def get_raw_materials(
    uow: AbstractUnitOfWork, skip: int = 0, limit: int = 100
) -> List[RawMaterial]:
    """
    Retrieve a list of all raw materials with pagination.

    Args:
        uow (AbstractUnitOfWork): Unit of Work.
        skip (int): Records to skip.
        limit (int): Maximum records to return.

    Returns:
        List[RawMaterial]: List of records.
    """
    return uow.raw_materials.session.query(RawMaterial).offset(skip).limit(limit).all()


def get_raw_material(
    uow: AbstractUnitOfWork, material_id: UUID
) -> Optional[RawMaterial]:
    """
    Retrieve a single raw material by its unique identifier.

    Args:
        uow (AbstractUnitOfWork): Unit of Work.
        material_id (UUID): ID of the material.

    Returns:
        Optional[RawMaterial]: The material record, or None if not found.
    """
    return uow.raw_materials.get_by_id(material_id)


def update_raw_material(
    uow: AbstractUnitOfWork, material_id: UUID, material_update: RawMaterialUpdate
) -> Optional[RawMaterial]:
    """
    Update a raw material's attributes and handle price history tracking.

    Args:
        uow (AbstractUnitOfWork): Unit of Work.
        material_id (UUID): ID of the material.
        material_update (RawMaterialUpdate): Updated fields.

    Returns:
        Optional[RawMaterial]: Updated record.
    """
    from app.services.bom_service import update_item_production_cost

    with uow:
        db_material = uow.raw_materials.get_by_id(material_id)
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

        if price_changed:
            history = RawMaterialPriceHistory(
                material_id=db_material.id,
                price=db_material.current_price,
                source="MANUAL",
            )
            uow.session.add(history)

            # Update all items that use this material
            affected_items = (
                uow.session.query(BillOfMaterials.item_id)
                .filter(BillOfMaterials.material_id == material_id)
                .distinct()
                .all()
            )
            for (item_id,) in affected_items:
                update_item_production_cost(uow, item_id)

        uow.commit()

    uow.refresh(db_material)
    return db_material


def delete_raw_material(uow: AbstractUnitOfWork, material_id: UUID) -> bool:
    """
    Delete a raw material record.

    Args:
        uow (AbstractUnitOfWork): Unit of Work.
        material_id (UUID): ID to delete.

    Returns:
        bool: True if deleted.
    """
    with uow:
        db_material = uow.raw_materials.get_by_id(material_id)
        if not db_material:
            return False
        uow.raw_materials.delete(db_material)
        uow.commit()
    return True
