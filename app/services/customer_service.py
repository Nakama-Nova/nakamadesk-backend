from typing import List, Optional
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.models.customer import Customer
from app.schemas.customer import CustomerCreate

import logging

logger = logging.getLogger(__name__)


def get_customers(db: Session, limit: int = 50, offset: int = 0) -> List[Customer]:
    """Retrieve all customers with pagination."""
    return db.query(Customer).limit(limit).offset(offset).all()


def get_customer_by_id(db: Session, customer_id: UUID) -> Optional[Customer]:
    """Retrieve a single customer by ID."""
    return db.query(Customer).filter(Customer.id == customer_id).first()


def get_customer_by_phone(db: Session, phone: str) -> Optional[Customer]:
    """Search for a customer by phone number."""
    return db.query(Customer).filter(Customer.phone == phone).first()


def create_customer(db: Session, customer_data: CustomerCreate) -> Customer:
    """Create a new customer with existence checks."""
    if customer_data.phone:
        existing_phone = get_customer_by_phone(db, customer_data.phone)
        if existing_phone:
            raise HTTPException(status_code=400, detail="Phone already exists")

    if customer_data.email:
        existing_email = (
            db.query(Customer).filter(Customer.email == customer_data.email).first()
        )
        if existing_email:
            raise HTTPException(status_code=400, detail="Email already exists")

    new_customer = Customer(**customer_data.model_dump())
    db.add(new_customer)
    db.commit()
    db.refresh(new_customer)
    logger.info(f"Customer created: {new_customer.name} (ID: {new_customer.id})")
    return new_customer


def update_customer(
    db: Session, customer_id: UUID, customer_data: CustomerCreate
) -> Optional[Customer]:
    """Update an existing customer."""
    customer = get_customer_by_id(db, customer_id)
    if not customer:
        return None

    update_dict = customer_data.model_dump(exclude_unset=True)
    for key, value in update_dict.items():
        setattr(customer, key, value)

    db.commit()
    db.refresh(customer)
    logger.info(f"Customer updated: {customer.name} (ID: {customer.id})")
    return customer


def delete_customer(db: Session, customer_id: UUID) -> bool:
    """Delete a customer."""
    customer = get_customer_by_id(db, customer_id)
    if not customer:
        return False

    db.delete(customer)
    db.commit()
    logger.info(f"Customer deleted: {customer_id}")
    return True
