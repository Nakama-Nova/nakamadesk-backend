import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, List, Optional, Tuple
from uuid import UUID

from app.models.attendance import Attendance
from app.models.customer import Customer
from app.models.enums import UserRole
from app.models.item import Item
from app.models.raw_material import RawMaterial
from app.models.sale import Sale
from app.models.sync import SyncLog, SyncTombstone
from app.models.user import User
from app.repositories.base import AbstractUnitOfWork
from app.schemas.sync import (
    SyncAction,
    SyncOperation,
    SyncOperationResult,
    SyncPullResponse,
    SyncPushResponse,
)

logger = logging.getLogger(__name__)

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


# Sync must grant no more than the REST routes do. Lists (not sets): UserRole is a
# str Enum whose hash differs from the plain string stored on User.role.
_ALL_ROLES = (
    UserRole.OWNER,
    UserRole.MANAGER,
    UserRole.SALES,
    UserRole.ACHARI,
    UserRole.WORKER,
    UserRole.ARTISAN,
)
_COST_VIEWERS = (UserRole.OWNER, UserRole.MANAGER)
_SALES_ROLES = (UserRole.OWNER, UserRole.MANAGER, UserRole.SALES)

# Who may push changes for each entity (same as POST/PUT/DELETE on its REST routes).
PUSH_ROLES = {
    "sale": _SALES_ROLES,
    "item": _COST_VIEWERS,
    "raw_material": _COST_VIEWERS,
    "attendance": _SALES_ROLES,
}
# Who may pull each entity (same as its REST GET routes). Items are readable by all
# staff with cost fields hidden; attendance is own-rows-only outside _SALES_ROLES.
PULL_ROLES = {
    "sale": _SALES_ROLES,
    "item": _ALL_ROLES,
    "raw_material": _COST_VIEWERS,
    "attendance": _ALL_ROLES,
}


def _role_of(user: User) -> Optional[UserRole]:
    try:
        return UserRole(user.role)
    except ValueError:
        return None  # an unknown role gets nothing


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

    @staticmethod
    def check_maker(op: SyncOperation, user: User) -> Optional[str]:
        """Error message if the client says someone else made this change, else None."""
        if op.user_id is None or op.user_id == user.id:
            return None
        return (
            "This change was made by a different user. Sign in as that user to send it."
        )

    @staticmethod
    def authorize(op: SyncOperation, user: User) -> Optional[str]:
        """Error message if this user's role may not push this entity, else None."""
        if _role_of(user) in PUSH_ROLES.get(op.entity, ()):
            return None
        return f"Access denied: your role may not sync '{op.entity}' changes"


class ConflictResolver(ABC):
    """Abstract base class for resolving synchronization conflicts."""

    @abstractmethod
    def resolve(self, db_obj: Any, op: SyncOperation) -> bool:
        """Returns True if the update was applied."""


class LWWResolver(ConflictResolver):
    def resolve(self, db_obj: Any, op: SyncOperation) -> bool:
        # Client timestamp (SyncOperation and Pydantic handle TZ if provided)
        # Ensure it's treated as UTC (Issue #9)
        from datetime import timezone

        local_op_time = op.updated_at.astimezone(timezone.utc)
        db_updated_at = db_obj.updated_at

        # Ensure db_updated_at is timezone-aware if it's not (SQLAlchemy might return naive UTC)
        if db_updated_at and not db_updated_at.tzinfo:
            db_updated_at = db_updated_at.replace(tzinfo=timezone.utc)

        if db_updated_at and local_op_time < db_updated_at:
            return False

        payload_data = (
            op.payload.model_dump(exclude_unset=True)
            if hasattr(op.payload, "model_dump")
            else op.payload
        )
        # Only real columns: a sale payload also carries nested `items` and
        # customer_name/phone, which are not attributes to overwrite.
        columns = {c.name for c in db_obj.__table__.columns}
        for key, value in payload_data.items():
            if key in ["id", "user_id", "recorded_by"] or key not in columns:
                continue
            setattr(db_obj, key, value)
        return True


class StockDeltaResolver(ConflictResolver):
    def resolve(self, db_obj: Any, op: SyncOperation) -> bool:
        # Stock updates apply delta; other fields use LWW
        from datetime import timezone

        local_op_time = op.updated_at.astimezone(timezone.utc)
        db_updated_at = db_obj.updated_at

        if db_updated_at and not db_updated_at.tzinfo:
            db_updated_at = db_updated_at.replace(tzinfo=timezone.utc)

        is_newer = not db_updated_at or local_op_time >= db_updated_at

        payload_data = (
            op.payload.model_dump(exclude_unset=True)
            if hasattr(op.payload, "model_dump")
            else op.payload
        )
        for key, value in payload_data.items():
            if key in ["id", "user_id", "recorded_by"]:
                continue
            if key == "current_stock":
                db_obj.current_stock = getattr(db_obj, "current_stock", 0) + int(value)
            elif key == "stock":
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
        self,
        uow: AbstractUnitOfWork,
        payload: Any,
        current_user: User,
        op: Optional["SyncOperation"] = None,
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
        self,
        uow: AbstractUnitOfWork,
        payload: Any,
        current_user: User,
        op: Optional["SyncOperation"] = None,
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
        self,
        uow: AbstractUnitOfWork,
        payload: Any,
        current_user: User,
        op: Optional[SyncOperation] = None,
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
        # Capture the op timestamp so the client's time is stored (makes LWW deterministic)
        op_updated_at = op.updated_at if op is not None else None

        existing_obj = (
            repo.get_by_id_scoped(record_id, current_user.id)
            if hasattr(self.model_class, "user_id")
            else repo.get_by_id(record_id)
        )
        if not existing_obj:
            new_obj = self.model_class(**filtered_data)
            # Persist the client's timestamp so future LWW comparisons are deterministic
            if hasattr(new_obj, "updated_at") and op_updated_at is not None:
                from datetime import timezone as _tz

                new_obj.updated_at = op_updated_at.astimezone(_tz.utc).replace(
                    tzinfo=None
                )
            self._attach_children(new_obj, payload_data)
            repo.add(new_obj)
            uow.flush()
        return record_id, None

    def _attach_children(self, new_obj: Any, payload_data: dict) -> None:
        """Hook for entities whose payload carries nested rows (default: none)."""

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
            # Stamp the server record with the client's winning timestamp
            if hasattr(db_obj, "updated_at"):
                from datetime import timezone

                db_obj.updated_at = op.updated_at.astimezone(timezone.utc).replace(
                    tzinfo=None
                )
            uow.flush()

        return record_id, None

    def apply_delete(
        self,
        uow: AbstractUnitOfWork,
        payload: Any,
        current_user: User,
        op: Optional["SyncOperation"] = None,
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
            return record_id, "Record not found or access denied for delete"

        # LWW guard: if the record was updated server-side more recently than
        # this delete was queued, drop the stale delete instead of destroying
        # a newer write. Mirrors apply_update's silent-no-op-on-stale-write
        # behavior rather than erroring, since the delete is legitimately
        # outdated, not invalid.
        if op is not None and hasattr(db_obj, "updated_at"):
            from datetime import timezone as _tz

            db_updated_at = db_obj.updated_at
            if db_updated_at and not db_updated_at.tzinfo:
                db_updated_at = db_updated_at.replace(tzinfo=_tz.utc)

            if db_updated_at and op.updated_at.astimezone(_tz.utc) < db_updated_at:
                return record_id, None

        repo.delete(db_obj)
        uow.flush()
        return record_id, None


class SaleSyncHandler(GenericSyncHandler):
    """
    Offline counter sales go through the same sales logic as the REST API
    (stock ledger, tax, consecutive bill number), not a raw row insert.

    The goods have already left the shop, so a sale is recorded even when
    server stock is short (stock goes negative and is logged). Server prices
    are authoritative; the price the client saw is not used.
    """

    def __init__(self):
        super().__init__(Sale, "sales")

    @staticmethod
    def _customer_id(uow: AbstractUnitOfWork, data: dict) -> Optional[UUID]:
        """Explicit customer_id, else find-or-create by phone, else walk-in."""
        if data.get("customer_id"):
            return data["customer_id"]
        phone = (data.get("customer_phone") or "").strip()
        if not phone:
            return None
        customer = uow.session.query(Customer).filter(Customer.phone == phone).first()
        if not customer:
            customer = Customer(
                name=(data.get("customer_name") or "Walk-in Customer").strip(),
                phone=phone,
            )
            uow.session.add(customer)
            uow.flush()
        return customer.id

    def apply_create(
        self,
        uow: AbstractUnitOfWork,
        payload: Any,
        current_user: User,
        op: Optional[SyncOperation] = None,
    ) -> Tuple[Optional[UUID], Optional[str]]:
        # Imported here: sales_service pulls in inventory and invoice code that
        # this module must not depend on at import time.
        from app.schemas.sale import SaleCreate, SaleItemCreate
        from app.services import sales_service

        data = (
            payload.model_dump(exclude_unset=True)
            if hasattr(payload, "model_dump")
            else payload
        )
        try:
            client_id = UUID(str(op.id)) if op is not None else None
        except ValueError:
            client_id = None  # non-UUID client id: the SyncLog still dedupes

        sale_data = SaleCreate(
            customer_id=self._customer_id(uow, data),
            client_id=client_id,
            items=[
                SaleItemCreate(item_id=line["product_id"], quantity=line["quantity"])
                for line in data.get("items", [])
            ],
            payment_method=data.get("payment_method"),
        )
        sale_id = data.get("id")
        existing = uow.sales.get_by_id(sale_id) if sale_id else None
        if existing is not None and existing.user_id == current_user.id:
            return existing.id, None  # replay under a different op id
        sale, _ = sales_service.build_sale(
            uow,
            sale_data,
            current_user.id,
            allow_negative_stock=True,
            sale_id=sale_id,
        )
        if op is not None:
            # Store the client's op time (naive UTC) so last-write-wins on later
            # updates compares against when the sale happened, not when it synced.
            sale.updated_at = op.updated_at.astimezone(timezone.utc).replace(
                tzinfo=None
            )
        uow.flush()  # surface FK/unique errors inside this op's nested transaction
        return sale.id, None


class ItemSyncHandler(GenericSyncHandler):
    def __init__(self):
        super().__init__(Item, "items")

    def apply_create(
        self,
        uow: AbstractUnitOfWork,
        payload: Any,
        current_user: User,
        op: Optional["SyncOperation"] = None,
    ) -> Tuple[Optional[UUID], Optional[str]]:
        payload_data = (
            payload.model_dump(exclude_unset=True)
            if hasattr(payload, "model_dump")
            else payload
        )
        if not payload_data.get("name") or not payload_data.get("sku"):
            return None, "'name' and 'sku' are required to create an item"
        return super().apply_create(uow, payload, current_user, op)


class AttendanceSyncHandler(GenericSyncHandler):
    def __init__(self):
        super().__init__(Attendance, "attendance")


class RawMaterialSyncHandler(GenericSyncHandler):
    def __init__(self):
        super().__init__(RawMaterial, "raw_materials")

    def apply_create(
        self,
        uow: AbstractUnitOfWork,
        payload: Any,
        current_user: User,
        op: Optional["SyncOperation"] = None,
    ) -> Tuple[Optional[UUID], Optional[str]]:
        payload_data = (
            payload.model_dump(exclude_unset=True)
            if hasattr(payload, "model_dump")
            else payload
        )
        if not payload_data.get("name"):
            return None, "'name' is required to create a raw material"
        return super().apply_create(uow, payload, current_user, op)


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
                    return handler.apply_create(uow, op.payload, current_user, op)
                elif op.action == SyncAction.UPDATE:
                    return handler.apply_update(uow, op.payload, current_user, op)

                elif op.action == SyncAction.DELETE:
                    return handler.apply_delete(uow, op.payload, current_user, op)
                else:
                    return None, f"Invalid sync action: {op.action}"
        except Exception:
            # Log the real exception (with traceback) server-side only. The
            # client-facing/persisted message must stay generic — it can
            # otherwise leak internals like DB constraint or column names
            # (see issue #19).
            logger.exception(
                "Unhandled error applying sync operation "
                "(client_id=%s, entity=%s, action=%s)",
                op.id,
                op.entity,
                op.action,
            )
            return None, "An internal error occurred while processing this operation"


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
            if not err:
                # Reported as its own status so a client can keep the change queued
                # for the right user instead of treating it as a dead operation.
                err = OperationValidator.check_maker(op, current_user)
                if err:
                    aggregator.add_result(op.id, None, "wrong_user", err)
                    continue
                err = OperationValidator.authorize(op, current_user)
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


def pull_sync(
    uow: AbstractUnitOfWork,
    last_sync: datetime,
    current_user: User,
    limit: int = 100,
    offset: int = 0,
) -> SyncPullResponse:
    """
    Retrieve incremental updates from the server since the client's last synchronization.

    Enables clients to update their local database with new or modified records.
    Uses eager loading (joinedload) to prevent N+1 queries for sales items.

    Args:
        uow (AbstractUnitOfWork): Unit of Work.
        last_sync (datetime): Timestamp of the last successful sync.
        current_user (User): Who is pulling; limits what they may read.
        limit (int): Maximum records per entity.
        offset (int): Records to skip.

    Returns:
        SyncPullResponse: Dictionaries of changed items, sales, attendance, and materials.
    """
    # Enforce global max limit for stability
    limit = min(limit, 100)

    role = _role_of(current_user)

    def may_read(entity: str) -> bool:
        return role in PULL_ROLES[entity]

    # helper row to dict
    def to_dict_list(rows):
        res = []
        for r in rows:
            # Basic columns
            d = {c.name: getattr(r, c.name) for c in r.__table__.columns}

            # Handle nested relationships if they exist (e.g. Sale -> items)
            if isinstance(r, Sale):
                d["items"] = []
                for item in r.items:
                    item_dict = {
                        c.name: getattr(item, c.name) for c in item.__table__.columns
                    }
                    d["items"].append(item_dict)

            res.append(d)
        return res

    # Optimization: Use joinedload to fetch sale items in a single hit (Issue #15)
    from sqlalchemy.orm import joinedload

    items = (
        uow.session.query(Item)
        .filter(Item.updated_at > last_sync)
        .order_by(Item.updated_at, Item.id)
        .limit(limit)
        .offset(offset)
        .all()
        if may_read("item")
        else []
    )
    sales = (
        uow.session.query(Sale)
        .options(joinedload(Sale.items))
        .filter(Sale.updated_at > last_sync)
        .order_by(Sale.updated_at, Sale.id)
        .limit(limit)
        .offset(offset)
        .all()
        if may_read("sale")
        else []
    )
    attendance_q = uow.session.query(Attendance).filter(
        Attendance.updated_at > last_sync
    )
    if role not in _SALES_ROLES:
        attendance_q = attendance_q.filter(Attendance.user_id == current_user.id)
    attendance = (
        attendance_q.order_by(Attendance.updated_at, Attendance.id)
        .limit(limit)
        .offset(offset)
        .all()
        if may_read("attendance")
        else []
    )
    raw_materials = (
        uow.session.query(RawMaterial)
        .filter(RawMaterial.updated_at > last_sync)
        .order_by(RawMaterial.updated_at, RawMaterial.id)
        .limit(limit)
        .offset(offset)
        .all()
        if may_read("raw_material")
        else []
    )

    readable = [e for e in PULL_ROLES if may_read(e)]
    deleted = (
        uow.session.query(SyncTombstone)
        .filter(
            SyncTombstone.deleted_at > last_sync,
            SyncTombstone.entity.in_(readable),
        )
        .order_by(SyncTombstone.deleted_at, SyncTombstone.id)
        .limit(limit)
        .offset(offset)
        .all()
    )

    item_dicts = to_dict_list(items)
    if role not in _COST_VIEWERS:
        # what an item cost is for the owner and managers only (as on GET /items)
        for d in item_dicts:
            d["purchase_price"] = Decimal("0.00")
            d["production_cost"] = Decimal("0.00")

    return SyncPullResponse(
        deleted=[
            {
                "entity": t.entity,
                "record_id": str(t.record_id),
                "deleted_at": t.deleted_at.isoformat(),
            }
            for t in deleted
        ],
        items=item_dicts,
        sales=to_dict_list(sales),
        attendance=to_dict_list(attendance),
        raw_materials=to_dict_list(raw_materials),
    )
