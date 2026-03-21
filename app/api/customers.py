from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.deps import get_db, get_current_user
from app.schemas.customer import CustomerCreate, CustomerResponse
from app.services import customer_service

router = APIRouter(prefix="/customers", tags=["Customers"])


@router.post("/", response_model=CustomerResponse)
def create_customer(
    customer: CustomerCreate,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return customer_service.create_customer(db, customer)


@router.get("/search", response_model=CustomerResponse)
def search_customer_by_phone(
    phone: str,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    customer = customer_service.get_customer_by_phone(db, phone)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


@router.get("/", response_model=List[CustomerResponse])
def get_customers(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return customer_service.get_customers(db, limit, offset)


@router.get("/{customer_id}", response_model=CustomerResponse)
def get_customer(
    customer_id: UUID,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    customer = customer_service.get_customer_by_id(db, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


@router.put("/{customer_id}", response_model=CustomerResponse)
def update_customer(
    customer_id: UUID,
    customer_data: CustomerCreate,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    customer = customer_service.update_customer(db, customer_id, customer_data)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


@router.delete("/{customer_id}")
def delete_customer(
    customer_id: UUID,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    success = customer_service.delete_customer(db, customer_id)
    if not success:
        raise HTTPException(status_code=404, detail="Customer not found")
    return {"message": "Customer deleted successfully"}
