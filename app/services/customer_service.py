from typing import List, Optional
from uuid import UUID
from fastapi import HTTPException
from app.repositories.base import AbstractUnitOfWork
from app.models.customer import Customer
from app.schemas.customer import CustomerCreate

import logging

logger = logging.getLogger(__name__)


def get_customers(
    uow: AbstractUnitOfWork, limit: int = 50, offset: int = 0
) -> List[Customer]:
    """
    Retrieve a list of customers with pagination.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database access.
        limit (int): Maximum number of customers to return.
        offset (int): Number of records to skip.

    Returns:
        List[Customer]: List of customer records.
    """
    return uow.customers.session.query(Customer).limit(limit).offset(offset).all()


def get_customer_by_id(
    uow: AbstractUnitOfWork, customer_id: UUID
) -> Optional[Customer]:
    """
    Retrieve a single customer by their unique ID.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database access.
        customer_id (UUID): Unique ID of the customer.

    Returns:
        Optional[Customer]: The customer record if found, else None.
    """
    return uow.customers.get_by_id(customer_id)


def get_customer_by_phone(uow: AbstractUnitOfWork, phone: str) -> Optional[Customer]:
    """
    Search for a customer by their phone number.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database access.
        phone (str): Phone number to search for.

    Returns:
        Optional[Customer]: The customer record if found, else None.
    """
    return uow.customers.session.query(Customer).filter(Customer.phone == phone).first()


def create_customer(uow: AbstractUnitOfWork, customer_data: CustomerCreate) -> Customer:
    """
    Create a new customer in the system.

    Performs validation checks for unique phone numbers and email addresses.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database operations.
        customer_data (CustomerCreate): Payload containing customer details.

    Returns:
        Customer: The newly created customer object.

    Raises:
        HTTPException: If the phone number or email already exists.
    """
    with uow:
        if customer_data.phone:
            existing_phone = get_customer_by_phone(uow, customer_data.phone)
            if existing_phone:
                raise HTTPException(status_code=400, detail="Phone already exists")

        if customer_data.email:
            existing_email = (
                uow.customers.session.query(Customer)
                .filter(Customer.email == customer_data.email)
                .first()
            )
            if existing_email:
                raise HTTPException(status_code=400, detail="Email already exists")

        new_customer = Customer(**customer_data.model_dump())
        uow.customers.add(new_customer)
        uow.commit()
    uow.refresh(new_customer)
    logger.info(f"Customer created: {new_customer.name} (ID: {new_customer.id})")
    return new_customer


def update_customer(
    uow: AbstractUnitOfWork, customer_id: UUID, customer_data: CustomerCreate
) -> Optional[Customer]:
    """
    Update an existing customer's information.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database operations.
        customer_id (UUID): Unique ID of the customer to update.
        customer_data (CustomerCreate): Updated data fields.

    Returns:
        Optional[Customer]: The updated customer record, or None if not found.
    """
    with uow:
        customer = uow.customers.get_by_id(customer_id)
        if not customer:
            return None

        update_dict = customer_data.model_dump(exclude_unset=True)
        for key, value in update_dict.items():
            setattr(customer, key, value)

        uow.commit()
    uow.refresh(customer)
    logger.info(f"Customer updated: {customer.name} (ID: {customer.id})")
    return customer


def delete_customer(uow: AbstractUnitOfWork, customer_id: UUID) -> bool:
    """
    Delete a customer from the system.

    Args:
        uow (AbstractUnitOfWork): Unit of Work for database operations.
        customer_id (UUID): Unique ID of the customer to delete.

    Returns:
        bool: True if deleted successfully, False if not found.
    """
    with uow:
        customer = uow.customers.get_by_id(customer_id)
        if not customer:
            return False

        uow.customers.delete(customer)
        uow.commit()
    logger.info(f"Customer deleted: {customer_id}")
    return True
