from sqlalchemy import Column, Integer

from app.db.base import Base


class InvoiceCounter(Base):
    """
    One row per calendar year holding the last bill number issued.

    Bill numbers must be consecutive (a GST requirement once registered), so
    they come from this counter, not from random suffixes. See
    invoice_service.generate_invoice_number.
    """

    __tablename__ = "invoice_counters"

    year = Column(Integer, primary_key=True, autoincrement=False)
    last_no = Column(Integer, nullable=False, default=0)
