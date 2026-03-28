from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db.deps import check_role, get_uow
from app.models.enums import UserRole
from app.repositories.base import AbstractUnitOfWork
from app.schemas.raw_material import (
    RawMaterialCreate,
    RawMaterialResponse,
    RawMaterialUpdate,
)
from app.services import raw_material_service as service

router = APIRouter(prefix="/raw-materials", tags=["Manufacturing - Raw Materials"])


@router.post(
    "/",
    response_model=RawMaterialResponse,
)
def create_raw_material(
    material: RawMaterialCreate,
    current_user=Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Create a new raw material entry.

    RBAC: Restricted to OWNER and MANAGER roles.

    Args:
        material (RawMaterialCreate): Raw material data to create.
        current_user: Authenticated user with proper role.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        RawMaterialResponse: The created raw material.
    """
    return service.create_raw_material(uow, material)


@router.get("/", response_model=List[RawMaterialResponse])
def get_raw_materials(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user=Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    List all raw materials with pagination.

    Args:
        limit (int): Maximum number of materials to return.
        offset (int): Number of materials to skip.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        List[RawMaterialResponse]: List of raw materials.
    """
    return service.get_raw_materials(uow, offset, limit)


@router.get("/{id}", response_model=RawMaterialResponse)
def get_raw_material(
    id: UUID,
    current_user=Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Retrieve details of a specific raw material by ID.

    Args:
        id (UUID): Unique ID of the raw material.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        RawMaterialResponse: The requested raw material.

    Raises:
        HTTPException: If raw material is not found.
    """
    db_material = service.get_raw_material(uow, id)
    if not db_material:
        raise HTTPException(status_code=404, detail="Raw material not found")
    return db_material


@router.patch("/{id}", response_model=RawMaterialResponse)
def update_raw_material(
    id: UUID,
    material_update: RawMaterialUpdate,
    current_user=Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Update an existing raw material.

    RBAC: Restricted to OWNER and MANAGER roles.

    Args:
        id (UUID): Unique ID of the raw material.
        material_update (RawMaterialUpdate): Updated material data.
        current_user: Authenticated user with proper role.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        RawMaterialResponse: The updated raw material.

    Raises:
        HTTPException: If raw material is not found.
    """
    db_material = service.update_raw_material(uow, id, material_update)
    if not db_material:
        raise HTTPException(status_code=404, detail="Raw material not found")
    return db_material


@router.delete("/{id}")
def delete_raw_material(
    id: UUID,
    current_user=Depends(check_role([UserRole.OWNER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Delete a raw material.

    RBAC: Restricted to OWNER role.

    Args:
        id (UUID): Unique ID of the raw material to delete.
        current_user: Authenticated user with proper role.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        dict: Success message.

    Raises:
        HTTPException: If raw material is not found.
    """
    success = service.delete_raw_material(uow, id)
    if not success:
        raise HTTPException(status_code=404, detail="Raw material not found")
    return {"message": "Raw material deleted"}
