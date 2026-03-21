from abc import ABC, abstractmethod
from typing import Any, List, Optional
from uuid import UUID
from datetime import datetime


class BaseRepository(ABC):
    @abstractmethod
    def get_by_id(self, record_id: UUID) -> Any:
        pass

    @abstractmethod
    def get_by_id_scoped(self, record_id: UUID, user_id: UUID) -> Any:
        pass

    @abstractmethod
    def add(self, entity: Any) -> None:
        pass

    @abstractmethod
    def update(self, entity: Any) -> None:
        pass

    @abstractmethod
    def delete(self, entity: Any) -> None:
        pass

    @abstractmethod
    def get_by_client_id(self, client_id: str) -> Any:
        pass


class AbstractUnitOfWork(ABC):
    items: BaseRepository
    sales: BaseRepository
    attendance: BaseRepository
    raw_materials: BaseRepository
    sync_logs: BaseRepository

    def __enter__(self) -> "AbstractUnitOfWork":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.rollback()

    @abstractmethod
    def commit(self):
        pass

    @abstractmethod
    def rollback(self):
        pass

    @abstractmethod
    def begin_nested(self):
        pass
