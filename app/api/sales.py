from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.deps import get_db, get_current_user, check_role
from app.models.enums import UserRole
from app.models.sale_item import SaleItem
from app.models.user import User
from app.schemas.sale import SaleCreate, SaleItemResponse, SaleResponse
from app.services.sales_service import (
    create_sale_transaction,
    get_all_sales,
    get_sale_by_id,
    get_sale_items_by_id,
)

router = APIRouter(prefix="/sales", tags=["Sales"])


@router.post("/", response_model=SaleResponse)
def create_sale(
    sale_data: SaleCreate,
    current_user: User = Depends(
        check_role([UserRole.OWNER, UserRole.MANAGER, UserRole.SALES])
    ),
    db: Session = Depends(get_db),
):
    return create_sale_transaction(db, sale_data, current_user.id)


@router.get("/", response_model=List[SaleResponse])
def get_sales(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    customer_id: Optional[UUID] = None,
    date: str = None,
    current_user: User = Depends(
        check_role([UserRole.OWNER, UserRole.MANAGER, UserRole.SALES])
    ),
    db: Session = Depends(get_db),
):
    return get_all_sales(
        db, limit=limit, offset=offset, customer_id=customer_id, date=date
    )


@router.get("/{sale_id}", response_model=SaleResponse)
def get_sale(
    sale_id: UUID,
    current_user: User = Depends(
        check_role([UserRole.OWNER, UserRole.MANAGER, UserRole.SALES])
    ),
    db: Session = Depends(get_db),
):
    sale = get_sale_by_id(db, sale_id)
    if not sale:
        raise HTTPException(status_code=404, detail="Sale not found")
    return sale


@router.get("/{sale_id}/items", response_model=List[SaleItemResponse])
def get_sale_items(
    sale_id: UUID,
    current_user: User = Depends(
        check_role([UserRole.OWNER, UserRole.MANAGER, UserRole.SALES])
    ),
    db: Session = Depends(get_db),
):
    return get_sale_items_by_id(db, sale_id)
