from datetime import datetime
import uuid
import logging
from abc import ABC, abstractmethod
from typing import List, Optional, Tuple, Any
from uuid import UUID

logger = logging.getLogger(__name__)

from app.models.sync import SyncLog
from app.models.item import Item
from app.models.sale import Sale
from app.models.attendance import Attendance
from app.models.raw_material import RawMaterial
from app.schemas.sync import (
    SyncOperation,
    SyncPushResponse,
    SyncOperationResult,
    SyncPullResponse,
    SyncAction,
)
from app.models.user import User
from app.repositories.base import AbstractUnitOfWork

# Note: Ideally these handlers would dispatch to specific services (e.g. sales_service.create_sale)
# But for outbox parsing dynamically with LWW conflicts, we construct them inline mapping here to avoid
# circular logic, or call services.


def _get_model_class(entity: str):
    """
    Internal helper to map an entity name to its corresponding model class.

    Args:
        entity (str): Name of the entity (e.g., 'sale', 'item').

    Returns:
        Type: The SQLAlchemy model class or None.
    """
    handler = SYNC_HANDLER_REGISTRY.get(entity)
    if handler:
        return handler.model_class
    return None


class OperationValidator:
    """Validator for incoming sync operations from clients."""

    @staticmethod
    def validate(op: SyncOperation) -> Optional[str]:
        """
        Validate that the sync operation is targeting a known entity.

        Args:
            op (SyncOperation): The operation to validate.

        Returns:
            Optional[str]: Error message if invalid, else None.
        """
        if op.entity not in SYNC_HANDLER_REGISTRY:
            return f"Unknown entity: {op.entity}"
        return None


class ConflictResolver(ABC):
    """Abstract base class for resolving synchronization conflicts."""

    @abstractmethod
    def resolve(self, db_obj: Any, op: SyncOperation) -> bool:
        """Returns True if the update was applied."""


class LWWResolver(ConflictResolver):
    def resolve(self, db_obj: Any, op: SyncOperation) -> bool:
        local_op_time = op.updated_at.astimezone(None).replace(tzinfo=None)
        if db_obj.updated_at and local_op_time < db_obj.updated_at:
            return False

        payload_data = (
            op.payload.model_dump(exclude_unset=True)
            if hasattr(op.payload, "model_dump")
            else op.payload
        )
        for key, value in payload_data.items():
            if key in ["id", "user_id", "recorded_by"]:
                continue
            setattr(db_obj, key, value)
        return True


class StockDeltaResolver(ConflictResolver):
    def resolve(self, db_obj: Any, op: SyncOperation) -> bool:
        # Stock updates apply delta; other fields use LWW
        local_op_time = op.updated_at.astimezone(None).replace(tzinfo=None)
        is_newer = not db_obj.updated_at or local_op_time >= db_obj.updated_at

        payload_data = (
            op.payload.model_dump(exclude_unset=True)
            if hasattr(op.payload, "model_dump")
            else op.payload
        )
        for key, value in payload_data.items():
            if key in ["id", "user_id", "recorded_by"]:
                continue
            if key == "current_stock":
                # item.current_stock is Integer
                db_obj.current_stock = getattr(db_obj, "current_stock", 0) + int(value)
            elif key == "stock":
                # raw_material.stock is Numeric
                from app.utils.money import to_decimal

                db_obj.stock = to_decimal(getattr(db_obj, "stock", 0)) + to_decimal(
                    value
                )
            elif is_newer:
                setattr(db_obj, key, value)
        return True


CONFLICT_RESOLVER_REGISTRY = {
    "item": StockDeltaResolver(),
    "raw_material": StockDeltaResolver(),
}


def _get_resolver(entity: str) -> ConflictResolver:
    return CONFLICT_RESOLVER_REGISTRY.get(entity, LWWResolver())


class BaseSyncHandler(ABC):
    """Abstract base for handling entity-specific sync logic (CRUD)."""

    @abstractmethod
    def apply_create(
        self, uow: AbstractUnitOfWork, payload: Any, current_user: User
    ) -> Tuple[Optional[UUID], Optional[str]]:
        pass

    @abstractmethod
    def apply_update(
        self,
        uow: AbstractUnitOfWork,
        payload: Any,
        current_user: User,
        op: SyncOperation,
    ) -> Tuple[Optional[UUID], Optional[str]]:
        pass

    @abstractmethod
    def apply_delete(
        self, uow: AbstractUnitOfWork, payload: Any, current_user: User
    ) -> Tuple[Optional[UUID], Optional[str]]:
        pass


class GenericSyncHandler(BaseSyncHandler):
    """
    Standard implementation for syncing most database entities.

    Uses a repository-based approach to apply create, update, and delete actions.
    """

    def __init__(self, model_class: Any, repo_name: str):
        self.model_class = model_class
        self.repo_name = repo_name

    def _get_repo(self, uow: AbstractUnitOfWork):
        return getattr(uow, self.repo_name)

    def apply_create(
        self, uow: AbstractUnitOfWork, payload: Any, current_user: User
    ) -> Tuple[Optional[UUID], Optional[str]]:
        # Convert payload to dict if it's a Pydantic model
        payload_data = (
            payload.model_dump(exclude_unset=True)
            if hasattr(payload, "model_dump")
            else payload
        )
        # Robustly extract or generate record_id
        record_id_raw = payload_data.get("id")
        if record_id_raw:
            record_id = (
                UUID(record_id_raw)
                if not isinstance(record_id_raw, UUID)
                else record_id_raw
            )
        else:
            record_id = uuid.uuid4()

        repo = self._get_repo(uow)
        obj_data = payload_data.copy()
        obj_data["id"] = record_id

        if hasattr(self.model_class, "user_id"):
            obj_data["user_id"] = current_user.id
        if hasattr(self.model_class, "recorded_by"):
            obj_data["recorded_by"] = current_user.id

        # Filter obj_data to only include keys that are actual columns in the model
        # This prevents issues with relationships like 'items' being passed as dicts
        valid_columns = {c.name for c in self.model_class.__table__.columns}
        filtered_data = {k: v for k, v in obj_data.items() if k in valid_columns}

        existing_obj = (
            repo.get_by_id_scoped(record_id, current_user.id)
            if hasattr(self.model_class, "user_id")
            else repo.get_by_id(record_id)
        )
        if not existing_obj:
            new_obj = self.model_class(**filtered_data)
            repo.add(new_obj)
            uow.flush()
        return record_id, None

    def apply_update(
        self,
        uow: AbstractUnitOfWork,
        payload: Any,
        current_user: User,
        op: SyncOperation,
    ) -> Tuple[Optional[UUID], Optional[str]]:
        # Convert payload to dict if it's a Pydantic model
        payload_data = (
            payload.model_dump(exclude_unset=True)
            if hasattr(payload, "model_dump")
            else payload
        )
        repo = self._get_repo(uow)
        record_id_raw = payload_data.get("id")
        record_id = (
            UUID(record_id_raw)
            if not isinstance(record_id_raw, UUID)
            else record_id_raw
        )
        db_obj = (
            repo.get_by_id_scoped(record_id, current_user.id)
            if hasattr(self.model_class, "user_id")
            else repo.get_by_id(record_id)
        )

        if not db_obj:
            return record_id, "Record not found or access denied for update"

        resolver = _get_resolver(op.entity)
        if resolver.resolve(db_obj, op):
            uow.flush()

        return record_id, None

    def apply_delete(
        self, uow: AbstractUnitOfWork, payload: Any, current_user: User
    ) -> Tuple[Optional[UUID], Optional[str]]:
        # Convert payload to dict if it's a Pydantic model
        payload_data = (
            payload.model_dump(exclude_unset=True)
            if hasattr(payload, "model_dump")
            else payload
        )
        repo = self._get_repo(uow)
        record_id_raw = payload_data.get("id")
        record_id = (
            UUID(record_id_raw)
            if not isinstance(record_id_raw, UUID)
            else record_id_raw
        )
        db_obj = (
            repo.get_by_id_scoped(record_id, current_user.id)
            if hasattr(self.model_class, "user_id")
            else repo.get_by_id(record_id)
        )

        if db_obj:
            repo.delete(db_obj)
            uow.flush()
            return record_id, None
        return record_id, "Record not found or access denied for delete"


class SaleSyncHandler(GenericSyncHandler):
    def __init__(self):
        super().__init__(Sale, "sales")


class ItemSyncHandler(GenericSyncHandler):
    def __init__(self):
        super().__init__(Item, "items")


class AttendanceSyncHandler(GenericSyncHandler):
    def __init__(self):
        super().__init__(Attendance, "attendance")


class RawMaterialSyncHandler(GenericSyncHandler):
    def __init__(self):
        super().__init__(RawMaterial, "raw_materials")


SYNC_HANDLER_REGISTRY = {
    "sale": SaleSyncHandler(),
    "item": ItemSyncHandler(),
    "attendance": AttendanceSyncHandler(),
    "raw_material": RawMaterialSyncHandler(),
}


class SyncExecutor:
    """Executes validated sync operations using the appropriate handlers."""

    @staticmethod
    def execute(
        uow: AbstractUnitOfWork, op: SyncOperation, current_user: User
    ) -> Tuple[Optional[UUID], Optional[str]]:
        """
        Run a single sync operation within a nested transaction.

        Args:
            uow (AbstractUnitOfWork): Unit of Work.
            op (SyncOperation): The sync operation (CREATE/UPDATE/DELETE).
            current_user (User): The user performing the sync.

        Returns:
            Tuple[Optional[UUID], Optional[str]]: (record_id, error_message).
        """
        handler = SYNC_HANDLER_REGISTRY.get(op.entity)
        if not handler:
            return None, f"Unsupported entity: {op.entity}"

        try:
            with uow.begin_nested():
                if op.action == SyncAction.CREATE:
                    return handler.apply_create(uow, op.payload, current_user)
                elif op.action == SyncAction.UPDATE:
                    return handler.apply_update(uow, op.payload, current_user, op)

                elif op.action == SyncAction.DELETE:
                    return handler.apply_delete(uow, op.payload, current_user)
                else:
                    return None, f"Invalid sync action: {op.action}"
        except Exception as e:
            return None, str(e)


class SyncLogWriter:
    """Records sync results into the audit log for idempotency and debugging."""

    @staticmethod
    def write(
        uow: AbstractUnitOfWork,
        op: SyncOperation,
        record_id: Optional[uuid.UUID],
        status: str,
        error: Optional[str],
    ):
        """
        Create or update a sync log entry for a client-side operation.

        Args:
            uow (AbstractUnitOfWork): Unit of Work.
            op (SyncOperation): The client operation.
            record_id (Optional[UUID]): ID of the record in the server database.
            status (str): Outcome status (success/failed).
            error (Optional[str]): Error details if failed.
        """
        existing_log = uow.sync_logs.get_by_client_id(op.id)
        if not existing_log:
            uow.sync_logs.add(
                SyncLog(
                    client_id=op.id,
                    entity=op.entity,
                    record_id=record_id or uuid.uuid4(),
                    action=op.action,
                    payload=(
                        op.payload.model_dump(mode="json")
                        if hasattr(op.payload, "model_dump")
                        else op.payload
                    ),
                    status=status,
                    error_message=error,
                )
            )
        else:
            existing_log.status = status
            existing_log.record_id = record_id or existing_log.record_id
            existing_log.error_message = error


class ResponseAggregator:
    def __init__(self):
        self.success = []
        self.failed = []

    def add_result(
        self,
        client_id: str,
        record_id: Optional[uuid.UUID],
        status: str,
        error: Optional[str] = None,
    ):
        result = SyncOperationResult(
            client_id=client_id, record_id=record_id, status=status, error=error
        )
        if status == "success":
            self.success.append(result)
        else:
            self.failed.append(result)

    def get_response(self) -> SyncPushResponse:
        return SyncPushResponse(success=self.success, failed=self.failed)


class OperationDispatcher:
    """Main entry point for processing batches of sync operations."""

    @staticmethod
    def dispatch(
        uow: AbstractUnitOfWork, operations: List[SyncOperation], current_user: User
    ) -> SyncPushResponse:
        """
        Process multiple sync operations, handling idempotency and validation.

        Args:
            uow (AbstractUnitOfWork): Unit of Work.
            operations (List[SyncOperation]): Batch of client-side changes.
            current_user (User): User pushing the changes.

        Returns:
            SyncPushResponse: Aggregated results of all operations.
        """
        aggregator = ResponseAggregator()
        for op in operations:
            # 1. Idempotency Check
            existing_log = uow.sync_logs.get_by_client_id(op.id)
            if existing_log and existing_log.status == "success":
                aggregator.add_result(op.id, existing_log.record_id, "success")
                continue

            # 2. Validation
            err = OperationValidator.validate(op)
            if err:
                aggregator.add_result(op.id, None, "failed", err)
                continue

            # 3. Execution
            record_id, exec_err = SyncExecutor.execute(uow, op, current_user)

            # 4. Logging & Response
            status = "failed" if exec_err else "success"
            if exec_err:
                logger.warning(f"Sync Push failed for {op.id}: {exec_err}")

            SyncLogWriter.write(uow, op, record_id, status, exec_err)
            aggregator.add_result(op.id, record_id, status, exec_err)

        uow.commit()
        return aggregator.get_response()


def process_push_sync(
    uow: AbstractUnitOfWork, operations: List[SyncOperation], current_user: User
) -> SyncPushResponse:
    """
    Public entry point for mobile clients to push offline changes.

    Args:
        uow (AbstractUnitOfWork): Unit of Work.
        operations (List[SyncOperation]): List of changes to apply.
        current_user (User): Authenticated user.

    Returns:
        SyncPushResponse: Results for each operation in the batch.
    """
    return OperationDispatcher.dispatch(uow, operations, current_user)


def pull_sync(uow: AbstractUnitOfWork, last_sync: datetime) -> SyncPullResponse:
    """
    Retrieve all server-side changes since the client's last synchronization.

    Enables clients to update their local database with new or modified records.

    Args:
        uow (AbstractUnitOfWork): Unit of Work.
        last_sync (datetime): Timestamp of the last successful sync.

    Returns:
        SyncPullResponse: Dictionaries of changed items, sales, attendance, and materials.
    """

    # helper row to dict
    def to_dict_list(rows):
        res = []
        for r in rows:
            d = {c.name: getattr(r, c.name) for c in r.__table__.columns}
            res.append(d)
        return res

    items = uow.session.query(Item).filter(Item.updated_at > last_sync).all()
    sales = uow.session.query(Sale).filter(Sale.updated_at > last_sync).all()
    attendance = (
        uow.session.query(Attendance).filter(Attendance.updated_at > last_sync).all()
    )
    raw_materials = (
        uow.session.query(RawMaterial).filter(RawMaterial.updated_at > last_sync).all()
    )

    return SyncPullResponse(
        items=to_dict_list(items),
        sales=to_dict_list(sales),
        attendance=to_dict_list(attendance),
        raw_materials=to_dict_list(raw_materials),
    )
