from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, Float, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import uuid

from app.db.base import Base


class Sale(Base):
    __tablename__ = "sales"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    invoice_number = Column(String, unique=True, index=True)
    invoice_date = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    customer_id = Column(UUID(as_uuid=True), ForeignKey("customers.id"), nullable=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True) # Staff who made the sale
    
    order_type = Column(String, default="in-store") # in-store, online
    sub_total = Column(Float, default=0.0)
    tax_total = Column(Float, default=0.0)
    discount = Column(Float, default=0.0)
    total_amount = Column(Float, default=0.0)
    
    payment_status = Column(String, default="pending") # pending, paid, partial
    payment_method = Column(String, nullable=True) # cash, upi, card, online
    order_status = Column(String, default="completed") # draft, confirmed, shipped, delivered, cancelled
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    items = relationship("SaleItem", back_populates="sale")
    customer = relationship("Customer")
    user = relationship("User")
