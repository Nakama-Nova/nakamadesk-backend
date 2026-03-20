from sqlalchemy.orm import Session
from datetime import datetime
import uuid
import logging
from typing import List

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
)

# Note: Ideally these handlers would dispatch to specific services (e.g. sales_service.create_sale)
# But for outbox parsing dynamically with LWW conflicts, we construct them inline mapping here to avoid
# circular logic, or call services.


def _get_model_class(entity: str):
    models = {
        "item": Item,
        "sale": Sale,
        "attendance": Attendance,
        "raw_material": RawMaterial,
    }
    return models.get(entity)


def process_push_sync(db: Session, operations: List[SyncOperation]) -> SyncPushResponse:
    success_results = []
    failed_results = []

    for op in operations:
        # 1. Idempotency Check
        existing_log = db.query(SyncLog).filter(SyncLog.client_id == op.id).first()
        if existing_log and existing_log.status == "success":
            success_results.append(
                SyncOperationResult(
                    client_id=op.id, record_id=existing_log.record_id, status="success"
                )
            )
            continue

        model_class = _get_model_class(op.entity)
        if not model_class:
            failed_results.append(
                SyncOperationResult(
                    client_id=op.id,
                    status="failed",
                    error=f"Unknown entity: {op.entity}",
                )
            )
            continue

        record_id = None
        op_failed = False
        op_error = ""

        try:
            with db.begin_nested():
                if op.action == "create":
                    obj_data = op.payload.copy()
                    if "id" in obj_data:
                        record_id = uuid.UUID(obj_data["id"])
                    else:
                        record_id = uuid.uuid4()
                        obj_data["id"] = str(record_id)

                    existing_obj = (
                        db.query(model_class)
                        .filter(model_class.id == record_id)
                        .first()
                    )
                    if not existing_obj:
                        new_obj = model_class(**obj_data)
                        db.add(new_obj)
                        db.flush()
                    else:
                        pass  # Record already exists locally, could be a previous collision. Ignored.

                elif op.action == "update":
                    record_id = uuid.UUID(op.payload["id"])
                    db_obj = (
                        db.query(model_class)
                        .filter(model_class.id == record_id)
                        .first()
                    )
                    if not db_obj:
                        raise ValueError("Record not found for update")

                    # LWW conflict resolution
                    local_op_time = op.updated_at.astimezone(None).replace(tzinfo=None)
                    if db_obj.updated_at and local_op_time < db_obj.updated_at:
                        pass  # Server is newer
                    else:
                        for key, value in op.payload.items():
                            if key == "id":
                                continue
                            if key == "current_stock" and op.entity == "item":
                                db_obj.current_stock = getattr(
                                    db_obj, "current_stock", 0
                                ) + float(value)
                            else:
                                setattr(db_obj, key, value)
                        db.flush()

                elif op.action == "delete":
                    record_id = uuid.UUID(op.payload["id"])
                    db_obj = (
                        db.query(model_class)
                        .filter(model_class.id == record_id)
                        .first()
                    )
                    if db_obj:
                        db.delete(db_obj)
                        db.flush()

        except Exception as e:
            op_failed = True
            op_error = str(e)

        # Logging to SyncLog
        if op_failed:
            logger.warning(
                f"Sync Push Entity collision failed across tracking id {op.id} natively blocked evaluating: {op_error}"
            )
            failed_results.append(
                SyncOperationResult(client_id=op.id, status="failed", error=op_error)
            )
            if not existing_log:
                db.add(
                    SyncLog(
                        client_id=op.id,
                        entity=op.entity,
                        record_id=record_id or uuid.uuid4(),
                        action=op.action,
                        payload=op.payload,
                        status="failed",
                        error_message=op_error,
                    )
                )
            else:
                existing_log.status = "failed"
                existing_log.error_message = op_error
        else:
            success_results.append(
                SyncOperationResult(
                    client_id=op.id, record_id=record_id, status="success"
                )
            )
            if not existing_log:
                db.add(
                    SyncLog(
                        client_id=op.id,
                        entity=op.entity,
                        record_id=record_id,
                        action=op.action,
                        payload=op.payload,
                        status="success",
                    )
                )
            else:
                existing_log.status = "success"
                existing_log.record_id = record_id
                existing_log.error_message = None

    db.commit()
    return SyncPushResponse(success=success_results, failed=failed_results)


def pull_sync(db: Session, last_sync: datetime) -> SyncPullResponse:
    # Ensure timezone naive for SQLAlchemy matching if standard setup,
    # but we will just pass standard datetime

    items = db.query(Item).filter(Item.updated_at > last_sync).all()
    sales = db.query(Sale).filter(Sale.updated_at > last_sync).all()
    attendance = db.query(Attendance).filter(Attendance.updated_at > last_sync).all()
    raw_materials = (
        db.query(RawMaterial).filter(RawMaterial.updated_at > last_sync).all()
    )

    # helper row to dict
    def to_dict_list(rows):
        res = []
        for r in rows:
            d = {c.name: getattr(r, c.name) for c in r.__table__.columns}
            res.append(d)
        return res

    return SyncPullResponse(
        items=to_dict_list(items),
        sales=to_dict_list(sales),
        attendance=to_dict_list(attendance),
        raw_materials=to_dict_list(raw_materials),
    )
