from datetime import datetime
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

from app.models.user import User
from app.repositories.base import AbstractUnitOfWork, BaseRepository
from app.schemas.sync import SyncAction, SyncOperation
from app.services.sync_service import SyncExecutor


class MockUnitOfWork(AbstractUnitOfWork):
    def __init__(self):
        self.items = MagicMock(spec=BaseRepository)
        self.sales = MagicMock(spec=BaseRepository)
        self.attendance = MagicMock(spec=BaseRepository)
        self.raw_materials = MagicMock(spec=BaseRepository)
        self.sync_logs = MagicMock(spec=BaseRepository)
        self.committed = False
        self.rolled_back = False
        self._session = MagicMock()

    @property
    def session(self):
        return self._session

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def begin_nested(self):
        return MagicMock()

    def flush(self):
        pass

    def refresh(self, entity: Any):
        pass


def test_sync_executor_create_calls_repository():
    """Verify that SyncExecutor calls the repository's add method on create."""
    uow = MockUnitOfWork()
    user = User(id=uuid4(), username="testuser")

    op = SyncOperation(
        id=str(uuid4()),
        entity="sale",
        action=SyncAction.CREATE,
        payload={
            "id": str(uuid4()),
            "customer_id": str(uuid4()),
            "items": [{"product_id": str(uuid4()), "quantity": 1, "price": 100.0}],
            "total_amount": 100.0,
            "payment_method": "cash",
        },
        updated_at=datetime.now(),
    )

    # Mock repository behavior
    uow.sales.get_by_id_scoped.return_value = None

    record_id, error = SyncExecutor.execute(uow, op, user)

    assert error is None
    assert uow.sales.add.called
    # Check that record_id matches the payload
    assert str(record_id) == str(op.payload.id)


def test_sync_executor_update_calls_repository():
    """Verify that SyncExecutor calls repository's update logic."""
    uow = MockUnitOfWork()
    user = User(id=uuid4(), username="testuser")
    record_id = uuid4()

    # Mock existing object
    mock_sale = MagicMock()
    mock_sale.id = record_id
    mock_sale.updated_at = datetime.now()
    uow.sales.get_by_id_scoped.return_value = mock_sale

    op = SyncOperation(
        id=str(uuid4()),
        entity="sale",
        action=SyncAction.UPDATE,
        payload={
            "id": str(record_id),
            "customer_id": str(uuid4()),
            "items": [],
            "total_amount": 200.0,
            "payment_method": "card",
        },
        updated_at=datetime.now(),  # Newer by default in this test setup if we don't fix times
    )

    record_id_res, error = SyncExecutor.execute(uow, op, user)

    assert error is None
    assert uow.sales.get_by_id_scoped.called
