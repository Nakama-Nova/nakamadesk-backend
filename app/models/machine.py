import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Machine(Base):
    """
    A machine in the workshop: a fixed asset, not stock. Its cost is written off over
    `useful_life_years` in a straight line down to `salvage_value` (see
    machine_service.depreciation). Machines are retired, never deleted, because their
    maintenance history and cost still count.
    """

    __tablename__ = "machines"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'down', 'retired')", name="ck_machines_status"
        ),
        CheckConstraint("purchase_cost >= 0", name="ck_machines_cost"),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    tag = Column(String, unique=True, nullable=False)  # the sticker on the machine
    name = Column(String, nullable=False)
    make_model = Column(String, nullable=True)
    serial_number = Column(String, nullable=True)
    location_id = Column(
        UUID(as_uuid=True), ForeignKey("plant_units.id"), nullable=True, index=True
    )
    purchase_date = Column(Date, nullable=True)
    purchase_cost = Column(Numeric(12, 2), nullable=True)
    useful_life_years = Column(Integer, nullable=True)
    salvage_value = Column(
        Numeric(12, 2), nullable=False, default=0, server_default="0"
    )
    # Service every N days. Empty = no schedule.
    maintenance_every_days = Column(Integer, nullable=True)
    status = Column(String, nullable=False, default="active", server_default="active")
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now)


class MachineMaintenance(Base):
    """One service or repair. `scheduled` visits reset the next-due date. Never edited."""

    __tablename__ = "machine_maintenance"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('scheduled', 'repair')", name="ck_machine_maint_kind"
        ),
        CheckConstraint("cost >= 0", name="ck_machine_maint_cost"),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    machine_id = Column(
        UUID(as_uuid=True), ForeignKey("machines.id"), nullable=False, index=True
    )
    kind = Column(String, nullable=False)
    performed_on = Column(Date, nullable=False)
    cost = Column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    description = Column(Text, nullable=True)
    done_by = Column(String, nullable=True)  # the mechanic or company
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=_now)


class MachineDowntime(Base):
    """A stretch when the machine was not working. Open while ended_at is empty."""

    __tablename__ = "machine_downtime"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    machine_id = Column(
        UUID(as_uuid=True), ForeignKey("machines.id"), nullable=False, index=True
    )
    started_at = Column(DateTime, nullable=False, default=_now)
    ended_at = Column(DateTime, nullable=True)
    reason = Column(String, nullable=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
