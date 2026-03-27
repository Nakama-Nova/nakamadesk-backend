from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from uuid import UUID

from app.db.deps import get_db, check_role
from app.models.enums import UserRole
from app.schemas.raw_material import (
    RawMaterialCreate,
    RawMaterialUpdate,
    RawMaterialResponse,
)
from app.services import raw_material_service as service

router = APIRouter(prefix="/raw-materials", tags=["Manufacturing - Raw Materials"])


@router.post(
    "/",
    response_model=RawMaterialResponse,
    dependencies=[Depends(check_role([UserRole.OWNER, UserRole.MANAGER]))],
)
def create_raw_material(material: RawMaterialCreate, db: Session = Depends(get_db)):
    """
    Create a new raw material entry.

    RBAC: Restricted to OWNER and MANAGER roles.

    Args:
        material (RawMaterialCreate): Raw material data to create.
        db (Session): Database session.

    Returns:
        RawMaterialResponse: The created raw material.
    """
    return service.create_raw_material(db, material)


@router.get("/", response_model=List[RawMaterialResponse])
def get_raw_materials(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """
    List all raw materials with pagination.

    Args:
        skip (int): Number of materials to skip.
        limit (int): Maximum number of materials to return.
        db (Session): Database session.

    Returns:
        List[RawMaterialResponse]: List of raw materials.
    """
    return service.get_raw_materials(db, skip, limit)


@router.get("/{id}", response_model=RawMaterialResponse)
def get_raw_material(id: UUID, db: Session = Depends(get_db)):
    """
    Retrieve details of a specific raw material by ID.

    Args:
        id (UUID): Unique ID of the raw material.
        db (Session): Database session.

    Returns:
        RawMaterialResponse: The requested raw material.

    Raises:
        HTTPException: If raw material is not found.
    """
    db_material = service.get_raw_material(db, id)
    if not db_material:
        raise HTTPException(status_code=404, detail="Raw material not found")
    return db_material


@router.patch(
    "/{id}",
    response_model=RawMaterialResponse,
    dependencies=[Depends(check_role([UserRole.OWNER, UserRole.MANAGER]))],
)
def update_raw_material(
    id: UUID, material_update: RawMaterialUpdate, db: Session = Depends(get_db)
):
    """
    Update an existing raw material.

    RBAC: Restricted to OWNER and MANAGER roles.

    Args:
        id (UUID): Unique ID of the raw material.
        material_update (RawMaterialUpdate): Updated material data.
        db (Session): Database session.

    Returns:
        RawMaterialResponse: The updated raw material.

    Raises:
        HTTPException: If raw material is not found.
    """
    db_material = service.update_raw_material(db, id, material_update)
    if not db_material:
        raise HTTPException(status_code=404, detail="Raw material not found")
    return db_material


@router.delete("/{id}", dependencies=[Depends(check_role([UserRole.OWNER]))])
def delete_raw_material(id: UUID, db: Session = Depends(get_db)):
    """
    Delete a raw material.

    RBAC: Restricted to OWNER role.

    Args:
        id (UUID): Unique ID of the raw material to delete.
        db (Session): Database session.

    Returns:
        dict: Success message.

    Raises:
        HTTPException: If raw material is not found.
    """
    success = service.delete_raw_material(db, id)
    if not success:
        raise HTTPException(status_code=404, detail="Raw material not found")
    return {"message": "Raw material deleted"}
