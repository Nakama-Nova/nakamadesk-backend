from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from uuid import UUID

from app.db.deps import get_db, check_role
from app.schemas.raw_material import RawMaterialCreate, RawMaterialUpdate, RawMaterialResponse
from app.services import raw_material_service as service

router = APIRouter(prefix="/raw-materials", tags=["Manufacturing - Raw Materials"])


@router.post("/", response_model=RawMaterialResponse, dependencies=[Depends(check_role(["owner", "manager"]))])
def create_raw_material(material: RawMaterialCreate, db: Session = Depends(get_db)):
    return service.create_raw_material(db, material)


@router.get("/", response_model=List[RawMaterialResponse])
def get_raw_materials(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return service.get_raw_materials(db, skip, limit)


@router.get("/{id}", response_model=RawMaterialResponse)
def get_raw_material(id: UUID, db: Session = Depends(get_db)):
    db_material = service.get_raw_material(db, id)
    if not db_material:
        raise HTTPException(status_code=404, detail="Raw material not found")
    return db_material


@router.patch("/{id}", response_model=RawMaterialResponse, dependencies=[Depends(check_role(["owner", "manager"]))])
def update_raw_material(id: UUID, material_update: RawMaterialUpdate, db: Session = Depends(get_db)):
    db_material = service.update_raw_material(db, id, material_update)
    if not db_material:
        raise HTTPException(status_code=404, detail="Raw material not found")
    return db_material


@router.delete("/{id}", dependencies=[Depends(check_role(["owner"]))])
def delete_raw_material(id: UUID, db: Session = Depends(get_db)):
    success = service.delete_raw_material(db, id)
    if not success:
        raise HTTPException(status_code=404, detail="Raw material not found")
    return {"message": "Raw material deleted"}
