from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, EmailStr


class CustomerBase(BaseModel):
    name: str
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    address: Optional[str] = None
    pincode: Optional[str] = None
    gstin: Optional[str] = None
    customer_type: str = "retail"
    user_id: Optional[UUID] = None


class CustomerCreate(CustomerBase):
    pass


class CustomerResponse(CustomerBase):
    id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
