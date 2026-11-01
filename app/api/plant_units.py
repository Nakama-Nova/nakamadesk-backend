from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError

from app.db.deps import check_role, get_current_user, get_uow
from app.models.enums import UserRole
from app.models.plant_unit import PlantUnit
from app.models.user import User
from app.repositories.base import AbstractUnitOfWork
from app.schemas.plant_unit import PlantUnitIn, PlantUnitOut

router = APIRouter(prefix="/plant-units", tags=["Plant units"])


@router.get("/", response_model=List[PlantUnitOut])
def list_plant_units(
    _: User = Depends(get_current_user),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """Every plant unit, for any signed-in user (the phone app needs the list)."""
    return uow.session.query(PlantUnit).order_by(PlantUnit.code).all()


def _save(uow: AbstractUnitOfWork, row: PlantUnit) -> PlantUnit:
    try:
        uow.session.add(row)
        uow.commit()
    except IntegrityError:
        uow.rollback()
        raise HTTPException(
            status_code=409, detail="A plant unit with that name or code already exists"
        ) from None
    uow.session.refresh(row)
    return row


@router.post("/", response_model=PlantUnitOut, status_code=201)
def create_plant_unit(
    body: PlantUnitIn,
    _: User = Depends(check_role([UserRole.OWNER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """Owner only."""
    return _save(uow, PlantUnit(**body.model_dump()))


@router.put("/{unit_id}", response_model=PlantUnitOut)
def update_plant_unit(
    unit_id: UUID,
    body: PlantUnitIn,
    _: User = Depends(check_role([UserRole.OWNER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """Owner only. Switch a unit off with is_active=false; units are never deleted."""
    row = uow.session.get(PlantUnit, unit_id)
    if not row:
        raise HTTPException(status_code=404, detail="Plant unit not found")
    for k, v in body.model_dump().items():
        setattr(row, k, v)
    return _save(uow, row)
