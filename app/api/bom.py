from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from uuid import UUID

from app.db.deps import get_db, check_role
from app.schemas.bom import BOMCreate, BOMResponse, BOMCostResponse
from app.services import bom_service as service

router = APIRouter(prefix="/bom", tags=["Manufacturing - BOM"])


@router.post(
    "/",
    response_model=BOMResponse,
    dependencies=[Depends(check_role(["owner", "achari"]))],
)
def create_bom_blueprint(bom_entry: BOMCreate, db: Session = Depends(get_db)):
    return service.create_bom_entry(db, bom_entry)


@router.get(
    "/item/{item_id}",
    response_model=BOMCostResponse,
    dependencies=[Depends(check_role(["owner", "manager", "achari"]))],
)
def get_item_bom(item_id: UUID, db: Session = Depends(get_db)):
    return service.calculate_item_cost(db, item_id)


@router.delete("/{id}", dependencies=[Depends(check_role(["owner", "achari"]))])
def delete_bom_entry(id: UUID, db: Session = Depends(get_db)):
    success = service.delete_bom_entry(db, id)
    if not success:
        raise HTTPException(status_code=404, detail="BOM entry not found")
    return {"message": "BOM entry deleted"}
