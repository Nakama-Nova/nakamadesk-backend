from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Any, List, Optional, Type
from uuid import UUID
from datetime import datetime
from app.repositories.base import BaseRepository, AbstractUnitOfWork
from app.models.item import Item
from app.models.sale import Sale
from app.models.attendance import Attendance
from app.models.raw_material import RawMaterial
from app.models.sync import SyncLog

class SQLAlchemyRepository(BaseRepository):
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
            return self.session.query(self.model).filter(self.model.client_id == client_id).first()
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
        return self.session.query(SyncLog).filter(SyncLog.client_id == client_id).first()

class SQLAlchemyUnitOfWork(AbstractUnitOfWork):
    def __init__(self, session: Session):
        self.session = session
        self.items = ItemRepository(session)
        self.sales = SaleRepository(session)
        self.attendance = AttendanceRepository(session)
        self.raw_materials = RawMaterialRepository(session)
        self.sync_logs = SyncLogRepository(session)

    def commit(self):
        self.session.commit()

    def rollback(self):
        self.session.rollback()
    
    def begin_nested(self):
        return self.session.begin_nested()

    def flush(self):
        self.session.flush()

    def query(self, model: Type[Any]):
        return self.session.query(model)
