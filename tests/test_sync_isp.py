import pytest
from pydantic import ValidationError
from uuid import uuid4, UUID
from datetime import datetime, date
from decimal import Decimal
from app.schemas.sync import (
    SyncOperation,
    SyncAction,
    SalePayload,
    ItemPayload,
    AttendancePayload,
)
from app.services.sync_service import SyncExecutor
from app.repositories.base import AbstractUnitOfWork, BaseRepository
from app.models.user import User

from unittest.mock import MagicMock


class MockUnitOfWork(AbstractUnitOfWork):
    def __init__(self):
        self.items = MagicMock()
        self.sales = MagicMock()
        self.attendance = MagicMock()
        self.raw_materials = MagicMock()
        self.sync_logs = MagicMock()
        self.committed = False
        self.rolled_back = False
        self.nested = False

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def begin_nested(self):
        self.nested = True
        return self

    def flush(self):
        pass


def test_valid_item_payload_processed_successfully():
    uow = MockUnitOfWork()
    user = User(id=uuid4(), username="test")
    payload = {
        "name": "Test Item",
        "sku": "ITEM-001",
        "selling_price": Decimal("9.99"),
        "current_stock": 100,
    }
    op = SyncOperation(
        id=str(uuid4()),
        entity="item",
        action=SyncAction.CREATE,
        payload=payload,
        updated_at=datetime.now(),
    )
    # Mock repository behavior
    uow.items.get_by_id_scoped = lambda rid, uid: None
    uow.items.add = lambda obj: None
    record_id, err = SyncExecutor.execute(uow, op, user)
    assert err is None
    assert isinstance(record_id, UUID)


# Keep the invalid sale payload test unchanged


def test_invalid_sale_payload_raises_validation_error():
    # Missing required field 'payment_method'
    payload = {
        "customer_id": uuid4(),
        "items": [{"product_id": uuid4(), "quantity": 2, "price": Decimal("10.00")}],
        "total_amount": Decimal("20.00"),
    }
    with pytest.raises(ValidationError):
        SyncOperation(
            id=str(uuid4()),
            entity="sale",
            action=SyncAction.CREATE,
            payload=payload,
            updated_at=datetime.now(),
        )
