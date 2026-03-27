from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.deps import check_role, get_db
from app.models.enums import UserRole
from app.schemas.bom import BOMCostResponse, BOMCreate, BOMResponse
from app.services import bom_service as service

router = APIRouter(prefix="/bom", tags=["Manufacturing - BOM"])


@router.post(
    "/",
    response_model=BOMResponse,
    dependencies=[Depends(check_role([UserRole.OWNER, UserRole.ACHARI]))],
)
def create_bom_blueprint(bom_entry: BOMCreate, db: Session = Depends(get_db)):
    """
    Create a new Bill of Materials (BOM) entry.

    RBAC: Restricted to OWNER and ACHARI roles.

    Args:
        bom_entry (BOMCreate): BOM data including items and quantities.
        db (Session): Database session.

    Returns:
        BOMResponse: The created BOM entry.
    """
    return service.create_bom_entry(db, bom_entry)


@router.get(
    "/item/{item_id}",
    response_model=BOMCostResponse,
    dependencies=[
        Depends(check_role([UserRole.OWNER, UserRole.MANAGER, UserRole.ACHARI]))
    ],
)
def get_item_bom(item_id: UUID, db: Session = Depends(get_db)):
    """
    Calculate and retrieve the BOM cost for a specific item.

    RBAC: Restricted to OWNER, MANAGER, and ACHARI roles.

    Args:
        item_id (UUID): Unique ID of the item.
        db (Session): Database session.

    Returns:
        BOMCostResponse: The calculated cost for the item.
    """
    return service.calculate_item_cost(db, item_id)


@router.delete(
    "/{id}", dependencies=[Depends(check_role([UserRole.OWNER, UserRole.ACHARI]))]
)
def delete_bom_entry(id: UUID, db: Session = Depends(get_db)):
    """
    Delete a BOM entry.

    RBAC: Restricted to OWNER and ACHARI roles.

    Args:
        id (UUID): Unique ID of the BOM entry to delete.
        db (Session): Database session.

    Returns:
        dict: Success message.

    Raises:
        HTTPException: If BOM entry is not found.
    """
    success = service.delete_bom_entry(db, id)
    if not success:
        raise HTTPException(status_code=404, detail="BOM entry not found")
    return {"message": "BOM entry deleted"}
