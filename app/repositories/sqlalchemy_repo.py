from typing import Any, Optional, Type
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.attendance import Attendance
from app.models.customer import Customer
from app.models.daily_wage import DailyWage
from app.models.item import Item
from app.models.purchase import Purchase
from app.models.purchase_item import PurchaseItem
from app.models.raw_material import RawMaterial
from app.models.sale import Sale
from app.models.sync import SyncLog
from app.models.user import User
from app.repositories.base import AbstractUnitOfWork, BaseRepository


class SQLAlchemyRepository(BaseRepository):
    """
    Concrete implementation of BaseRepository using SQLAlchemy.

    Provides generic CRUD operations for any mapped SQLAlchemy model.
    """

    def __init__(self, session: Session, model: Type[Any]):
        self.session = session
        self.model = model

    def get_by_id(self, record_id: UUID) -> Any:
        return self.session.query(self.model).filter(self.model.id == record_id).first()

    def add(self, entity: Any) -> None:
        self.session.add(entity)

    def update(self, entity: Any) -> None:
        # SQLAlchemy handles updates via identity map once object is modified
        pass

    def delete(self, entity: Any) -> None:
        self.session.delete(entity)

    def get_by_client_id(self, client_id: str) -> Any:
        if hasattr(self.model, "client_id"):
            return (
                self.session.query(self.model)
                .filter(self.model.client_id == client_id)
                .first()
            )
        return None

    def get_by_id_scoped(self, record_id: UUID, user_id: UUID) -> Any:
        query = self.session.query(self.model).filter(self.model.id == record_id)
        if hasattr(self.model, "user_id"):
            query = query.filter(self.model.user_id == user_id)
        return query.first()


class ItemRepository(SQLAlchemyRepository):
    def __init__(self, session: Session):
        super().__init__(session, Item)


class SaleRepository(SQLAlchemyRepository):
    def __init__(self, session: Session):
        super().__init__(session, Sale)

    def get_by_id_eager(self, sale_id: UUID) -> Optional[Sale]:
        from sqlalchemy.orm import joinedload

        from app.models.sale_item import SaleItem

        return (
            self.session.query(Sale)
            .options(
                joinedload(Sale.items).joinedload(SaleItem.item),
                joinedload(Sale.customer),
            )
            .filter(Sale.id == sale_id)
            .first()
        )

    def get_by_client_id_eager(self, client_id: str) -> Optional[Sale]:
        from sqlalchemy.orm import joinedload

        from app.models.sale_item import SaleItem

        return (
            self.session.query(Sale)
            .options(
                joinedload(Sale.items).joinedload(SaleItem.item),
                joinedload(Sale.customer),
            )
            .filter(Sale.client_id == client_id)
            .first()
        )


class AttendanceRepository(SQLAlchemyRepository):
    def __init__(self, session: Session):
        super().__init__(session, Attendance)


class RawMaterialRepository(SQLAlchemyRepository):
    def __init__(self, session: Session):
        super().__init__(session, RawMaterial)


class SyncLogRepository(SQLAlchemyRepository):
    def __init__(self, session: Session):
        super().__init__(session, SyncLog)

    def get_by_client_id(self, client_id: str) -> Optional[SyncLog]:
        return (
            self.session.query(SyncLog).filter(SyncLog.client_id == client_id).first()
        )


class UserRepository(SQLAlchemyRepository):
    def __init__(self, session: Session):
        super().__init__(session, User)


class CustomerRepository(SQLAlchemyRepository):
    def __init__(self, session: Session):
        super().__init__(session, Customer)


class DailyWageRepository(SQLAlchemyRepository):
    def __init__(self, session: Session):
        super().__init__(session, DailyWage)


class PurchaseRepository(SQLAlchemyRepository):
    def __init__(self, session: Session):
        super().__init__(session, Purchase)


class PurchaseItemRepository(SQLAlchemyRepository):
    def __init__(self, session: Session):
        super().__init__(session, PurchaseItem)


class SQLAlchemyUnitOfWork(AbstractUnitOfWork):
    """
    SQLAlchemy-backed implementation of the Unit of Work pattern.

    Integrates with SQLAlchemy Session for transaction management.
    """

    def __init__(self, session: Session):
        self._session = session
        self.items = ItemRepository(session)
        self.sales = SaleRepository(session)
        self.attendance = AttendanceRepository(session)
        self.raw_materials = RawMaterialRepository(session)
        self.sync_logs = SyncLogRepository(session)
        self.users = UserRepository(session)
        self.customers = CustomerRepository(session)
        self.wages = DailyWageRepository(session)
        self.purchases = PurchaseRepository(session)
        self.purchase_items = PurchaseItemRepository(session)

    @property
    def session(self):
        return self._session

    def commit(self):
        """
        Commit the current database transaction.

        Ensures session is active and avoids redundant state changes.
        """
        if self._session.is_active:
            try:
                # Only commit if we actually have an active transaction to avoid IllegalStateChangeError
                if self._session.in_transaction():
                    self._session.commit()
            except Exception:
                self.rollback()
                raise

    def rollback(self):
        """Rolls back the current transaction unconditionally. SQLAlchemy handles redundant rollbacks safely."""
        try:
            self._session.rollback()
        except Exception:
            pass

    def reset(self):
        """
        Forcefully clears the session state.
        Used to prevent dirty/stale objects from leaking between retry attempts.
        """
        try:
            if self._session.is_active:
                if self._session.in_transaction():
                    self._session.rollback()
            self._session.expunge_all()
            self._session.close()  # Close to ensure a completely fresh start if needed
        except Exception:
            pass

    def begin_nested(self):
        return self._session.begin_nested()

    def flush(self):
        """Explicitly flushes pending changes. Use with caution to avoid SAWarnings."""
        if self._session.is_active:
            self._session.flush()

    def refresh(self, entity: Any):
        """
        Expire and reload an entity from the database.

        Args:
            entity (Any): The model instance to refresh.
        """
        self._session.refresh(entity)
