from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db.deps import get_uow, check_role
from app.repositories.base import AbstractUnitOfWork
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.customer import CustomerCreate, CustomerResponse
from app.services import customer_service

router = APIRouter(prefix="/customers", tags=["Customers"])


@router.post("/", response_model=CustomerResponse)
def create_customer(
    customer: CustomerCreate,
    current_user: User = Depends(
        check_role([UserRole.OWNER, UserRole.MANAGER, UserRole.SALES])
    ),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Create a new customer.

    RBAC: Restricted to OWNER, MANAGER, and SALES roles.

    Args:
        customer (CustomerCreate): Customer data.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        CustomerResponse: The created customer.
    """
    return customer_service.create_customer(uow, customer)


@router.get("/search", response_model=CustomerResponse)
def search_customer_by_phone(
    phone: str,
    current_user: User = Depends(
        check_role([UserRole.OWNER, UserRole.MANAGER, UserRole.SALES])
    ),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Search for a customer by phone number.

    RBAC: Restricted to OWNER, MANAGER, and SALES roles.

    Args:
        phone (str): Phone number to search for.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        CustomerResponse: The customer if found.

    Raises:
        HTTPException: If customer is not found.
    """
    customer = customer_service.get_customer_by_phone(uow, phone)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


@router.get("/", response_model=List[CustomerResponse])
def get_customers(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(
        check_role([UserRole.OWNER, UserRole.MANAGER, UserRole.SALES])
    ),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    List all customers with pagination.

    RBAC: Restricted to OWNER, MANAGER, and SALES roles.

    Args:
        limit (int): Maximum number of customers to return.
        offset (int): Number of customers to skip.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        List[CustomerResponse]: List of customers.
    """
    return customer_service.get_customers(uow, limit, offset)


@router.get("/{customer_id}", response_model=CustomerResponse)
def get_customer(
    customer_id: UUID,
    current_user: User = Depends(
        check_role([UserRole.OWNER, UserRole.MANAGER, UserRole.SALES])
    ),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Get a customer by ID.

    RBAC: Restricted to OWNER, MANAGER, and SALES roles.

    Args:
        customer_id (UUID): Unique ID of the customer.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        CustomerResponse: Customer information.

    Raises:
        HTTPException: If customer is not found.
    """
    customer = customer_service.get_customer_by_id(uow, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


@router.put("/{customer_id}", response_model=CustomerResponse)
def update_customer(
    customer_id: UUID,
    customer_data: CustomerCreate,
    current_user: User = Depends(
        check_role([UserRole.OWNER, UserRole.MANAGER, UserRole.SALES])
    ),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Update customer information.

    RBAC: Restricted to OWNER, MANAGER, and SALES roles.

    Args:
        customer_id (UUID): Unique ID of the customer.
        customer_data (CustomerCreate): Updated data.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        CustomerResponse: Updated customer information.

    Raises:
        HTTPException: If customer is not found.
    """
    customer = customer_service.update_customer(uow, customer_id, customer_data)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


@router.delete("/{customer_id}")
def delete_customer(
    customer_id: UUID,
    current_user: User = Depends(check_role([UserRole.OWNER, UserRole.MANAGER])),
    uow: AbstractUnitOfWork = Depends(get_uow),
):
    """
    Delete a customer.

    RBAC: Restricted to OWNER and MANAGER roles.

    Args:
        customer_id (UUID): Unique ID of the customer to delete.
        current_user (User): Authenticated user.
        uow (AbstractUnitOfWork): Unit of Work for database operations.

    Returns:
        dict: Success message.

    Raises:
        HTTPException: If customer is not found.
    """
    success = customer_service.delete_customer(uow, customer_id)
    if not success:
        raise HTTPException(status_code=404, detail="Customer not found")
    return {"message": "Customer deleted successfully"}
