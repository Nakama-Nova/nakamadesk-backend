from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.deps import get_db, get_current_user, check_role
from app.models.enums import UserRole
from app.models.customer import Customer
from app.schemas.customer import CustomerCreate, CustomerResponse

router = APIRouter(prefix="/customers", tags=["Customers"])


@router.post("/", response_model=CustomerResponse)
def create_customer(
    customer: CustomerCreate,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    existing_phone = db.query(Customer).filter(Customer.phone == customer.phone).first()
    if existing_phone:
        raise HTTPException(status_code=400, detail="Phone already exists")

    if customer.email:
        existing_email = (
            db.query(Customer).filter(Customer.email == customer.email).first()
        )
        if existing_email:
            raise HTTPException(status_code=400, detail="Email already exists")

    new_customer = Customer(**customer.model_dump())
    db.add(new_customer)
    db.commit()
    db.refresh(new_customer)
    return new_customer


@router.get("/search", response_model=CustomerResponse)
def search_customer_by_phone(
    phone: str,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    customer = db.query(Customer).filter(Customer.phone == phone).first()
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
    return db.query(Customer).limit(limit).offset(offset).all()


@router.get("/{customer_id}", response_model=CustomerResponse)
def get_customer(
    customer_id: UUID,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
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
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    update_data = customer_data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(customer, key, value)

    db.commit()
    db.refresh(customer)
    return customer


@router.delete("/{customer_id}")
def delete_customer(
    customer_id: UUID,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    db.delete(customer)
    db.commit()
    return {"message": "Customer deleted successfully"}
